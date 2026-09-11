"""Email and password auth, self-hosted. The graph is the user store.

Tokens are stateless JWTs that last a week. There is no refresh-token table
and the token is never stored in the graph.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from neo4j.exceptions import ConstraintError
from neo4j.time import DateTime

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

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


def _user_response(node) -> UserResponse:
    created = node["created_at"]
    if isinstance(created, DateTime):
        created = created.to_native().isoformat()
    return UserResponse(
        id=node["id"],
        email=node["email"],
        handle=node["handle"],
        name=node["name"],
        created_at=str(created),
    )


def current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        user_id = decode_token(credentials.credentials)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token") from None
    with get_session() as session:
        record = session.run(
            "MATCH (u:User {id: $id}) RETURN u", id=user_id
        ).single()
    if record is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return record["u"]


CREATE_USER = """
CREATE (u:User {
    id: $id,
    email: $email,
    password_hash: $password_hash,
    handle: $handle,
    name: $name,
    created_at: datetime()
})
"""


def _taken_field(error: ConstraintError) -> str:
    return "handle" if "handle" in str(error) else "email"


@router.post("/signup", response_model=TokenResponse)
def signup(body: SignupRequest):
    user_id = str(uuid.uuid4())
    try:
        with get_session() as session:
            session.run(
                CREATE_USER,
                id=user_id,
                email=body.email.lower(),
                password_hash=hash_password(body.password),
                handle=body.handle,
                name=body.name,
            ).consume()
    except ConstraintError as e:
        raise HTTPException(
            status_code=409, detail=f"That {_taken_field(e)} is already taken"
        ) from e
    return TokenResponse(token=create_token(user_id))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    with get_session() as session:
        record = session.run(
            "MATCH (u:User {email: $email}) "
            "RETURN u.id AS id, u.password_hash AS hash",
            email=body.email.lower(),
        ).single()
    # Same 401 whether the email is unknown or the password is wrong.
    if (
        record is None
        or not record["hash"]
        or not verify_password(record["hash"], body.password)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return TokenResponse(token=create_token(record["id"]))


@router.get("/me", response_model=UserResponse)
def me(user=Depends(current_user)):
    return _user_response(user)


@router.patch("/me", response_model=UserResponse)
def update_me(body: UpdateMeRequest, user=Depends(current_user)):
    props = body.model_dump(exclude_none=True)
    if not props:
        return _user_response(user)
    if "email" in props:
        props["email"] = props["email"].lower()
    try:
        with get_session() as session:
            record = session.run(
                "MATCH (u:User {id: $id}) SET u += $props RETURN u",
                id=user["id"],
                props=props,
            ).single()
    except ConstraintError as e:
        raise HTTPException(
            status_code=409, detail=f"That {_taken_field(e)} is already taken"
        ) from e
    return _user_response(record["u"])


@router.post("/me/password")
def change_password(body: ChangePasswordRequest, user=Depends(current_user)):
    # 400, not 401. The bearer token authenticated perfectly well; it is a
    # field in the body that is wrong, exactly as when /reset-password is
    # given a bad code. A client that treats 401 on an authenticated call as
    # "this session is over" must not be signed out by a mistyped password.
    if not verify_password(user["password_hash"], body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    with get_session() as session:
        session.run(
            "MATCH (u:User {id: $id}) SET u.password_hash = $hash",
            id=user["id"],
            hash=hash_password(body.new_password),
        ).consume()
    return {"ok": True}


# Every way of failing /reset-password says exactly this. Which of them
# happened, no such account, wrong code, expired, guessed at too often, is
# precisely what an attacker would like to know.
BAD_CODE_DETAIL = "That code is wrong or has expired. Request a new one."


def _clear_reset(session, user_id: str) -> None:
    session.run(
        "MATCH (u:User {id: $id}) "
        "REMOVE u.reset_code_hash, u.reset_expires_at, "
        "       u.reset_attempts, u.reset_sent_at",
        id=user_id,
    ).consume()


@router.post("/forgot-password")
def forgot_password(body: ForgotPasswordRequest):
    """Mail a reset code to the address, if it belongs to an account.

    Unauthenticated, so the answer is the same either way: a response that
    differed would turn this into a way of testing whether an address has an
    account. Nothing here tells the caller anything it did not already know.
    """
    email = body.email.lower()
    with get_session() as session:
        record = session.run(
            "MATCH (u:User {email: $email}) "
            "RETURN u.id AS id, u.name AS name, "
            "u.reset_sent_at > datetime() - duration({seconds: $cooldown}) AS recent",
            email=email,
            cooldown=RESET_CODE_COOLDOWN_SECONDS,
        ).single()
        # No account, or a second mail inside the cooldown. Both are silent
        # by design.
        if record is None or record["recent"]:
            return {"ok": True}
        code = create_reset_code()
        session.run(
            "MATCH (u:User {id: $id}) "
            "SET u.reset_code_hash = $hash, "
            "    u.reset_expires_at = datetime() + duration({minutes: $ttl}), "
            "    u.reset_attempts = 0, "
            "    u.reset_sent_at = datetime()",
            id=record["id"],
            hash=hash_password(code),
            ttl=RESET_CODE_TTL_MINUTES,
        ).consume()
    # Outside the session: the write is what matters and it is done before
    # anything leaves the building.
    emails.send_password_reset(
        to=email, name=record["name"], code=code, ttl_minutes=RESET_CODE_TTL_MINUTES
    )
    return {"ok": True}


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest):
    """Spend a code from /forgot-password on a new password.

    The code is held as an argon2 hash, like the password itself, so a dump of
    the graph is not a pile of live account-takeover tokens.
    """
    email = body.email.lower()
    with get_session() as session:
        record = session.run(
            "MATCH (u:User {email: $email}) "
            "RETURN u.id AS id, u.reset_code_hash AS hash, "
            "coalesce(u.reset_attempts, 0) AS attempts, "
            "u.reset_expires_at > datetime() AS live",
            email=email,
        ).single()
        if record is None or not record["hash"] or not record["live"]:
            raise HTTPException(status_code=400, detail=BAD_CODE_DETAIL)
        if record["attempts"] >= RESET_CODE_MAX_ATTEMPTS:
            _clear_reset(session, record["id"])
            raise HTTPException(status_code=400, detail=BAD_CODE_DETAIL)
        if not verify_password(record["hash"], body.code):
            session.run(
                "MATCH (u:User {id: $id}) "
                "SET u.reset_attempts = coalesce(u.reset_attempts, 0) + 1",
                id=record["id"],
            ).consume()
            raise HTTPException(status_code=400, detail=BAD_CODE_DETAIL)
        # One code, one password. Clearing it here is what stops the same
        # email being replayed for as long as the hour lasts.
        session.run(
            "MATCH (u:User {id: $id}) "
            "SET u.password_hash = $hash "
            "REMOVE u.reset_code_hash, u.reset_expires_at, "
            "       u.reset_attempts, u.reset_sent_at",
            id=record["id"],
            hash=hash_password(body.new_password),
        ).consume()
    return {"ok": True}


@router.delete("/me")
def delete_me(user=Depends(current_user)):
    with get_session() as session:
        session.run(
            "MATCH (u:User {id: $id}) DETACH DELETE u", id=user["id"]
        ).consume()
    return {"ok": True}
