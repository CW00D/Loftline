"""Email and password auth, self-hosted. The users table is the user store.

Tokens are stateless JWTs that last a week. There is no refresh-token table
and the token is never stored in the database.

The endpoints, their bodies and their answers are identical to the graph
branch's: the app and the web front-end are written against this contract,
not against a database.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import emails
from config import (
    RESET_CODE_COOLDOWN_SECONDS,
    RESET_CODE_MAX_ATTEMPTS,
    RESET_CODE_TTL_MINUTES,
)
from db import get_session
from models import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    ResetPasswordRequest,
    SignupRequest,
    TokenResponse,
    UpdateMeRequest,
    UserResponse,
)
from security import (
    create_reset_code,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from tables import User

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        handle=user.handle,
        name=user.name,
        created_at=user.created_at.isoformat(),
    )


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        user_id = decode_token(credentials.credentials)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from None
    with get_session() as session:
        user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    # Detached from its session, which is what a request wants: a plain
    # record of who is calling. Endpoints that write re-fetch by id.
    return user


def _taken_field(error: IntegrityError) -> str:
    return "handle" if "uq_users_handle" in str(error.orig) else "email"


@router.post("/signup", response_model=TokenResponse)
def signup(body: SignupRequest):
    user = User(
        id=str(uuid.uuid4()),
        email=body.email.lower(),
        password_hash=hash_password(body.password),
        handle=body.handle,
        name=body.name,
    )
    try:
        with get_session() as session:
            session.add(user)
            session.commit()
    except IntegrityError as e:
        raise HTTPException(
            status_code=409, detail=f"That {_taken_field(e)} is already taken"
        ) from e
    return TokenResponse(token=create_token(user.id))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    with get_session() as session:
        user = session.scalar(select(User).where(User.email == body.email.lower()))
    # Same 401 whether the email is unknown or the password is wrong.
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(token=create_token(user.id))


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(current_user)):
    return _user_response(user)


@router.patch("/me", response_model=UserResponse)
def update_me(body: UpdateMeRequest, user: User = Depends(current_user)):
    fields = body.model_dump(exclude_none=True)
    if not fields:
        return _user_response(user)
    if "email" in fields:
        fields["email"] = fields["email"].lower()
    try:
        with get_session() as session:
            current = session.get(User, user.id)
            for name, value in fields.items():
                setattr(current, name, value)
            session.commit()
            session.refresh(current)
            return _user_response(current)
    except IntegrityError as e:
        raise HTTPException(
            status_code=409, detail=f"That {_taken_field(e)} is already taken"
        ) from e


@router.post("/me/password")
def change_password(body: ChangePasswordRequest, user: User = Depends(current_user)):
    # 400, not 401. The bearer token authenticated perfectly well; it is a
    # field in the body that is wrong, exactly as when /reset-password is
    # given a bad code. A client that treats 401 on an authenticated call as
    # "this session is over" must not be signed out by a mistyped password.
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    with get_session() as session:
        session.get(User, user.id).password_hash = hash_password(body.new_password)
        session.commit()
    return {"ok": True}


# Every way of failing /reset-password says exactly this. Which of them
# happened, no such account, wrong code, expired, guessed at too often, is
# precisely what an attacker would like to know.
BAD_CODE_DETAIL = "That code is wrong or has expired. Request a new one."


def _clear_reset(user: User) -> None:
    user.reset_code_hash = None
    user.reset_expires_at = None
    user.reset_attempts = 0
    user.reset_sent_at = None


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest):
    """Mail a reset code to the address, if it belongs to an account.

    Unauthenticated, so the answer is the same either way: a response that
    differed would turn this into a way of testing whether an address has an
    account. Nothing here tells the caller anything it did not already know.
    """
    email = body.email.lower()
    now = _now()
    with get_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        recent = user is not None and (
            user.reset_sent_at is not None
            and user.reset_sent_at > now - timedelta(seconds=RESET_CODE_COOLDOWN_SECONDS)
        )
        # No account, or a second mail inside the cooldown. Both are silent
        # by design.
        if user is None or recent:
            return {"ok": True}
        code = create_reset_code()
        user.reset_code_hash = hash_password(code)
        user.reset_expires_at = now + timedelta(minutes=RESET_CODE_TTL_MINUTES)
        user.reset_attempts = 0
        user.reset_sent_at = now
        session.commit()
        name = user.name
    # Outside the session: the write is what matters and it is done before
    # anything leaves the building.
    emails.send_password_reset(
        to=email, name=name, code=code, ttl_minutes=RESET_CODE_TTL_MINUTES
    )
    return {"ok": True}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest):
    """Spend a code from /forgot-password on a new password.

    The code is held as an argon2 hash, like the password itself, so a dump of
    the table is not a pile of live account-takeover tokens.
    """
    email = body.email.lower()
    now = _now()
    with get_session() as session:
        user = session.scalar(select(User).where(User.email == email))
        live = (
            user is not None
            and user.reset_code_hash is not None
            and user.reset_expires_at is not None
            and user.reset_expires_at > now
        )
        if not live:
            raise HTTPException(status_code=400, detail=BAD_CODE_DETAIL)
        if user.reset_attempts >= RESET_CODE_MAX_ATTEMPTS:
            _clear_reset(user)
            session.commit()
            raise HTTPException(status_code=400, detail=BAD_CODE_DETAIL)
        if not verify_password(user.reset_code_hash, body.code):
            user.reset_attempts += 1
            session.commit()
            raise HTTPException(status_code=400, detail=BAD_CODE_DETAIL)
        # One code, one password. Clearing it here is what stops the same
        # email being replayed for as long as the hour lasts.
        user.password_hash = hash_password(body.new_password)
        _clear_reset(user)
        session.commit()
    return {"ok": True}


@router.delete("/me")
def delete_me(user: User = Depends(current_user)):
    with get_session() as session:
        session.delete(session.get(User, user.id))
        session.commit()
    return {"ok": True}
