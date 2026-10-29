"""Sticky-but-adaptive call language: local analysis, session profile
hysteresis, explicit overrides, prompt wiring, STT hint and latency."""

from __future__ import annotations

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.context_manager import CallContext
from app.ai.language_profile import (
    EN,
    HI,
    HIGH,
    HINGLISH,
    LOW,
    UNKNOWN,
    analyze_language,
    detect_explicit_request,
    update_language_profile,
)


def _ctx() -> CallContext:
    return CallContext(call_sid="CA_LANG", caller_number="+919876543210")


def _turn(ctx: CallContext, text: str, **kwargs) -> str:
    """Feed one caller turn; return the response language for it."""
    return update_language_profile(ctx, analyze_language(text), **kwargs).response_language


def _loud_audio(num_samples: int = 4000) -> bytes:
    return b"".join(struct.pack("<h", 8000 if i % 2 == 0 else -8000) for i in range(num_samples))


# ── Per-turn analysis ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("I want to know about architecture services.", EN),
        ("Okay, how long does construction usually take?", EN),
        ("Mujhe aapki sevaon ke baare mein jankari chahiye.", HI),
        ("Mujhe ghar banwana hai.", HI),
        ("मुझे घर बनाना है", HI),
        ("Mujhe architecture services ke baare mein jaanna hai.", HINGLISH),
        ("Achha construction ka kaam bhi karte ho?", HINGLISH),
        ("Interior ka process kya hai?", HINGLISH),
        ("Project start hone mein kitna time lagta hai?", HINGLISH),
        ("Residential project ke liye architectural planning karte ho?", HINGLISH),
        ("मुझे interior design चाहिए", HINGLISH),
    ],
)
def test_meaningful_turn_language(text, language):
    analysis = analyze_language(text)
    assert analysis.meaningful
    assert analysis.language == language
    assert analysis.mixed_language == (language == HINGLISH)


@pytest.mark.parametrize(
    "text",
    ["hello", "yes", "haan", "ji", "okay", "hello ji", "theek hai", "hmm", "achha", "yes please",
     "ok sir", "हाँ जी", "Raj Kumar Sharma", "residential project in Pune", ""],
)
def test_low_evidence_turns_are_not_meaningful(text):
    analysis = analyze_language(text)
    assert not analysis.meaningful
    assert analysis.confidence == LOW


def test_english_words_shared_with_hindi_are_not_hindi_markers():
    """"to", "me", "the" used to count as Hindi and flip English to Hinglish."""
    assert analyze_language("Can you send it to me").language == EN
    assert analyze_language("I want to talk to the team").language == EN


def test_greeting_word_does_not_make_mixed_turn_english():
    # TEST D — "Hi" at the start must not decide the language.
    analysis = analyze_language("Hi, mujhe architecture services ke baare mein information chahiye.")
    assert analysis.language == HINGLISH
    assert analysis.confidence == HIGH


def test_extra_acknowledgements_are_configurable():
    text = "bilkul sahi baat"
    assert analyze_language(text).language == HI
    extended = analyze_language(text, extra_acknowledgements="bilkul, sahi, baat")
    assert not extended.meaningful


def test_analysis_log_fields_never_contain_caller_words():
    fields = analyze_language("Mera naam Rahul hai aur mujhe ghar chahiye").as_log_fields()
    assert "rahul" not in str(fields).lower()
    assert set(fields) == {"detected", "confidence", "meaningful", "mixed_language", "explicit_request"}


# ── Primary language (1–3) ──────────────────────────────────────────────────


def test_1_first_meaningful_english_turn_selects_english():
    ctx = _ctx()
    assert _turn(ctx, "hello") == UNKNOWN  # greeting establishes nothing
    assert not ctx.language_locked
    assert _turn(ctx, "I want to know about architecture services.") == EN
    assert ctx.primary_language == EN and ctx.language == EN and ctx.language_detected


def test_2_first_meaningful_hindi_turn_selects_hindi():
    ctx = _ctx()
    # Hindi is detected for the call, but replies use the lighter Hinglish style.
    assert _turn(ctx, "Mujhe aapki sevaon ke baare mein jankari chahiye.") == HINGLISH
    assert ctx.primary_language == HI and ctx.language == HI


def test_explicit_hindi_request_gets_pure_hindi_replies():
    ctx = _ctx()
    _turn(ctx, "I want to know about architecture services.")
    assert _turn(ctx, "Hindi mein baat karo") == HI


def test_3_first_mixed_turn_selects_hinglish():
    ctx = _ctx()
    assert _turn(ctx, "haan ji") == UNKNOWN
    assert _turn(ctx, "Hi, mujhe architecture services ke baare mein information chahiye.") == HINGLISH
    assert ctx.primary_language == HINGLISH


def test_greeting_and_acknowledgements_never_establish_primary():
    ctx = _ctx()
    for text in ("hello", "yes", "haan", "ji", "okay", "hello ji"):
        _turn(ctx, text)
    assert ctx.primary_language == UNKNOWN and not ctx.language_locked


# ── Acknowledgements never switch (4–7) ─────────────────────────────────────


def test_4_yes_does_not_switch_hinglish_conversation_to_english():
    ctx = _ctx()
    _turn(ctx, "Mujhe interior design service ke baare mein information chahiye.")
    assert _turn(ctx, "yes") == HINGLISH
    assert ctx.language == HINGLISH


def test_5_okay_does_not_switch_language():
    # TEST B
    ctx = _ctx()
    assert _turn(ctx, "Mujhe ghar banwana hai.") == HINGLISH
    assert _turn(ctx, "Interior design bhi karte ho?") == HINGLISH
    assert _turn(ctx, "okay") == HINGLISH
    assert ctx.language == HI


def test_6_haan_does_not_switch_english_conversation_to_hindi():
    # TEST A
    ctx = _ctx()
    assert _turn(ctx, "Hi, I want to know about your services.") == EN
    assert _turn(ctx, "Do you provide construction?") == EN
    assert _turn(ctx, "haan") == EN
    assert ctx.language == EN


def test_7_ji_does_not_switch_english_conversation_to_hindi():
    ctx = _ctx()
    _turn(ctx, "I want to know about architecture.")
    assert _turn(ctx, "ji") == EN
    assert _turn(ctx, "theek hai") == EN
    assert ctx.language == EN and ctx.current_turn_language == UNKNOWN


# ── Mid-call switching (8–12) ───────────────────────────────────────────────


def test_8_full_hindi_turn_during_english_call_gets_hindi_response():
    ctx = _ctx()
    _turn(ctx, "I want to know about architecture.")
    assert _turn(ctx, "मुझे अपना घर बनवाना है, कितना खर्चा आएगा?") == HINGLISH
    assert ctx.language == HI  # Clear Hindi switches the whole reply and call.


def test_9_full_english_turn_during_hindi_call_gets_english_response():
    ctx = _ctx()
    _turn(ctx, "मुझे घर बनाना है")
    assert _turn(ctx, "Okay, and approximately how long does the process take?") == EN
    assert ctx.language == EN


def test_10_hinglish_turn_gets_hinglish_response():
    ctx = _ctx()
    _turn(ctx, "I want to know about your architecture services.")
    assert _turn(ctx, "Achha, construction ka bhi kaam karte ho?") == HINGLISH
    assert _turn(ctx, "Interior ka process kya hai?") == HINGLISH
    assert _turn(ctx, "Okay, and approximately how long does the process take?") == EN


def test_11_two_consecutive_new_language_turns_update_preference():
    ctx = _ctx()
    _turn(ctx, "I want to know about architecture.")
    _turn(ctx, "Achha construction ka kya process hai?")
    assert ctx.language == EN
    decision = update_language_profile(ctx, analyze_language("Kitna time lagta hai?"))
    assert decision.preference_changed and decision.switch_reason == "clear_other_language"
    assert ctx.language == HINGLISH
    assert ctx.primary_language == EN  # primary is where the call started


def test_acknowledgements_between_switch_turns_do_not_reset_the_count():
    ctx = _ctx()
    _turn(ctx, "I want to know about architecture.")
    _turn(ctx, "Achha construction ka kya process hai?")
    _turn(ctx, "haan")
    _turn(ctx, "Kitna time lagta hai?")
    assert ctx.language == HINGLISH


def test_12_low_confidence_turns_do_not_cause_flapping():
    ctx = _ctx()
    _turn(ctx, "I want to know about architecture services.")
    responses = [
        _turn(ctx, text)
        for text in ("haan ji", "ok", "theek hai", "ghar", "acha", "residential project in Pune")
    ]
    assert responses == [EN] * 6
    # A lone switched turn, then back: preference never moves.
    _turn(ctx, "Achha construction ka kya process hai?")
    _turn(ctx, "And how much would that cost?")
    _turn(ctx, "Interior ka process kya hai?")
    _turn(ctx, "What about the timeline for that?")
    assert ctx.language == EN


def test_one_very_strong_turn_moves_preference():
    ctx = _ctx()
    _turn(ctx, "मुझे घर बनाना है")
    decision = update_language_profile(
        ctx,
        analyze_language(
            "Actually I would like to know how long it usually takes and what the "
            "process is for a full renovation of my house"
        ),
    )
    assert decision.switch_reason == "clear_other_language"
    assert ctx.language == EN


def test_switch_during_call_scenario_c():
    ctx = _ctx()
    assert _turn(ctx, "I want to know about architecture.") == EN
    assert _turn(ctx, "Achha construction ka kya process hai?") == EN
    assert _turn(ctx, "Kitna time lagta hai?") == HINGLISH
    assert _turn(ctx, "Okay, can you explain the next step in English?") == EN


# ── Explicit overrides (13–15) ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Please speak in English.", EN),
        ("Can you explain the next step in English?", EN),
        ("English mein bolo", EN),
        ("Hindi mein baat karo.", HI),
        ("Can you speak Hindi?", HI),
        ("हिंदी में बात कीजिए", HI),
        ("I don't speak English", HI),
        ("English nahi aati", HI),
        ("Hinglish mein batao.", HINGLISH),
        ("Hindi ya English, koi bhi", None),
        ("I want to know about construction", None),
    ],
)
def test_explicit_request_detection(text, language):
    assert detect_explicit_request(text) == language


def test_13_explicit_speak_english_overrides_immediately():
    ctx = _ctx()
    _turn(ctx, "मुझे घर बनाना है")
    decision = update_language_profile(ctx, analyze_language("Please speak in English."))
    assert decision.response_language == EN and decision.switch_reason == "explicit_request"
    assert ctx.language == EN and ctx.language_explicit
    # Short turns keep the preference; a clear new language can change it.
    assert _turn(ctx, "haan") == EN
    assert _turn(ctx, "Mujhe interior design chahiye ghar ke liye") == HINGLISH


def test_14_explicit_hindi_mein_baat_karo_overrides_immediately():
    ctx = _ctx()
    _turn(ctx, "I want to know about your architecture services.")
    assert _turn(ctx, "Hindi mein baat karo.") == HI
    assert ctx.language == HI
    assert _turn(ctx, "And what about interior design?") == EN


def test_15_explicit_hinglish_request_works():
    ctx = _ctx()
    _turn(ctx, "I want to know about your architecture services.")
    assert _turn(ctx, "Hinglish mein batao.") == HINGLISH
    assert ctx.language == HINGLISH
    assert _turn(ctx, "okay") == HINGLISH


def test_explicit_request_as_first_turn_establishes_primary():
    ctx = _ctx()
    assert _turn(ctx, "Hindi please") == HI
    assert ctx.primary_language == HI and ctx.language_locked


def test_explicit_preference_changes_again_on_new_explicit_request():
    ctx = _ctx()
    _turn(ctx, "Hindi mein baat karo.")
    assert _turn(ctx, "Please speak in English.") == EN
    assert ctx.language == EN


# ── Prompt: RAG facts vs response language (16–18) ──────────────────────────

_ENGLISH_FACT = "Kinikh provides architecture, planning and construction services. Site visits cost Rs 2,500."


async def _system_prompt(ctx: CallContext) -> str:
    from app.ai.conversation import ConversationEngine

    engine = ConversationEngine([], client=AsyncMock())
    retriever = MagicMock()
    retriever.retrieve_context = AsyncMock(return_value=_ENGLISH_FACT)
    with patch("app.rag.retriever.get_retriever", return_value=retriever):
        return await engine._build_system_prompt(ctx, text="caller question")


@pytest.mark.asyncio
async def test_16_english_rag_context_with_hindi_response_language():
    ctx = _ctx()
    _turn(ctx, "मुझे बताइए construction services क्या देते हैं आप लोग")
    _turn(ctx, "मुझे घर बनवाना है, कितना खर्चा आएगा?")
    prompt = await _system_prompt(ctx)
    assert "CALL LANGUAGE: Hinglish" in prompt
    assert _ENGLISH_FACT in prompt
    assert "Never reply in English just because the facts are in English." in prompt


@pytest.mark.asyncio
async def test_17_english_rag_context_with_hinglish_response_language():
    ctx = _ctx()
    _turn(ctx, "I want to know about your services.")
    _turn(ctx, "Mujhe construction ke baare mein bataiye please")
    prompt = await _system_prompt(ctx)
    assert ctx.primary_language == EN
    assert "CALL LANGUAGE: Hinglish" in prompt
    assert _ENGLISH_FACT in prompt
    assert "Speak natural, English-heavy Hinglish" in prompt


@pytest.mark.asyncio
async def test_18_facts_identical_across_response_languages():
    prompts = {}
    for first_turn in (
        "I want to know about architecture services.",
        "Mujhe aapki sevaon ke baare mein jankari chahiye.",
        "Mujhe architecture services ke baare mein jaanna hai.",
    ):
        ctx = _ctx()
        _turn(ctx, first_turn)
        prompts[ctx.language] = await _system_prompt(ctx)
    assert set(prompts) == {EN, HI, HINGLISH}

    def _facts(prompt: str) -> str:
        return prompt[prompt.index("# Company information"):].split("\n\n# How to speak")[0]

    # The company facts and RAG text reach the model byte-for-byte the same,
    # whatever the language; only the language directive differs.
    assert _facts(prompts[EN]) == _facts(prompts[HI]) == _facts(prompts[HINGLISH])
    for prompt in prompts.values():
        assert "Changing language never changes facts" in prompt


def test_prompt_before_first_meaningful_turn_has_no_language():
    from app.ai.prompt_manager import PromptManager

    prompt = PromptManager().greeting("Kinikh Group", "INFO")
    assert "CALL LANGUAGE: not decided yet" in prompt
    assert "Reply in the language the caller is using:" in prompt


def test_prompt_marks_acknowledgement_turn_as_unclear():
    from app.ai.prompt_manager import PromptManager

    prompt = PromptManager().greeting(
        "Kinikh Group", "INFO", language=EN, language_locked=True,
        response_language=EN,
    )
    assert "CALL LANGUAGE: English. Reply only in this language." in prompt
    assert "CALL LANGUAGE: English" in prompt


# ── Live call path: STT hint, no LLM, logging, latency (19–21) ──────────────


def _handler(mock_db_session, transcripts: list[str]):
    from app.voice.speech_handler import SpeechHandler

    voice = AsyncMock()
    voice.transcribe = AsyncMock(side_effect=transcripts)
    voice.synthesize = AsyncMock(return_value=b"\x00\x00" * 400)
    ai = AsyncMock()
    ai.chat = AsyncMock(return_value="Ji, bilkul.")
    handler = SpeechHandler(db=mock_db_session, voice=voice, ai_client=ai)
    handler.set_audio_sender(AsyncMock())
    return handler, voice, ai


@pytest.mark.asyncio
async def test_19_mixed_language_stt_text_preserved_and_never_language_locked(mock_db_session):
    from app.services.department_service import DepartmentService

    mixed = "Mujhe interior design service ke baare mein information chahiye."
    handler, voice, ai = _handler(
        mock_db_session, ["I want to know about your architecture services.", mixed]
    )
    with patch.object(DepartmentService, "list_active", AsyncMock(return_value=[])):
        await handler._ensure_initialized("CA_STT", "+919876543210")
        await handler._on_audio_flushed(_loud_audio())
        assert handler._context.language_locked and handler._context.language == EN
        await handler._on_audio_flushed(_loud_audio())

    # First turn auto-detects; the established English call hints English.
    assert voice.transcribe.call_args_list[0].args[1] is None
    assert voice.transcribe.call_args_list[1].args[1] == EN
    user_messages = [m.content for m in handler._context.messages if m.role == "user"]
    assert user_messages[-1] == mixed
    # This mixed turn is below the 70% Hindi switch threshold.
    system_prompt = ai.chat.call_args_list[-1].args[0][0]["content"]
    assert "CALL LANGUAGE: English" in system_prompt
    await handler.mark_stopped()


def test_openai_transcribe_without_hint_sends_no_language():
    from app.ai.openai_client import OpenAIClient

    client = OpenAIClient.__new__(OpenAIClient)
    captured = {}

    async def _retry(_label, fn):
        return fn()

    client._retry = _retry
    client._raw = MagicMock()
    client._raw.audio.transcriptions.create = MagicMock(
        side_effect=lambda **kw: captured.update(kw) or MagicMock(text=" Mujhe interior design chahiye ")
    )
    with patch("app.ai.knowledge.stt_vocabulary_prompt", return_value=""):
        text = asyncio.run(client.transcribe(b"RIFF", language=None))
    assert text == "Mujhe interior design chahiye"
    assert "language" not in captured


def test_20_language_analysis_makes_no_llm_or_network_call(mock_db_session):
    from app.voice.speech_handler import SpeechHandler

    ai = AsyncMock()
    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=ai)
    handler._context = _ctx()
    ai.reset_mock()  # ignore truthiness checks made while constructing the handler
    with patch("openai.AsyncOpenAI") as openai_cls, patch("httpx.AsyncClient") as http_cls:
        for text in (
            "hello",
            "I want to know about architecture.",
            "Achha construction ka kya process hai?",
            "Mujhe aapki sevaon ke baare mein jankari chahiye.",
            "Hindi mein baat karo.",
        ):
            handler._update_language(text, turn_id=1)
    assert ai.mock_calls == []
    openai_cls.assert_not_called()
    http_cls.assert_not_called()
    assert not hasattr(handler, "_language_detector")


def test_language_detected_event_is_structured_and_content_free(mock_db_session):
    from app.voice.speech_handler import SpeechHandler

    handler = SpeechHandler(db=mock_db_session, voice=AsyncMock(), ai_client=AsyncMock())
    handler._context = _ctx()
    handler._update_language("I want to know about architecture services.", turn_id=1)
    with patch("app.voice.speech_handler.log_call_event") as log_event:
        elapsed = handler._update_language("Achha mera naam Rahul hai, construction ka kya process hai?", turn_id=2)
    fields = log_event.call_args.kwargs
    assert log_event.call_args.args == ("voice.language.detected",)
    assert fields["turn_id"] == 2
    assert fields["detected"] == HINGLISH
    assert fields["previous_preference"] == EN
    assert fields["response_language"] == EN
    assert fields["meaningful"] is True
    assert fields["switch_reason"] == "not_clearly_other_language"
    assert isinstance(elapsed, float) and fields["language_ms"] == elapsed
    assert "rahul" not in str(fields).lower()


def test_21_language_processing_latency_is_negligible():
    turns = [
        "hello",
        "I want to know about your architecture services.",
        "Achha, construction ka bhi kaam karte ho?",
        "मुझे अपना घर बनवाना है, क्या आप लोग service देते हैं?",
        "okay",
        "Okay, and approximately how long does the process take?",
        "Hindi mein baat karo.",
    ] * 50
    ctx = _ctx()
    started = time.perf_counter()
    for text in turns:
        update_language_profile(ctx, analyze_language(text))
    per_turn_ms = (time.perf_counter() - started) * 1000 / len(turns)
    # STT/LLM/TTS are hundreds of ms each; budget is far below that.
    assert per_turn_ms < 2.0, per_turn_ms
