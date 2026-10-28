"""Prompt manager — renders prompt templates with runtime variables."""

from __future__ import annotations

from typing import Any

from app.ai.language_profile import LANGUAGE_NAMES, UNKNOWN
from app.prompts.system import (
    CONFIRMATION_PROMPT_EN,
    CONFIRMATION_PROMPT_HI,
    CONFIRMATION_PROMPT_HI_EN,
    CONVERSATION_QUESTIONS,
    DEPARTMENT_CLASSIFICATION_PROMPT,
    FAREWELL_EN,
    FAREWELL_HI,
    FAREWELL_HI_EN,
    GREETING_PROMPT,
    LANGUAGE_DETECTION_PROMPT,
    LEAD_EXTRACTION_PROMPT,
    MANAGEMENT_TRANSFER_AVAILABLE_RULE,
    MANAGEMENT_TRANSFER_UNAVAILABLE_RULE,
    MISSED_THAT_EN,
    MISSED_THAT_HI,
    MISSED_THAT_HI_EN,
    SILENCE_GOODBYE_EN,
    SILENCE_GOODBYE_HI,
    SILENCE_GOODBYE_HI_EN,
    STILL_THERE_EN,
    STILL_THERE_HI,
    STILL_THERE_HI_EN,
    SUMMARY_PROMPT,
)


def _caller_id_instruction(caller_number: str) -> str:
    digits = "".join(c for c in caller_number or "" if c.isdigit())
    if len(digits) >= 6:
        last4 = ", ".join(digits[-4:])
        return (
            f"the caller is calling from a number ending in {last4}. Ask whether this number "
            "is the best one to call them back on, saying only those last four digits one by "
            "one. If they give a different number, repeat it back in small groups of digits "
            "to confirm."
        )
    return (
        "the caller's number is not available. Ask for the best number to call them back "
        "on and repeat it back in small groups of digits to confirm."
    )


_RESPONSE_LANGUAGE_DIRECTIVES = {
    "en": (
        "Reply in conversational spoken English, even if the caller uses a few Hindi words."
    ),
    "hi": (
        "Speak in everyday spoken Hindi in Devanagari script, mixed with common English "
        "words exactly as described below."
    ),
    "hi-en": (
        "Speak in natural Hinglish (Hindi in Roman letters, mixed with common English "
        "business words), the way a friendly receptionist in India talks."
    ),
}
_UNKNOWN_LANGUAGE_DIRECTIVE = (
    "The caller's language is not known yet. Reply in the language the caller is using. "
    "It may be English, Hindi or Hinglish. If their message gives no clue (just a greeting or "
    "\"haan\"/\"okay\"), keep the reply short and simple."
)


def _language_label(code: str, *, short_turn: bool = False) -> str:
    if code in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[code]
    return "unclear (short reply or no words yet)" if short_turn else "not known yet"


def _language_instruction(primary: str, current_turn: str, response: str) -> str:
    directive = _RESPONSE_LANGUAGE_DIRECTIVES.get(response, _UNKNOWN_LANGUAGE_DIRECTIVE)
    return (
        f"PRIMARY CALL LANGUAGE: {_language_label(primary)}\n"
        f"CURRENT CALLER TURN LANGUAGE: {_language_label(current_turn, short_turn=True)}\n"
        f"RESPONSE LANGUAGE: {_language_label(response)}\n"
        f"{directive}"
    )


TRANSFER_AVAILABLE_INSTRUCTION = (
    "Confirm before transferring: \"Your enquiry is regarding <service>. Would you like me to connect you to <team> now?\" "
    "If the caller asks to \"connect to the team\" without specifying a service, ask ONE question: \"Which service is this about?\" "
    "After they confirm, say \"Please hold while I try to connect you to <team>.\" then output [TRANSFER:<key>] at the end. "
    "The <key> must ONLY be one of the following 11 keys: rural, stay, tech, brand, infra, land, story, ngo, accounts, support, ea. "
    "Map spoken synonyms to these keys (e.g. dairy/doodh->rural, hotel->stay, website/app->tech, marketing/ads->brand, construction->infra, plot->land, documentary->story, ngo/education->ngo, bill/payment->accounts, unknown/complaint/job->support, founder->ea). "
    "Never say \"connected\" yourself. Never use the marker in any other reply. A transfer reply ends with this "
    "marker instead of [END_CALL]."
)
TRANSFER_UNAVAILABLE_INSTRUCTION = (
    "Talking to a person is not available right now (outside office hours). If the caller "
    "asks, say the team is not available at the moment and offer a callback; never promise "
    "a transfer."
)


class PromptManager:
    """Renders all externalized prompts — no prompt text lives outside this module."""

    def greeting(
        self,
        company_name: str,
        business_context: str,
        *,
        caller_number: str = "",
        current_time: str = "",
        language: str = "en",
        language_locked: bool = False,
        transfer_available: bool = False,
        primary_language: str | None = None,
        current_turn_language: str | None = None,
        response_language: str | None = None,
    ) -> str:
        if response_language not in LANGUAGE_NAMES:
            response_language = language if language_locked else UNKNOWN
        if primary_language is None:
            primary_language = language if language_locked else UNKNOWN
        if current_turn_language is None:
            current_turn_language = UNKNOWN
        language_instruction = _language_instruction(
            primary_language, current_turn_language, response_language
        )

        return GREETING_PROMPT.format(
            company_name=company_name,
            business_context=business_context,
            caller_id_instruction=_caller_id_instruction(caller_number),
            current_time=current_time or "unknown",
            language_instruction=language_instruction,
            management_transfer_instruction=(
                MANAGEMENT_TRANSFER_AVAILABLE_RULE
                if transfer_available
                else MANAGEMENT_TRANSFER_UNAVAILABLE_RULE
            ),
            transfer_instruction=(
                TRANSFER_AVAILABLE_INSTRUCTION
                if transfer_available
                else TRANSFER_UNAVAILABLE_INSTRUCTION
            ),
        )

    def lead_extraction(
        self, transcript: str, caller_phone: str, department_names: str = "any suitable name"
    ) -> str:
        return LEAD_EXTRACTION_PROMPT.format(
            transcript=transcript, caller_phone=caller_phone, department_names=department_names
        )

    def still_there(self, language: str) -> str:
        if language == "hi":
            return STILL_THERE_HI
        if language == "hi-en":
            return STILL_THERE_HI_EN
        return STILL_THERE_EN

    def silence_goodbye(self, language: str, company_name: str) -> str:
        if language == "hi":
            template = SILENCE_GOODBYE_HI
        elif language == "hi-en":
            template = SILENCE_GOODBYE_HI_EN
        else:
            template = SILENCE_GOODBYE_EN
        return template.format(company_name=company_name)

    def department_classification(
        self, company_name: str, departments_json: str, requirement: str
    ) -> str:
        return DEPARTMENT_CLASSIFICATION_PROMPT.format(
            company_name=company_name,
            departments_json=departments_json,
            requirement=requirement,
        )

    def language_detection(self, text: str) -> str:
        return LANGUAGE_DETECTION_PROMPT.format(text=text)

    def summary(self, transcript: str) -> str:
        return SUMMARY_PROMPT.format(transcript=transcript)

    def question(self, field: str, language: str) -> str:
        for q in CONVERSATION_QUESTIONS:
            if q["field"] == field:
                if language == "hi":
                    return q.get("hi", q["en"])
                if language == "hi-en":
                    return q.get("hi-en", q.get("hi", q["en"]))
                return q.get("en", "")
        return ""

    def confirmation(self, language: str, **kwargs: Any) -> str:
        if language == "hi":
            template = CONFIRMATION_PROMPT_HI
        elif language == "hi-en":
            template = CONFIRMATION_PROMPT_HI_EN
        else:
            template = CONFIRMATION_PROMPT_EN
        return template.format(**kwargs)

    def farewell(self, language: str, company_name: str) -> str:
        if language == "hi":
            template = FAREWELL_HI
        elif language == "hi-en":
            template = FAREWELL_HI_EN
        else:
            template = FAREWELL_EN
        return template.format(company_name=company_name)

    def missed_that(self, language: str) -> str:
        if language == "hi":
            return MISSED_THAT_HI
        if language == "hi-en":
            return MISSED_THAT_HI_EN
        return MISSED_THAT_EN
