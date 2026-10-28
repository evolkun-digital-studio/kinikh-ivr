"""Production preflight rejects unsafe settings before a deployment changes data."""

from app.core.config import Settings
from scripts.check_production import configuration_errors


def valid_settings(**overrides):
    data = {
        "app_env": "production",
        "app_debug": False,
        "database_url": "postgresql+asyncpg://kinikh:" + "a" * 64 + "@db/kinikh_ivr",
        "secret_key": "a" * 64,
        "jwt_secret_key": "b" * 64,
        "exotel_webhook_secret": "c" * 64,
        "public_base_url": "https://ivr.company.com",
        "cors_origins": ["https://ivr.company.com"],
        "openai_api_key": "sk-production-key",
        "exotel_sid": "account",
        "exotel_api_key": "api-key",
        "exotel_token": "token",
        "exotel_caller_id": "+911234567890",
        "smtp_username": "smtp-user",
        "smtp_password": "smtp-password",
        "smtp_from_email": "notify@company.com",
        "admin_email": "ops@company.com",
        "admin_password_hash": "$2b$12$" + "x" * 53,
        "tts_provider": "openai",
        "transfer_enabled": False,
    }
    data.update(overrides)
    return Settings(_env_file=None, **data)


def test_valid_production_configuration():
    assert configuration_errors(valid_settings()) == []


def test_debug_mode_and_reused_secrets_rejected():
    errors = configuration_errors(valid_settings(app_debug=True, jwt_secret_key="a" * 64))
    assert any("APP_DEBUG" in error for error in errors)
    assert any("independent" in error for error in errors)


def test_insecure_public_origin_and_wildcard_cors_rejected():
    errors = configuration_errors(
        valid_settings(public_base_url="http://localhost", cors_origins=["*"])
    )
    assert any("PUBLIC_BASE_URL" in error for error in errors)
    assert any("CORS_ORIGINS" in error for error in errors)


def test_placeholder_provider_and_bad_admin_hash_rejected():
    errors = configuration_errors(
        valid_settings(openai_api_key="CHANGE_ME", admin_password_hash="plaintext")
    )
    assert any("OPENAI_API_KEY" in error for error in errors)
    assert any("ADMIN_PASSWORD_HASH" in error for error in errors)


def test_elevenlabs_requires_voices_and_credentials():
    errors = configuration_errors(
        valid_settings(
            tts_provider="elevenlabs",
            elevenlabs_api_key="",
            elevenlabs_voice_id_en="",
            elevenlabs_voice_id_hi="",
        )
    )
    assert any("ELEVENLABS_API_KEY" in error for error in errors)
    assert any("ELEVENLABS_VOICE_ID_HI" in error for error in errors)


def test_weak_database_password_rejected():
    errors = configuration_errors(
        valid_settings(database_url="postgresql+asyncpg://kinikh:password@db/kinikh_ivr")
    )
    assert any("DB_PASSWORD" in error for error in errors)


def test_asymmetric_jwt_algorithm_rejected_for_shared_secret():
    errors = configuration_errors(valid_settings(jwt_algorithm="RS256"))
    assert any("JWT_ALGORITHM" in error for error in errors)
