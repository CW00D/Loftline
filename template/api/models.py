import email_validator
from pydantic import BaseModel, EmailStr, Field

# Dev and test users live at @skeleton.test; without this the validator
# rejects reserved TLDs like .test outright.
email_validator.TEST_ENVIRONMENT = True


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    handle: str = Field(min_length=2, max_length=30, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str


class UserResponse(BaseModel):
    id: str
    email: str
    handle: str
    name: str
    created_at: str


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    handle: str | None = Field(
        default=None, min_length=2, max_length=30, pattern=r"^[a-z0-9_]+$"
    )
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    # Shape-checked here so a malformed code 422s without touching the graph.
    # That leaks nothing: it depends on the code alone, never on the account.
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8)
