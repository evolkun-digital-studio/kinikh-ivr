"""JWT authentication routes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    settings = get_settings()
    if body.email != settings.admin_email or not verify_password(
        body.password, settings.admin_password_hash
    ):
        raise AuthenticationError("Invalid credentials")
    return TokenResponse(
        access_token=create_access_token(body.email),
        refresh_token=create_refresh_token(body.email),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest) -> TokenResponse:
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise AuthenticationError("Not a refresh token")
    subject = str(payload["sub"])
    return TokenResponse(
        access_token=create_access_token(subject),
        refresh_token=create_refresh_token(subject),
    )
