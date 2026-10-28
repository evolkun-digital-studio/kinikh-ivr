"""Fail deployment before migrations when production configuration is unsafe."""

import re
from urllib.parse import urlparse

from pydantic import ValidationError

from app.core.config import Settings, _DEV_MOCK_DEFAULTS


def configuration_errors(settings: Settings) -> list[str]:
    errors = []
    if not settings.is_production or settings.app_debug:
        errors.append("APP_ENV must be production and APP_DEBUG must be false")
    if settings.jwt_algorithm != "HS256":
        errors.append("JWT_ALGORITHM must be HS256 for this shared-secret deployment")
    required = (
        "secret_key",
        "jwt_secret_key",
        "exotel_webhook_secret",
        "openai_api_key",
        "exotel_sid",
        "exotel_api_key",
        "exotel_token",
        "exotel_caller_id",
        "smtp_username",
        "smtp_password",
        "smtp_from_email",
        "admin_email",
        "admin_password_hash",
    )
    if settings.tts_provider == "elevenlabs":
        required += ("elevenlabs_api_key", "elevenlabs_voice_id_en", "elevenlabs_voice_id_hi")
    for field in required:
        value = str(getattr(settings, field)).strip()
        if (
            not value
            or value == _DEV_MOCK_DEFAULTS.get(field)
            or any(
                word in value.lower() for word in ("change_me", "replace_me", "your-", "sk-test")
            )
        ):
            errors.append(f"{field.upper()} must contain a real production value")
    for field in ("secret_key", "jwt_secret_key", "exotel_webhook_secret"):
        if len(str(getattr(settings, field))) < 32:
            errors.append(f"{field.upper()} must contain at least 32 random characters")
    db_password = urlparse(settings.database_url).password or ""
    if not re.fullmatch(r"[0-9a-fA-F]{32,}", db_password):
        errors.append(
            "DB_PASSWORD must contain at least 32 hexadecimal characters for this Compose deployment"
        )
    if settings.secret_key == settings.jwt_secret_key:
        errors.append("SECRET_KEY and JWT_SECRET_KEY must be independent secrets")
    if (
        not settings.admin_password_hash.startswith(("$2a$", "$2b$", "$2y$"))
        or len(settings.admin_password_hash) != 60
    ):
        errors.append("ADMIN_PASSWORD_HASH must be a bcrypt hash")
    public = urlparse(settings.public_base_url)
    if (
        public.scheme != "https"
        or not public.hostname
        or public.hostname in ("localhost", "127.0.0.1", "ivr.example.com")
        or public.path not in ("", "/")
        or public.query
        or public.fragment
        or public.username
    ):
        errors.append("PUBLIC_BASE_URL must be a public HTTPS origin without a path")
    if not settings.cors_origins or any(
        origin != settings.public_base_url.rstrip("/") for origin in settings.cors_origins
    ):
        errors.append(
            "CORS_ORIGINS must contain only PUBLIC_BASE_URL for the bundled same-origin deployment"
        )
    if settings.transfer_enabled and not settings.transfer_numbers:
        errors.append("Configure TRANSFER_NUMBERS or disable TRANSFER_ENABLED")
    return errors


if __name__ == "__main__":
    try:
        settings = Settings()
    except ValidationError as exc:
        # Pydantic's normal exception rendering can include the input environment.
        fields = [".".join(map(str, item["loc"])) for item in exc.errors(include_input=False)]
        raise SystemExit("Invalid or missing production settings: " + ", ".join(fields)) from None
    problems = configuration_errors(settings)
    if problems:
        raise SystemExit("Production preflight failed:\n- " + "\n- ".join(problems))
    print(
        "Production configuration preflight passed (provider credentials still need live verification)"
    )
