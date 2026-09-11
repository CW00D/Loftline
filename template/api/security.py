"""Password hashing and JWT encode/decode.

seed_dev.py imports hash_password from here, so signup and the seed produce
interchangeable hashes.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

JWT_ALGORITHM = "HS256"
JWT_EXPIRY = timedelta(days=7)

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_reset_code() -> str:
    """A six-digit password reset code, short enough to retype off an email.

    Six digits is only a million guesses, so what makes it safe is the attempt
    cap and the expiry on the endpoint that checks it, not the code itself.
    Stored as an argon2 hash like a password; see auth.py.
    """
    return f"{secrets.randbelow(1_000_000):06d}"


# RFC 7518 section 3.2: an HMAC-SHA256 key must be at least 32 bytes. A
# shorter one is refused rather than warned about, because the warning would
# scroll past once at boot and the tokens would be weak for the life of the
# deployment. Loftline generates 32 random bytes, base64 encoded, which is 43
# characters.
JWT_SECRET_MIN_LENGTH = 32


def _secret() -> str:
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")
    if len(secret.encode()) < JWT_SECRET_MIN_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET is {len(secret.encode())} bytes; it must be at least "
            f"{JWT_SECRET_MIN_LENGTH}. Generate one with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return secret


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + JWT_EXPIRY}
    return jwt.encode(payload, _secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> str:
    """Return the user id from a valid token, or raise jwt.InvalidTokenError."""
    payload = jwt.decode(token, _secret(), algorithms=[JWT_ALGORITHM])
    user_id = payload.get("sub")
    if not user_id:
        raise jwt.InvalidTokenError("missing sub claim")
    return user_id
