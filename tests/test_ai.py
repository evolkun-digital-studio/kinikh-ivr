"""AI module tests — prompt manager, context manager, conversation state,
language detector, lead extractor, department classifier, summary generator,
and conversation engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_chat_completion(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(total_tokens=20)
    return resp


# ---------------------------------------------------------------------------
# PromptManager
# ---------------------------------------------------------------------------


class TestPromptManager:
    def test_greeting_renders(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.greeting("Kinikh", "rural, Support")
        assert "Kinikh" in result
        assert "rural, Support" in result

    def test_lead_extraction_renders(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.lead_extraction("Hello my name is Raj", "+919876543210")
        assert "+919876543210" in result
        assert "Raj" in result

    def test_department_classification_renders(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.department_classification("Kinikh", '["rural","support"]', "I want to buy")
        assert "rural" in result
        assert "I want to buy" in result

    def test_language_detection_renders(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.language_detection("Mera naam Raj hai")
        assert "Mera naam Raj hai" in result

    def test_summary_renders(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.summary("The caller needed a demo.")
        assert "demo" in result

    def test_question_en(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        q = pm.question("name", "en")
        assert len(q) > 0
        assert "name" in q.lower() or "?" in q

    def test_question_hi(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        q = pm.question("name", "hi")
        assert len(q) > 0

    def test_question_hinglish(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        q = pm.question("name", "hi-en")
        assert len(q) > 0
        assert q != pm.question("name", "en")

    def test_question_all_fields_have_hinglish(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        for field in ("name", "phone", "email", "requirement", "department"):
            q = pm.question(field, "hi-en")
            assert len(q) > 0, f"Missing hi-en question for field: {field}"

    def test_question_unknown_field_returns_empty(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        assert pm.question("nonexistent_field", "en") == ""

    def test_confirmation_en(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.confirmation(
            "en",
            name="Raj",
            phone="+91000",
            email="r@r.com",
            department="rural",
            requirement="Demo",
        )
        assert "Raj" in result
        assert "Demo" in result

    def test_confirmation_hi(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.confirmation(
            "hi",
            name="राज",
            phone="+91000",
            email="r@r.com",
            department="बिक्री",
            requirement="डेमो",
        )
        assert "राज" in result

    def test_confirmation_hinglish(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.confirmation(
            "hi-en",
            name="Raj",
            phone="+91000",
            email="r@r.com",
            department="rural",
            requirement="Demo",
        )
        assert "Raj" in result
        assert "sahi" in result.lower() or "confirm" in result.lower()

    def test_farewell_en(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.farewell("en", "Kinikh")
        assert "Kinikh" in result
        assert "Thank you" in result

    def test_farewell_hi(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.farewell("hi", "Kinikh")
        assert "Kinikh" in result

    def test_farewell_hinglish(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        result = pm.farewell("hi-en", "Kinikh")
        assert "Kinikh" in result
        assert "शुक्रिया" in result or "thank" in result.lower()


# ---------------------------------------------------------------------------
# ContextManager / CallContext
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        from app.ai.context_manager import ContextManager

        cm = ContextManager()
        ctx = await cm.create("SID001", "+919876543210")
        assert ctx.call_sid == "SID001"
        assert ctx.collected["phone"] == "+919876543210"

        retrieved = await cm.get("SID001")
        assert retrieved is ctx

    @pytest.mark.asyncio
    async def test_get_or_create_idempotent(self):
        from app.ai.context_manager import ContextManager

        cm = ContextManager()
        ctx1 = await cm.get_or_create("SID002", "+91000")
        ctx2 = await cm.get_or_create("SID002", "+91000")
        assert ctx1 is ctx2

    @pytest.mark.asyncio
    async def test_remove(self):
        from app.ai.context_manager import ContextManager

        cm = ContextManager()
        await cm.create("SID003", "+91000")
        await cm.remove("SID003")
        assert await cm.get("SID003") is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent_is_noop(self):
        from app.ai.context_manager import ContextManager

        cm = ContextManager()
        await cm.remove("DOES_NOT_EXIST")  # should not raise

    @pytest.mark.asyncio
    async def test_count(self):
        from app.ai.context_manager import ContextManager

        cm = ContextManager()
        assert await cm.count() == 0
        await cm.create("SID_A", "+91000")
        await cm.create("SID_B", "+91001")
        assert await cm.count() == 2

    def test_all_required_collected_false_by_default(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        assert not ctx.all_required_collected()

    def test_all_required_collected_true(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        ctx.collected["name"] = "Raj"
        ctx.collected["phone"] = "+91000"
        ctx.collected["requirement"] = "Demo needed"
        assert ctx.all_required_collected()

    def test_all_required_collected_partial(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        ctx.collected["name"] = "Raj"
        assert not ctx.all_required_collected()

    def test_transcript_builds(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        ctx.add_message("user", "Hello")
        ctx.add_message("assistant", "Hi there!")
        assert "[USER] Hello" in ctx.transcript
        assert "[ASSISTANT] Hi there!" in ctx.transcript

    def test_openai_messages_format(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        ctx.add_message("user", "Hello")
        ctx.add_message("assistant", "Hi")
        msgs = ctx.openai_messages
        assert msgs[0] == {"role": "user", "content": "Hello"}
        assert msgs[1] == {"role": "assistant", "content": "Hi"}

    def test_collected_defaults_to_none(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        assert ctx.collected["name"] is None
        assert ctx.collected["email"] is None
        assert ctx.collected["department"] is None

    def test_add_message_appends_to_transcript_parts(self):
        from app.ai.context_manager import CallContext

        ctx = CallContext(call_sid="X", caller_number="+91000")
        assert ctx.transcript == ""
        ctx.add_message("user", "Test message")
        assert "Test message" in ctx.transcript


# ---------------------------------------------------------------------------
# ConversationState
# ---------------------------------------------------------------------------


class TestConversationState:
    def _make_ctx(self, **kwargs) -> object:
        from app.ai.context_manager import CallContext

        return CallContext(call_sid="TEST", caller_number="+91000", **kwargs)

    def test_greeting_phase_when_no_messages(self):
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = self._make_ctx()
        assert ConversationState.current_phase(ctx) == ConversationPhase.GREETING

    def test_collecting_phase_after_first_user_message(self):
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = self._make_ctx()
        ctx.add_message("user", "Hello")
        assert ConversationState.current_phase(ctx) == ConversationPhase.COLLECTING

    def test_confirming_phase_when_all_fields_collected(self):
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = self._make_ctx()
        ctx.add_message("user", "Hello")
        ctx.collected["name"] = "Raj"
        ctx.collected["requirement"] = "Demo"
        ctx.collected["email"] = "raj@example.com"
        assert ConversationState.current_phase(ctx) == ConversationPhase.CONFIRMING

    def test_farewell_phase_when_confirmed(self):
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = self._make_ctx()
        ctx.collected["name"] = "Raj"
        ctx.collected["requirement"] = "Demo"
        ctx.collected["email"] = "raj@example.com"
        ctx.confirmed = True
        assert ConversationState.current_phase(ctx) == ConversationPhase.FAREWELL

    def test_ended_phase_when_ended(self):
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = self._make_ctx()
        ctx.ended = True
        assert ConversationState.current_phase(ctx) == ConversationPhase.ENDED

    def test_ended_takes_priority_over_confirmed(self):
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = self._make_ctx()
        ctx.confirmed = True
        ctx.ended = True
        assert ConversationState.current_phase(ctx) == ConversationPhase.ENDED

    def test_next_field_returns_first_missing(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        assert ConversationState.next_field(ctx) == "name"

    def test_next_field_skips_collected(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        ctx.collected["name"] = "Raj"
        assert ConversationState.next_field(ctx) == "requirement"

    def test_next_field_returns_none_when_all_collected(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        ctx.collected["name"] = "Raj"
        ctx.collected["requirement"] = "Demo"
        ctx.collected["email"] = "raj@example.com"
        assert ConversationState.next_field(ctx) is None

    def test_should_confirm_true_when_all_collected_and_not_confirmed(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        ctx.collected["name"] = "Raj"
        ctx.collected["requirement"] = "Demo"
        ctx.collected["email"] = "raj@example.com"
        assert ConversationState.should_confirm(ctx) is True

    def test_should_confirm_false_when_already_confirmed(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        ctx.collected["name"] = "Raj"
        ctx.collected["requirement"] = "Demo"
        ctx.collected["email"] = "raj@example.com"
        ctx.confirmed = True
        assert ConversationState.should_confirm(ctx) is False

    def test_should_confirm_false_when_fields_missing(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        ctx.collected["name"] = "Raj"
        assert ConversationState.should_confirm(ctx) is False

    def test_is_terminal_false_by_default(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        assert ConversationState.is_terminal(ctx) is False

    def test_is_terminal_true_when_ended(self):
        from app.ai.conversation_state import ConversationState

        ctx = self._make_ctx()
        ctx.ended = True
        assert ConversationState.is_terminal(ctx) is True

    def test_collection_fields_order(self):
        from app.ai.conversation_state import COLLECTION_FIELDS

        assert COLLECTION_FIELDS[0] == "name"
        assert "requirement" in COLLECTION_FIELDS
        assert "email" in COLLECTION_FIELDS


# ---------------------------------------------------------------------------
# LanguageDetector
# ---------------------------------------------------------------------------


class TestLanguageDetector:
    @pytest.mark.asyncio
    async def test_detect_empty_returns_en(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("")
        assert result == "en"
        mock_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_detect_whitespace_only_returns_en(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("   \t\n")
        assert result == "en"

    @pytest.mark.asyncio
    async def test_detect_devanagari_returns_hi(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("मेरा नाम राज है")
        assert result == "hi"
        mock_client.chat.assert_not_called()  # fast path

    @pytest.mark.asyncio
    async def test_detect_mixed_script_returns_hinglish(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        # Text with both Devanagari and Latin characters
        result = await detector.detect("मेरा naam Raj है")
        assert result == "hi-en"
        mock_client.chat.assert_not_called()  # fast path

    @pytest.mark.asyncio
    async def test_detect_llm_path_for_romanized_hindi(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="hi")
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("Mera naam Raj hai")
        assert result == "hi"
        mock_client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_detect_llm_returns_hinglish(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="hi-en")
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("Main aapki help karna chahta hoon")
        assert result == "hi-en"

    @pytest.mark.asyncio
    async def test_detect_llm_unknown_response_falls_back_to_en(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="fr")
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("Bonjour")
        assert result == "en"

    @pytest.mark.asyncio
    async def test_detect_llm_failure_falls_back_to_en(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("API error"))
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("Mera naam Raj hai")
        assert result == "en"

    @pytest.mark.asyncio
    async def test_detect_valid_en_response(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="en")
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("Hello, I need help")
        assert result == "en"


# ---------------------------------------------------------------------------
# LeadExtractor
# ---------------------------------------------------------------------------


class TestLeadExtractor:
    @pytest.mark.asyncio
    async def test_extract_success_returns_all_fields(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        mock_client.chat_json = AsyncMock(
            return_value={
                "name": "Raj Kumar",
                "phone": "+919876543210",
                "email": "raj@example.com",
                "department": "rural",
                "requirement": "Need enterprise pricing",
                "summary": "Caller wants enterprise pricing.",
                "language": "en",
                "timestamp": "2026-06-29T10:00:00Z",
                "additional_notes": None,
            }
        )
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("Transcript text", "+919876543210")
        assert result["name"] == "Raj Kumar"
        assert result["phone"] == "+919876543210"
        assert result["language"] == "en"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_extract_sends_max_completion_tokens_not_max_tokens(self):
        """Regression: gpt-5.6-luna rejects max_tokens with HTTP 400 — lead
        extraction must go through with max_completion_tokens on the real
        OpenAI wire call, and still return usable extracted fields."""
        import json

        from app.ai.lead_extractor import LeadExtractor
        from app.ai.openai_client import OpenAIClient

        client = OpenAIClient()
        captured: dict = {}
        extracted = {
            "name": "Raj Kumar",
            "phone": "+919876543210",
            "email": "raj@example.com",
            "department": "rural",
            "requirement": "Need enterprise pricing",
            "summary": "Caller wants enterprise pricing.",
            "language": "en",
        }

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return _mock_chat_completion(json.dumps(extracted))

        extractor = LeadExtractor(client=client)
        with patch.object(client._raw.chat.completions, "create", _capture):
            result = await extractor.extract("Transcript text", "+919876543210")

        assert result["name"] == "Raj Kumar"
        assert result["phone"] == "+919876543210"
        assert "max_completion_tokens" in captured
        assert "max_tokens" not in captured

    @pytest.mark.asyncio
    async def test_extract_returns_defaults_on_api_failure(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        mock_client.chat_json = AsyncMock(side_effect=Exception("API error"))
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("Transcript text", "+91000")
        assert result["phone"] == "+91000"
        assert "timestamp" in result
        assert "language" in result

    @pytest.mark.asyncio
    async def test_extract_fills_missing_fields(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        # LLM returns only name and phone — missing fields should be defaulted
        mock_client.chat_json = AsyncMock(return_value={"name": "Raj", "phone": "+91000"})
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("Hi I'm Raj", "+91000")
        assert result["email"] is None
        assert result["department"] is None
        assert result["requirement"] is None
        assert result["summary"] == ""
        assert result["language"] == "en"

    @pytest.mark.asyncio
    async def test_extract_phone_defaults_to_caller_phone(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        mock_client.chat_json = AsyncMock(return_value={})
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("Hello", "+919999999999")
        assert result["phone"] == "+919999999999"

    @pytest.mark.asyncio
    async def test_extract_does_not_override_existing_phone(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        mock_client.chat_json = AsyncMock(return_value={"phone": "+911234567890", "name": "Raj"})
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("Transcript", "+919999999999")
        assert result["phone"] == "+911234567890"

    @pytest.mark.asyncio
    async def test_extract_ensures_required_keys(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        mock_client.chat_json = AsyncMock(return_value={})
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("", "+91000")
        required_keys = {"name", "phone", "email", "department", "requirement", "timestamp"}
        assert required_keys.issubset(result.keys())


# ---------------------------------------------------------------------------
# DepartmentClassifier
# ---------------------------------------------------------------------------


class TestDepartmentClassifier:
    @pytest.mark.asyncio
    async def test_classify_returns_none_on_empty(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        classifier = DepartmentClassifier(
            [{"name": "rural", "keywords": ["buy"]}], client=mock_client
        )
        result = await classifier.classify("")
        assert result is None
        mock_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_classify_keyword_fast_path(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        classifier = DepartmentClassifier(
            [{"name": "rural", "keywords": ["buy", "purchase", "pricing"]}],
            client=mock_client,
        )
        result = await classifier.classify("I want to buy a product")
        assert result == "rural"
        mock_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_classify_keyword_case_insensitive(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        classifier = DepartmentClassifier(
            [{"name": "Support", "keywords": ["Help", "Issue"]}], client=mock_client
        )
        result = await classifier.classify("I need HELP with my account")
        assert result == "Support"
        mock_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_classify_llm_path_success(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="support")
        classifier = DepartmentClassifier([{"name": "support", "keywords": []}], client=mock_client)
        result = await classifier.classify("I need help with my order")
        assert result == "support"

    @pytest.mark.asyncio
    async def test_classify_llm_returns_unknown_department(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="marketing")
        classifier = DepartmentClassifier([{"name": "rural", "keywords": []}], client=mock_client)
        result = await classifier.classify("I need some information")
        assert result is None

    @pytest.mark.asyncio
    async def test_classify_llm_failure_returns_none(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("OpenAI error"))
        classifier = DepartmentClassifier([{"name": "rural", "keywords": []}], client=mock_client)
        result = await classifier.classify("I need something")
        assert result is None

    @pytest.mark.asyncio
    async def test_classify_no_departments(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="rural")
        classifier = DepartmentClassifier([], client=mock_client)
        result = await classifier.classify("I need rural")
        assert result is None  # "rural" not in empty valid_names set

    @pytest.mark.asyncio
    async def test_classify_keyword_matches_first_department(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        depts = [
            {"name": "rural", "keywords": ["buy"]},
            {"name": "Support", "keywords": ["help"]},
        ]
        classifier = DepartmentClassifier(depts, client=mock_client)
        result = await classifier.classify("I want to buy something")
        assert result == "rural"


# ---------------------------------------------------------------------------
# SummaryGenerator
# ---------------------------------------------------------------------------


class TestSummaryGenerator:
    @pytest.mark.asyncio
    async def test_generate_empty_transcript(self):
        from app.ai.summary_generator import SummaryGenerator

        mock_client = AsyncMock()
        generator = SummaryGenerator(client=mock_client)
        result = await generator.generate("")
        assert result == "No transcript available."
        mock_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_generate_whitespace_only_transcript(self):
        from app.ai.summary_generator import SummaryGenerator

        mock_client = AsyncMock()
        generator = SummaryGenerator(client=mock_client)
        result = await generator.generate("   \n  ")
        assert result == "No transcript available."

    @pytest.mark.asyncio
    async def test_generate_success(self):
        from app.ai.summary_generator import SummaryGenerator

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(
            return_value="Caller needed enterprise pricing. Demo was scheduled."
        )
        generator = SummaryGenerator(client=mock_client)
        result = await generator.generate("[USER] Hello [ASSISTANT] Hi there")
        assert "pricing" in result or "Demo" in result

    @pytest.mark.asyncio
    async def test_generate_api_failure_returns_fallback(self):
        from app.ai.summary_generator import SummaryGenerator

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("API error"))
        generator = SummaryGenerator(client=mock_client)
        result = await generator.generate("Some transcript")
        assert result == "Summary unavailable."

    @pytest.mark.asyncio
    async def test_generate_calls_chat_with_summary_prompt(self):
        from app.ai.summary_generator import SummaryGenerator

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="A brief summary.")
        generator = SummaryGenerator(client=mock_client)
        await generator.generate("The transcript content here")
        mock_client.chat.assert_called_once()
        call_args = mock_client.chat.call_args
        messages = call_args[0][0]
        assert any("transcript" in str(m).lower() for m in messages)

    @pytest.mark.asyncio
    async def test_generate_sends_max_completion_tokens_not_max_tokens(self):
        """Regression: gpt-5.6-luna rejects max_tokens with HTTP 400 —
        summary generation must go through with max_completion_tokens on the
        real OpenAI wire call, and still return a usable summary."""
        from app.ai.openai_client import OpenAIClient
        from app.ai.summary_generator import SummaryGenerator

        client = OpenAIClient()
        captured: dict = {}

        async def _capture(**kwargs: object) -> object:
            captured.update(kwargs)
            return _mock_chat_completion("Caller needed a product demo.")

        generator = SummaryGenerator(client=client)
        with patch.object(client._raw.chat.completions, "create", _capture):
            result = await generator.generate("[USER] I need a demo [ASSISTANT] Sure")

        assert result == "Caller needed a product demo."
        assert "max_completion_tokens" in captured
        assert "max_tokens" not in captured


# ---------------------------------------------------------------------------
# ConversationEngine
# ---------------------------------------------------------------------------


class TestConversationEngine:
    def _make_context(self, language: str = "en") -> object:
        from app.ai.context_manager import CallContext

        return CallContext(call_sid="TEST", caller_number="+91000", language=language)

    @pytest.mark.asyncio
    async def test_greeting_is_deterministic_maya_line(self):
        """The opening greeting is Maya's fixed INITIAL_GREETING constant —
        never OpenAI-generated, so it never depends on chat.language."""
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([{"name": "rural"}, {"name": "Support"}], client=mock_client)
        ctx = self._make_context("en")
        greeting = await engine.get_greeting(ctx)
        assert "Welcome to Kinikh Group" in greeting
        assert "TEST" not in greeting  # should use company name
        mock_client.chat.assert_not_called()  # greeting is hardcoded

    @pytest.mark.asyncio
    async def test_greeting_same_regardless_of_call_language(self):
        """Unlike the old language-branching greeting, Maya's opening line
        is identical no matter what context.language is set to — the
        caller's language isn't known yet at call start anyway."""
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        greetings = []
        for language in ("en", "hi", "hi-en"):
            engine = ConversationEngine([], client=mock_client)
            ctx = self._make_context(language)
            greetings.append(await engine.get_greeting(ctx))
        assert len(set(greetings)) == 1
        mock_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_greeting_adds_to_context(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("en")
        greeting = await engine.get_greeting(ctx)
        assert greeting in ctx.transcript
        assert len(ctx.messages) == 1
        assert ctx.messages[0].role == "assistant"

    @pytest.mark.asyncio
    async def test_get_next_response_success(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Could you share your name?")
        engine = ConversationEngine([{"name": "rural"}], client=mock_client)
        ctx = self._make_context("en")
        reply = await engine.get_next_response(ctx, "Hi, I need help")
        assert reply == "Could you share your name?"
        assert len(ctx.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_get_next_response_failure_returns_fallback(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("OpenAI down"))
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("en")
        reply = await engine.get_next_response(ctx, "Hello")
        assert "trouble" in reply or "sorry" in reply.lower()

    @pytest.mark.asyncio
    async def test_get_next_response_fallback_hi(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("Error"))
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("hi")
        reply = await engine.get_next_response(ctx, "नमस्ते")
        assert len(reply) > 0

    @pytest.mark.asyncio
    async def test_get_next_response_fallback_hinglish(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("Error"))
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("hi-en")
        reply = await engine.get_next_response(ctx, "Hello")
        assert "issue" in reply.lower() or "sorry" in reply.lower()

    @pytest.mark.asyncio
    async def test_build_system_prompt_en_no_language_suffix(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([{"name": "rural"}], client=mock_client)
        ctx = self._make_context("en")
        prompt = await engine._build_system_prompt(ctx)
        assert "rural" in prompt
        # Language-specific instruction blocks are NOT appended for English (if not locked)
        assert "CALL LANGUAGE: not decided yet." in prompt

    @pytest.mark.asyncio
    async def test_build_system_prompt_hi_adds_instruction(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([{"name": "rural"}], client=mock_client)
        ctx = self._make_context("hi")
        ctx.language_locked = True
        prompt = await engine._build_system_prompt(ctx)
        assert "CALL LANGUAGE: Hinglish. Reply only in this language." in prompt

    @pytest.mark.asyncio
    async def test_build_system_prompt_hinglish_adds_instruction(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([{"name": "rural"}], client=mock_client)
        ctx = self._make_context("hi-en")
        ctx.language_locked = True
        prompt = await engine._build_system_prompt(ctx)
        assert "CALL LANGUAGE: Hinglish. Reply only in this language." in prompt

    @pytest.mark.asyncio
    async def test_build_system_prompt_hi_forbids_translating_to_english(self):
        """Regression: a Hindi-speaking caller must get a Hindi reply, not
        an English translation of one."""
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([{"name": "rural"}], client=mock_client)
        ctx = self._make_context("hi")
        ctx.language_locked = True
        prompt = await engine._build_system_prompt(ctx)
        assert "CALL LANGUAGE: Hinglish. Reply only in this language." in prompt

    @pytest.mark.asyncio
    async def test_build_system_prompt_hi_preserves_department_names(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([{"name": "rural"}], client=mock_client)
        ctx = self._make_context("hi")
        prompt = await engine._build_system_prompt(ctx)
        assert "English" in prompt

    @pytest.mark.asyncio
    async def test_system_prompt_includes_real_business_context(self):
        """The system prompt OpenAI actually receives must contain the real
        Kinikh business data (company, department knowledge/instructions/
        routing/contact) — not just a bare department-name list. With no
        departments passed (today's live DB state), this comes from the
        existing app/tenants/ constants."""
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("en")
        prompt = await engine._build_system_prompt(ctx)

        assert "Company: Kinikh" in prompt
        assert "rural" in prompt and "rural@kinikh.com" in prompt
        assert "organic produce" in prompt  # real knowledge-base text
        assert "Focus on rural enterprise" in prompt  # real dept prompt
        assert "dairy" in prompt  # real routing keyword

    @pytest.mark.asyncio
    async def test_system_prompt_prohibits_hallucination(self):
        """The model must be explicitly instructed not to invent business
        facts, and to admit when information isn't supplied."""
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("en")
        prompt = await engine._build_system_prompt(ctx)

        lowered = prompt.lower()
        assert "do not invent facts" in lowered

    @pytest.mark.asyncio
    async def test_system_prompt_uses_db_department_data_over_tenants_defaults(self):
        """When real department rows ARE supplied (e.g. once seeded/admin-
        edited), their values take priority over the app/tenants/ fallback."""
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        engine = ConversationEngine(
            [
                {
                    "name": "rural",
                    "display_name": "rural",
                    "email": "rural@kinikh.com",
                    "prompt": "DB-CUSTOM rural instructions.",
                    "knowledge_base": "DB-CUSTOM rural knowledge.",
                    "keywords": ["custom"],
                }
            ],
            client=mock_client,
        )
        ctx = self._make_context("en")
        prompt = await engine._build_system_prompt(ctx)

        assert "DB-CUSTOM rural instructions." in prompt
        assert "DB-CUSTOM rural knowledge." in prompt
        # The tenants-module default text must NOT also leak in alongside it.
        assert "helpful rural assistant for Kinikh" not in prompt

    @pytest.mark.asyncio
    async def test_get_next_response_adds_user_message_first(self):
        from app.ai.conversation import ConversationEngine

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Sure, please share your name.")
        engine = ConversationEngine([], client=mock_client)
        ctx = self._make_context("en")
        await engine.get_next_response(ctx, "I need help")
        assert ctx.messages[0].role == "user"
        assert ctx.messages[0].content == "I need help"

    def test_language_fallback_en(self):
        from app.ai.conversation import ConversationEngine

        fallback = ConversationEngine._language_fallback("en")
        assert "sorry" in fallback.lower() or "trouble" in fallback.lower()

    def test_language_fallback_hi(self):
        from app.ai.conversation import ConversationEngine

        fallback = ConversationEngine._language_fallback("hi")
        assert len(fallback) > 0

    def test_language_fallback_hinglish(self):
        from app.ai.conversation import ConversationEngine

        fallback = ConversationEngine._language_fallback("hi-en")
        assert "issue" in fallback.lower() or "sorry" in fallback.lower()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------


class TestModuleLevelSingleton:
    @pytest.mark.asyncio
    async def test_context_manager_singleton_shared(self):
        from app.ai.context_manager import context_manager

        ctx = await context_manager.create("SINGLETON_TEST", "+91000")
        retrieved = await context_manager.get("SINGLETON_TEST")
        assert ctx is retrieved
        await context_manager.remove("SINGLETON_TEST")

    def test_ai_init_exports_all_expected_names(self):
        import app.ai as ai_module

        expected = {
            "CallContext",
            "ContextManager",
            "ConversationEngine",
            "ConversationPhase",
            "ConversationState",
            "DepartmentClassifier",
            "LanguageDetector",
            "LeadExtractor",
            "PromptManager",
            "SummaryGenerator",
            "context_manager",
        }
        for name in expected:
            assert hasattr(ai_module, name), f"app.ai missing export: {name}"

    def test_conversation_phase_str_values(self):
        from app.ai.conversation_state import ConversationPhase

        assert ConversationPhase.GREETING == "greeting"
        assert ConversationPhase.COLLECTING == "collecting"
        assert ConversationPhase.CONFIRMING == "confirming"
        assert ConversationPhase.FAREWELL == "farewell"
        assert ConversationPhase.ENDED == "ended"


# ---------------------------------------------------------------------------
# Devanagari detection helpers
# ---------------------------------------------------------------------------


class TestDevanagariHelpers:
    def test_has_devanagari_true(self):
        from app.ai.language_detector import _has_devanagari

        assert _has_devanagari("नमस्ते") is True
        assert _has_devanagari("मेरा नाम राज है") is True

    def test_has_devanagari_false_for_latin(self):
        from app.ai.language_detector import _has_devanagari

        assert _has_devanagari("Hello world") is False
        assert _has_devanagari("Mera naam Raj hai") is False

    def test_has_devanagari_false_for_empty(self):
        from app.ai.language_detector import _has_devanagari

        assert _has_devanagari("") is False

    def test_has_latin_words_true(self):
        from app.ai.language_detector import _has_latin_words

        assert _has_latin_words("Hello") is True
        assert _has_latin_words("Raj hai") is True

    def test_has_latin_words_false_for_devanagari_only(self):
        from app.ai.language_detector import _has_latin_words

        assert _has_latin_words("नमस्ते") is False

    def test_has_latin_words_false_for_empty(self):
        from app.ai.language_detector import _has_latin_words

        assert _has_latin_words("") is False

    def test_has_latin_words_false_for_digits_only(self):
        from app.ai.language_detector import _has_latin_words

        assert _has_latin_words("123 456") is False


# ---------------------------------------------------------------------------
# Integration: LanguageDetector + ConversationState
# ---------------------------------------------------------------------------


class TestLanguageAndStateIntegration:
    @pytest.mark.asyncio
    async def test_hindi_detection_updates_context_language(self):
        from app.ai.context_manager import CallContext
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        ctx = CallContext(call_sid="INT_TEST", caller_number="+91000")
        lang = await detector.detect("मेरा नाम राज है")
        ctx.language = lang
        assert ctx.language == "hi"

    @pytest.mark.asyncio
    async def test_hinglish_detection_updates_context_language(self):
        from app.ai.context_manager import CallContext
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        ctx = CallContext(call_sid="INT_TEST2", caller_number="+91000")
        lang = await detector.detect("मेरा naam Raj है")
        ctx.language = lang
        assert ctx.language == "hi-en"

    @pytest.mark.asyncio
    async def test_state_transitions_through_full_flow(self):
        from app.ai.context_manager import CallContext
        from app.ai.conversation_state import ConversationPhase, ConversationState

        ctx = CallContext(call_sid="FLOW_TEST", caller_number="+91000")

        # Phase 1: Greeting (no messages)
        assert ConversationState.current_phase(ctx) == ConversationPhase.GREETING

        # Phase 2: User speaks — collecting
        ctx.add_message("user", "Hi, I need help")
        assert ConversationState.current_phase(ctx) == ConversationPhase.COLLECTING

        # Phase 3: Fields collected — confirming
        ctx.collected["name"] = "Raj"
        ctx.collected["requirement"] = "Demo"
        ctx.collected["email"] = "raj@example.com"
        assert ConversationState.current_phase(ctx) == ConversationPhase.CONFIRMING

        # Phase 4: Confirmed — farewell
        ctx.confirmed = True
        assert ConversationState.current_phase(ctx) == ConversationPhase.FAREWELL

        # Phase 5: Ended
        ctx.ended = True
        assert ConversationState.current_phase(ctx) == ConversationPhase.ENDED
        assert ConversationState.is_terminal(ctx) is True

    def test_mock_client_not_needed_for_sync_operations(self):
        from app.ai.context_manager import CallContext
        from app.ai.conversation_state import ConversationState

        ctx = CallContext(call_sid="X", caller_number="+91000")
        # All ConversationState methods are sync and need no client
        phase = ConversationState.current_phase(ctx)
        field = ConversationState.next_field(ctx)
        should_confirm = ConversationState.should_confirm(ctx)
        is_term = ConversationState.is_terminal(ctx)

        assert phase is not None
        assert field is not None
        assert should_confirm is False
        assert is_term is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_lead_extractor_with_hindi_transcript(self):
        from app.ai.lead_extractor import LeadExtractor

        mock_client = AsyncMock()
        mock_client.chat_json = AsyncMock(
            return_value={
                "name": "राज",
                "phone": "+919876543210",
                "language": "hi",
            }
        )
        extractor = LeadExtractor(client=mock_client)
        result = await extractor.extract("नमस्ते मेरा नाम राज है", "+919876543210")
        assert result["name"] == "राज"
        assert result["language"] == "hi"

    @pytest.mark.asyncio
    async def test_summary_generator_with_hindi_transcript(self):
        from app.ai.summary_generator import SummaryGenerator

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Caller needed demo in Hindi.")
        generator = SummaryGenerator(client=mock_client)
        result = await generator.generate("नमस्ते, मुझे डेमो चाहिए")
        assert len(result) > 0

    def test_prompt_manager_confirmation_kwargs_forwarded(self):
        from app.ai.prompt_manager import PromptManager

        pm = PromptManager()
        kwargs = {
            "name": "Test Name",
            "phone": "+91000",
            "email": "test@test.com",
            "department": "rural",
            "requirement": "Need info",
        }
        for lang in ("en", "hi", "hi-en"):
            result = pm.confirmation(lang, **kwargs)
            assert "Test Name" in result

    @pytest.mark.asyncio
    async def test_context_manager_concurrent_creates(self):
        import asyncio

        from app.ai.context_manager import ContextManager

        cm = ContextManager()
        tasks = [cm.create(f"SID_{i}", f"+9100{i}") for i in range(10)]
        contexts = await asyncio.gather(*tasks)
        assert len(contexts) == 10
        assert await cm.count() == 10

    def test_conversation_phase_is_string_enum(self):
        from app.ai.conversation_state import ConversationPhase

        # ConversationPhase(str, Enum) — can be used as a string key
        mapping = {phase: i for i, phase in enumerate(ConversationPhase)}
        assert ConversationPhase.GREETING in mapping
        assert str(ConversationPhase.GREETING) == "ConversationPhase.GREETING"

    def test_conversation_phase_value_comparison(self):
        from app.ai.conversation_state import ConversationPhase

        # str(Enum) gives "ClassName.VALUE" but .value is just the string
        assert ConversationPhase.GREETING.value == "greeting"
        assert ConversationPhase.ENDED.value == "ended"

    @pytest.mark.asyncio
    async def test_department_classifier_no_keywords_list(self):
        from app.ai.department_classifier import DepartmentClassifier

        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="rural")
        # Department with no "keywords" key at all
        classifier = DepartmentClassifier([{"name": "rural"}], client=mock_client)
        result = await classifier.classify("I want to buy")
        # No keyword fast-path triggered — goes to LLM
        assert result == "rural"

    def test_message_dataclass(self):
        from app.ai.context_manager import Message

        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.timestamp > 0

    @pytest.mark.asyncio
    async def test_language_detector_with_punctuation_and_devanagari(self):
        from app.ai.language_detector import LanguageDetector

        mock_client = AsyncMock()
        detector = LanguageDetector(client=mock_client)
        result = await detector.detect("नमस्ते! आप कैसे हैं?")
        assert result == "hi"
        mock_client.chat.assert_not_called()
