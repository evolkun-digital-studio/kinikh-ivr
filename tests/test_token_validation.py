"""JWT migration preserves signature, expiry and algorithm enforcement."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
from app.core.security import decode_token


@pytest.mark.parametrize("case", ["expired", "wrong-key", "wrong-algorithm", "unsigned"])
def test_rejects_invalid_jwt(case, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "jwt_secret_key", "k" * 64)
    payload = {
        "sub": "operator@company.com",
        "type": "access",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    key = settings.jwt_secret_key
    algorithm = "HS256"
    if case == "expired":
        payload["exp"] = datetime.now(UTC) - timedelta(minutes=5)
    elif case == "wrong-key":
        key = "different-secret-" + "x" * 64
    elif case == "wrong-algorithm":
        algorithm = "HS512"
    elif case == "unsigned":
        key, algorithm = None, "none"
    token = jwt.encode(payload, key, algorithm=algorithm)
    with pytest.raises(AuthenticationError):
        decode_token(token)
