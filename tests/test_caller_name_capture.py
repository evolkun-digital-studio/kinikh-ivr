"""Caller names are answers in context, not just introduction keywords."""

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.context_manager import CallContext
from app.ai.conversation import ConversationEngine
from app.prompts.system import INITIAL_GREETING
from app.voice.speech_handler import SpeechHandler, _Response
from app.voice.turn_metrics import TurnMetrics


def _handler(db, question="Could you please share your name?"):
    handler = SpeechHandler(db=db, voice=AsyncMock(), ai_client=AsyncMock())
    handler._context = CallContext(call_sid="NAME_TEST", caller_number="")
    handler._context.add_message("assistant", question)
    return handler


def test_opening_uses_namaste():
    assert INITIAL_GREETING.startswith("Namaste.")
    assert "Namaskar" not in INITIAL_GREETING


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question,text,expected",
    [
        ("Could you please share your name?", "Rahul", "Rahul"),
        ("What should I call you?", "Rahul Kumar Sharma", "Rahul Kumar Sharma"),
        ("Who am I speaking with?", "Aditi Rao", "Aditi Rao"),
        ("आपका नाम क्या है?", "राहुल", "राहुल"),
        ("Aapka naam kya hai?", "Rahul Sharma", "Rahul Sharma"),
        ("आपका नाम बताइए।", "जी, राहुल शर्मा है।", "राहुल शर्मा"),
        ("Could you share your name?", "Yes, Rahul.", "Rahul"),
        ("What's your name?", "R. K. Sharma", "R. K. Sharma"),
        ("What's your name?", "Anne-Marie O'Connor", "Anne-Marie O'Connor"),
        ("What's your name?", "Bill", "Bill"),
    ],
)
async def test_bare_name_answers_saved(mock_db_session, question, text, expected):
    handler = _handler(mock_db_session, question)
    handler._context.add_message("user", text)
    await handler._try_extract_fields(text)
    assert handler._context.collected["name"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,expected",
    [
        ("My name is Rahul Kumar Sharma.", "Rahul Kumar Sharma"),
        ("My name Rahul", "Rahul"),
        ("Mera naam Rahul Sharma hai", "Rahul Sharma"),
        ("mera name Rahul hai", "Rahul"),
        ("मेरा नाम राहुल शर्मा है।", "राहुल शर्मा"),
        ("I'm Rahul.", "Rahul"),
        ("I am Rahul and I need a website", "Rahul"),
    ],
)
async def test_natural_introductions_saved_without_asking(mock_db_session, text, expected):
    handler = _handler(mock_db_session, "How can I help?")
    handler._context.add_message("user", text)
    await handler._try_extract_fields(text)
    assert handler._context.collected["name"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "haan ji",
        "नहीं",
        "website",
        "construction",
        "9876543210",
        "I need a website",
        "मुझे घर बनवाना है",
        "I don't want to share my name",
        "I'm interested in construction",
        "My name is not important",
        "My name is",
        "राहुल या रोहित",
    ],
)
async def test_non_names_and_uncertainty_not_saved(mock_db_session, text):
    handler = _handler(mock_db_session)
    handler._context.add_message("user", text)
    await handler._try_extract_fields(text)
    assert handler._context.collected["name"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "question",
    [
        "How can I help?",
        "Which city is your project in?",
        "What is your company name?",
        "I have your name. What city is your project in?",
        "I have your name, could you tell me the project location?",
    ],
)
async def test_bare_words_are_only_names_after_personal_name_question(mock_db_session, question):
    handler = _handler(mock_db_session, question)
    await handler._try_extract_fields("Rahul")
    assert handler._context.collected["name"] is None


@pytest.mark.asyncio
async def test_explicit_name_correction_replaces_previous_name(mock_db_session):
    handler = _handler(mock_db_session, "How can I help?")
    handler._context.collected["name"] = "Raj"
    await handler._try_extract_fields("No, my name is Rahul Sharma")
    assert handler._context.collected["name"] == "Rahul Sharma"


@pytest.mark.asyncio
async def test_collected_name_stays_in_prompt_beyond_history_window():
    ctx = CallContext(call_sid="NAME_TEST", caller_number="")
    ctx.collected["name"] = "Rahul Sharma"
    for _ in range(5):
        ctx.add_message("user", "Tell me more about this service")
        ctx.add_message("assistant", "Our team will explain the next step.")
    engine = ConversationEngine([], client=AsyncMock())
    messages = await engine._live_messages(ctx, text="", rag_context="")
    assert "Rahul Sharma" in messages[0]["content"]
    assert "do not ask for their name again" in messages[0]["content"].lower()
    assert len(messages) == 9


@pytest.mark.asyncio
async def test_name_answer_does_not_add_1200ms_pause(mock_db_session):
    handler = _handler(mock_db_session)
    response = _Response(
        turn_id=1, kind="caller_turn", metrics=TurnMetrics(call_sid="NAME_TEST", turn_id=1)
    )
    with patch("app.voice.speech_handler.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await handler._wait_if_unfinished(response, "Rahul")
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_bare_name_reaches_same_turn_reply_prompt(mock_db_session):
    from app.services.department_service import DepartmentService
    from tests.test_pipeline import _loud_audio

    voice = AsyncMock()
    voice.transcribe.return_value = "Rahul"
    voice.synthesize.return_value = b"\x01\x00" * 160
    ai = AsyncMock()
    ai.chat.return_value = "What sort of website do you need?"
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=ai)
    handler.set_audio_sender(AsyncMock())
    with (
        patch.object(handler._settings, "ai_auto_greeting", False),
        patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])),
    ):
        await handler._ensure_initialized("NAME_LIVE_TEST", "+919876543210")
    handler._context.add_message("assistant", "Could you please share your name?")
    await handler._on_audio_flushed(_loud_audio())
    assert handler._context.collected["name"] == "Rahul"
    messages = ai.chat.call_args.args[0]
    assert '"name": "Rahul"' in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "Rahul"}
    assert handler._context.language == "en"
    ai.chat.assert_awaited_once()
    ai.chat_json.assert_not_awaited()
