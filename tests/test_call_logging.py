"""Tests for the structured, safe diagnostic call-lifecycle logging added
while diagnosing the first real end-to-end Exotel call test."""

from __future__ import annotations

import json
from unittest.mock import patch

from app.core.call_logging import log_call_event, mask_phone


class TestMaskPhone:
    def test_masks_middle_digits_keeps_prefix_and_last_two(self):
        assert mask_phone("+919876543210") == "+91********10"

    def test_short_number_fully_masked(self):
        assert mask_phone("1234") == "****"

    def test_none_returns_unknown(self):
        assert mask_phone(None) == "unknown"

    def test_empty_string_returns_unknown(self):
        assert mask_phone("") == "unknown"

    def test_never_returns_the_original_full_number_for_a_real_length_number(self):
        number = "+919876543210"
        masked = mask_phone(number)
        assert masked != number
        assert number[3:-2] not in masked


class TestLogCallEvent:
    def test_emits_valid_json_with_event_name(self):
        with patch("app.core.call_logging.logger") as mock_logger:
            log_call_event("call.created", call_sid="CA123")

        logged_line = mock_logger.info.call_args[0][0]
        parsed = json.loads(logged_line)
        assert parsed["event"] == "call.created"
        assert parsed["call_sid"] == "CA123"

    def test_arbitrary_fields_are_included(self):
        with patch("app.core.call_logging.logger") as mock_logger:
            log_call_event("ai.response", call_sid="CA123", status="ok", reply_length=42)

        parsed = json.loads(mock_logger.info.call_args[0][0])
        assert parsed == {
            "event": "ai.response",
            "call_sid": "CA123",
            "status": "ok",
            "reply_length": 42,
        }

    def test_non_json_native_values_are_stringified_not_raise(self):
        """default=str must prevent a call-lifecycle log from ever crashing
        the request just because a field (e.g. a UUID) isn't JSON-native."""
        import uuid

        with patch("app.core.call_logging.logger") as mock_logger:
            log_call_event("call.persistence.completed", call_sid="CA123", lead_id=uuid.uuid4())

        parsed = json.loads(mock_logger.info.call_args[0][0])
        assert parsed["event"] == "call.persistence.completed"
        assert isinstance(parsed["lead_id"], str)
