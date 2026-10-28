"""Live call transfer: [TRANSFER:...] marker, Exotel Passthru/Connect
endpoints, office-hours availability and number resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi.testclient import TestClient
import pytest

from app.ai.context_manager import CallContext
from app.core.config import get_settings
from app.core.dependencies import get_current_user_id, get_db
from app.main import app
from app.models.call import Call
from app.services.call_service import CallService
from app.telephony import transfer
from app.services.call_service import _TRANSFER_FALLBACK

@pytest.fixture(autouse=True)
def clear_transfer_fallback():
    _TRANSFER_FALLBACK.clear()
    yield
    _TRANSFER_FALLBACK.clear()

from app.telephony.transfer import (
    TRANSFER_TIMEZONE,
    is_transfer_available,
    normalize_department,
    parse_transfer_hours,
    parse_transfer_numbers,
    resolve_transfer_number,
)
from tests.test_voice_duplex import _ai, _handler, _utterance, _voice, _wait_for

# Thursday 1 Oct 2026, India time.
_IN_HOURS = datetime(2026, 10, 1, 11, 0, tzinfo=TRANSFER_TIMEZONE)
_AFTER_HOURS = datetime(2026, 10, 1, 20, 30, tzinfo=TRANSFER_TIMEZONE)
_SUNDAY = datetime(2026, 10, 4, 11, 0, tzinfo=TRANSFER_TIMEZONE)


def _frozen_now(moment: datetime):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment

    return patch.object(transfer, "datetime", _Frozen)


# ── Settings parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("general manager se baat karni hai", "support"),
        ("manager", "support"),
        ("management se baat", "support"),
        ("meeting with management", "ea"),
        ("founder se milna hai", "ea"),
    ],
)
def test_management_route_normalization(utterance, expected):
    assert normalize_department(utterance) == expected


def test_transfer_numbers_parsing():
    assert parse_transfer_numbers(
        "infra=+911111111111|+912222222222; stay = +913333333333;broken; =+914"
    ) == {"infra": ["+911111111111", "+912222222222"], "stay": ["+913333333333"]}
    assert parse_transfer_numbers("") == {}


def test_resolve_transfer_number_uses_department_else_default():
    settings = get_settings()
    with (
        patch.object(settings, "transfer_numbers", "infra=+911111111111; support=+919999999999"),
        patch.object(settings, "transfer_default_number", "+919761615375"),
    ):
        assert resolve_transfer_number("infra") == ["+911111111111", "+919999999999"]
        assert resolve_transfer_number("stay") == ["+919761615375", "+919999999999"]
        assert resolve_transfer_number("support") == ["+919999999999"]
        assert resolve_transfer_number(None) == ["+919999999999"]


def test_default_transfer_number_is_support():
    settings = get_settings()
    assert settings.transfer_default_number == "+918958815375"
    assert parse_transfer_numbers(settings.transfer_numbers)["support"] == [
        settings.transfer_default_number
    ]


def test_transfer_hours_parsing():
    (window,) = parse_transfer_hours("Mon-Sat 10:00-19:00")
    assert window.days == frozenset(range(6))
    weekdays, saturday = parse_transfer_hours("Mon-Fri 10:00-19:00, Sat 10:00-14:00")
    assert weekdays.days == frozenset(range(5)) and saturday.days == {5}
    assert parse_transfer_hours("Mon,Wed 09:30-12:00")[0].days == {0, 2}
    for bad in ("", "Someday 10:00-19:00", "Mon-Sat 19:00-10:00", "Mon-Sat"):
        with pytest.raises(ValueError):
            parse_transfer_hours(bad)


def test_transfer_availability_follows_hours_and_switch():
    settings = get_settings()
    with patch.object(settings, "transfer_hours", "Mon-Sat 10:00-19:00"):
        assert is_transfer_available(_IN_HOURS)
        assert not is_transfer_available(_AFTER_HOURS)
        assert not is_transfer_available(_SUNDAY)
        # 05:30 UTC is 11:00 in India.
        assert is_transfer_available(datetime(2026, 10, 1, 5, 30, tzinfo=UTC))
        with patch.object(settings, "transfer_enabled", False):
            assert not is_transfer_available(_IN_HOURS)
    with patch.object(settings, "transfer_hours", "nonsense"):
        assert not is_transfer_available(_IN_HOURS)


# ── Prompt ───────────────────────────────────────────────────────────────────


async def _system_prompt() -> str:
    from app.ai.conversation import ConversationEngine

    engine = ConversationEngine([{"name": "rural"}], client=AsyncMock())
    return await engine._build_system_prompt(CallContext(call_sid="CA1", caller_number="+91"))


@pytest.mark.asyncio
async def test_prompt_outside_transfer_hours_says_not_available():
    settings = get_settings()
    with patch.object(settings, "transfer_hours", "Mon-Sat 10:00-19:00"), _frozen_now(_AFTER_HOURS):
        prompt = await _system_prompt()
    assert "Talking to a person is not available right now (outside office hours)" in prompt
    assert "[TRANSFER:" not in prompt


@pytest.mark.asyncio
async def test_prompt_inside_transfer_hours_offers_transfer():
    settings = get_settings()
    with patch.object(settings, "transfer_hours", "Mon-Sat 10:00-19:00"), _frozen_now(_IN_HOURS):
        prompt = await _system_prompt()
    assert "[TRANSFER:<key>]" in prompt
    assert "not available right now" not in prompt
    assert "Do not claim to transfer the call" not in prompt


# ── Live call ────────────────────────────────────────────────────────────────


async def _transfer_call(mock_db_session, mark_transfer: AsyncMock):
    settings = get_settings()
    voice = _voice("I want to talk to the infra team please.")
    ai = _ai("Ek moment, main aapko hamari team se connect kar rahi hoon. [TRANSFER:infra]")
    handler, _, _ = await _handler(voice, ai, mock_db_session)
    hangup = AsyncMock()
    handler.set_hangup(hangup)
    with (
        patch.object(settings, "transfer_numbers", "infra=+911111111111"),
        patch.object(CallService, "mark_transfer", mark_transfer),
        patch("app.voice.speech_handler.log_call_event") as log_event,
    ):
        await handler.handle_audio(_utterance())
        await _wait_for(lambda: hangup.await_count == 1, timeout=4)
    return handler, voice, hangup, log_event


@pytest.mark.asyncio
async def test_transfer_marker_is_stripped_and_saved_then_call_ends(mock_db_session):
    mark_transfer = AsyncMock(return_value=SimpleNamespace())
    handler, voice, hangup, log_event = await _transfer_call(mock_db_session, mark_transfer)
    ctx = handler._context

    spoken = [c.args[0] for c in voice.synthesize.await_args_list]
    assert spoken == ["Ek moment, main aapko hamari team se connect kar rahi hoon."]
    assert not any("TRANSFER" in s.upper() for s in spoken)
    assert ctx.transfer_department == "infra"
    assert ctx.ended is True
    # The default number will be appended by resolve_transfer_number because infra != support
    # We patched infra=+911111111111. And default is fetched from settings.
    expected_support = get_settings().transfer_default_number
    mark_transfer.assert_awaited_once_with(ctx.call_sid, "infra", f"+911111111111,{expected_support}")
    mock_db_session.commit.assert_awaited()
    hangup.assert_awaited_once()

    events = {c.args[0]: c.kwargs for c in log_event.call_args_list}
    assert events["call.transfer.requested"]["department"] == "infra"
    assert events["call.transfer.requested"]["number"] != f"+911111111111,{expected_support}"  # masked
    assert events["call.ending"]["reason"] == "transfer"
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_transfer_save_failure_never_crashes_the_call(mock_db_session):
    mark_transfer = AsyncMock(side_effect=RuntimeError("db down"))
    handler, _, hangup, _ = await _transfer_call(mock_db_session, mark_transfer)

    hangup.assert_awaited_once()  # the stream still closes normally
    mock_db_session.rollback.assert_awaited()
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_bare_transfer_marker_means_general(mock_db_session):
    voice = _voice("Can I speak to someone?")
    ai = _ai("Please hold, I'm connecting you to our team. [transfer]")
    handler, _, _ = await _handler(voice, ai, mock_db_session)
    hangup = AsyncMock()
    handler.set_hangup(hangup)
    mark_transfer = AsyncMock(return_value=SimpleNamespace())
    with (
        patch.object(CallService, "mark_transfer", mark_transfer),
        patch.object(get_settings(), "transfer_numbers", ""),
    ):
        await handler.handle_audio(_utterance())
        await _wait_for(lambda: hangup.await_count == 1, timeout=4)

    assert handler._context.transfer_department == "support"
    assert mark_transfer.await_args.args[1:] == ("support", get_settings().transfer_default_number)
    await handler.mark_stopped()


@pytest.mark.asyncio
async def test_mark_transfer_saves_transfer_requested(mock_db_session):
    call = SimpleNamespace(call_sid="CA1")
    service = CallService(mock_db_session)
    with (
        patch.object(service._repo, "get_by_sid_for_update", AsyncMock(return_value=call)),
        patch.object(service._repo, "update", AsyncMock(return_value=call)) as update,
    ):
        await service.mark_transfer("CA1", "infra", "+911111111111")

    update.assert_awaited_once_with(
        call,
        transfer_requested=True,
        transfer_department="infra",
        transfer_number="+911111111111",
    )


def test_call_model_transfer_columns():
    columns = Call.__table__.columns
    assert columns["transfer_requested"].nullable is False
    assert columns["transfer_requested"].default.arg is False
    assert columns["transfer_department"].nullable is True
    assert columns["transfer_number"].nullable is True


# ── Exotel endpoints ─────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    from app.services.call_service import _TRANSFER_FALLBACK
    _TRANSFER_FALLBACK.clear()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    _TRANSFER_FALLBACK.clear()


def _url(path: str, **params: str) -> str:
    query = {"webhook_token": get_settings().exotel_webhook_secret, **params}
    return f"/api/v1/telephony/{path}?" + "&".join(f"{k}={v}" for k, v in query.items())


def _call(**fields) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            "transfer_requested": False,
            "transfer_department": None,
            "transfer_number": None,
            **fields,
        }
    )


def test_transfer_check_marked_call_returns_200(client):
    with patch.object(
        CallService, "get_by_sid", AsyncMock(return_value=_call(transfer_requested=True))
    ) as get:
        response = client.get(_url("transfer-check", CallSid="CA1"))
    assert response.status_code == 200
    assert response.json() == {"transfer": True}
    get.assert_awaited_once_with("CA1")


def test_transfer_check_accepts_post_form(client):
    with patch.object(
        CallService, "get_by_sid", AsyncMock(return_value=_call(transfer_requested=True))
    ):
        response = client.post(_url("transfer-check"), data={"CallSid": "CA1"})
    assert response.status_code == 200


@pytest.mark.parametrize("call", [_call(), None], ids=["unmarked", "unknown"])
def test_transfer_check_unmarked_or_unknown_call_returns_404(client, call):
    with patch.object(CallService, "get_by_sid", AsyncMock(return_value=call)):
        response = client.get(_url("transfer-check", CallSid="CA1"))
    assert response.status_code == 404
    assert response.json() == {"transfer": False}


def test_transfer_check_without_call_sid_returns_400(client):
    with patch.object(CallService, "get_by_sid", AsyncMock()) as get:
        response = client.get(_url("transfer-check"))
    assert response.status_code == 400
    get.assert_not_awaited()


@pytest.mark.parametrize("path", ["transfer-check", "transfer-number"])
def test_transfer_endpoints_reject_wrong_token(client, path):
    with patch.object(CallService, "get_by_sid", AsyncMock()) as get:
        response = client.get(f"/api/v1/telephony/{path}?CallSid=CA1&webhook_token=wrong")
    assert response.status_code == 401
    get.assert_not_awaited()


def test_transfer_number_returns_saved_number_as_json(client):
    call = _call(transfer_requested=True, transfer_number="+913333333333")
    with patch.object(CallService, "get_by_sid", AsyncMock(return_value=call)):
        response = client.get(_url("transfer-number", CallSid="CA1"))
    assert response.status_code == 200
    assert response.json()["destination"]["numbers"] == ["+913333333333"]
    assert response.headers["content-type"].startswith("application/json")


def test_transfer_number_resolves_department(client):
    settings = get_settings()
    call = _call(transfer_requested=True, transfer_department="stay")
    with (
        patch.object(settings, "transfer_numbers", "stay=+912222222222"),
        patch.object(CallService, "get_by_sid", AsyncMock(return_value=call)),
    ):
        response = client.get(_url("transfer-number", CallSid="CA1"))
    assert response.json()["destination"]["numbers"][0] == "+912222222222"


@pytest.mark.parametrize("call_sid", ["CA_UNKNOWN", ""])
def test_transfer_number_falls_back_to_default(client, call_sid):
    settings = get_settings()
    with (
        patch.object(settings, "transfer_default_number", "+919761615375"),
        patch.object(settings, "transfer_numbers", ""),
        patch.object(CallService, "get_by_sid", AsyncMock(return_value=None)),
    ):
        response = client.get(_url("transfer-number", CallSid=call_sid))
    assert response.status_code == 200
    assert response.json()["destination"]["numbers"] == ["+919761615375"]


# ── Dashboard API ────────────────────────────────────────────────────────────


def test_calls_api_includes_transfer_fields():
    call = Call(
        id=uuid.uuid4(),
        call_sid="SID1",
        caller_number="+91000",
        status="completed",
        direction="inbound",
        provider="exotel",
        created_at=datetime.now(UTC),
        transfer_requested=True,
        transfer_department="infra",
    )
    plain = Call(
        id=uuid.uuid4(),
        call_sid="SID2",
        caller_number="+91000",
        status="completed",
        direction="inbound",
        provider="exotel",
        created_at=datetime.now(UTC),
    )
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[get_current_user_id] = lambda: "dev-admin@example.com"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        with patch.object(
            CallService, "list_filtered", AsyncMock(return_value=([call, plain], 2))
        ):
            response = client.get("/api/v1/calls")
    finally:
        app.dependency_overrides.clear()

    first, second = response.json()["items"]
    assert first["transfer_requested"] is True
    assert first["transfer_department"] == "infra"
    assert second["transfer_requested"] is False
    assert second["transfer_department"] is None


def test_call_detail_schema_has_transfer_fields():
    from app.api.v1.calls import CallDetailResponse

    assert {"transfer_requested", "transfer_department"} <= set(CallDetailResponse.model_fields)
