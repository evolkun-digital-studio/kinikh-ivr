"""Auth/security behavioral tests.

Covers app/core/security.py (password hashing, JWT) and the /auth routes,
which previously had no behavioral test coverage — only "router imports"
smoke tests (see test_api.py). Written during the pre-API production audit
after finding two real bugs here (see docs/pre_api_audit.md):
  - passlib+bcrypt>=4.1 incompatibility made hash_password()/verify_password()
    crash on every call;
  - a refresh token could be used directly as a Bearer access token because
    get_current_user_id() never checked the `type` claim.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    extract_subject,
    hash_password,
    verify_password,
)
from app.main import app


class TestPasswordHashing:
    def test_hash_and_verify_round_trip(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed)

    def test_verify_rejects_wrong_password(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert not verify_password("wrong-password", hashed)

    def test_hash_is_not_the_plaintext(self):
        hashed = hash_password("secret")
        assert hashed != "secret"
        assert hashed.startswith("$2b$")


class TestJWTTokens:
    def test_access_token_round_trips_subject(self):
        token = create_access_token("user@test.com")
        payload = decode_token(token)
        assert payload["sub"] == "user@test.com"
        assert payload["type"] == "access"

    def test_refresh_token_has_refresh_type(self):
        token = create_refresh_token("user@test.com")
        payload = decode_token(token)
        assert payload["type"] == "refresh"

    def test_extract_subject_returns_sub_claim(self):
        token = create_access_token("user@test.com")
        assert extract_subject(token) == "user@test.com"

    def test_decode_invalid_token_raises_authentication_error(self):
        from app.core.exceptions import AuthenticationError

        with pytest.raises(AuthenticationError):
            decode_token("not-a-real-token")

    def test_access_token_carries_extra_claims(self):
        token = create_access_token("user@test.com", extra={"role": "admin"})
        payload = decode_token(token)
        assert payload["role"] == "admin"


class TestGetCurrentUserId:
    def test_access_token_is_accepted(self):
        from app.core.dependencies import get_current_user_id

        token = create_access_token("user@test.com")
        user_id = asyncio.run(get_current_user_id(f"Bearer {token}"))
        assert user_id == "user@test.com"

    def test_refresh_token_is_rejected_as_access_token(self):
        """Regression test: a refresh token must not work as a Bearer access token."""
        from app.core.dependencies import get_current_user_id

        token = create_refresh_token("user@test.com")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_current_user_id(f"Bearer {token}"))
        assert exc_info.value.status_code == 401

    def test_missing_bearer_prefix_is_rejected(self):
        from app.core.dependencies import get_current_user_id

        token = create_access_token("user@test.com")
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_current_user_id(token))  # no "Bearer " prefix
        assert exc_info.value.status_code == 401


class TestAuthRoutes:
    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(app, raise_server_exceptions=False)

    def test_login_with_correct_admin_credentials_succeeds(self, client):
        settings = get_settings()
        response = client.post(
            "/api/v1/auth/login",
            json={"email": settings.admin_email, "password": "dev-only-changeme"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert decode_token(body["access_token"])["type"] == "access"
        assert decode_token(body["refresh_token"])["type"] == "refresh"

    def test_login_with_wrong_password_is_rejected(self, client):
        settings = get_settings()
        response = client.post(
            "/api/v1/auth/login",
            json={"email": settings.admin_email, "password": "wrong-password"},
        )
        assert response.status_code == 401

    def test_login_with_unknown_email_is_rejected(self, client):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.com", "password": "dev-only-changeme"},
        )
        assert response.status_code == 401

    def test_refresh_with_access_token_is_rejected(self, client):
        """A non-refresh token must not be usable at the /refresh endpoint."""
        access_token = create_access_token("user@test.com")
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": access_token})
        assert response.status_code == 401

    def test_refresh_with_valid_refresh_token_issues_new_tokens(self, client):
        refresh_token = create_refresh_token("user@test.com")
        response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert response.status_code == 200
        body = response.json()
        assert decode_token(body["access_token"])["sub"] == "user@test.com"
