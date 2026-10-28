"""Department classification — keyword fast-path then OpenAI fallback."""

from __future__ import annotations

import json

from loguru import logger

from app.ai.openai_client import OpenAIClient, get_openai_client
from app.ai.prompt_manager import PromptManager
from app.core.call_logging import describe_error
from app.core.config import get_settings


class DepartmentClassifier:
    """Classifies caller requirement into a configured department."""

    def __init__(
        self,
        departments: list[dict],  # type: ignore[type-arg]
        client: OpenAIClient | None = None,
    ) -> None:
        self._client = client or get_openai_client()
        self._departments = departments
        self._prompt_manager = PromptManager()
        self._company = get_settings().company_name

    def _keyword_match(self, requirement: str) -> str | None:
        """Return department name on keyword match, or None."""
        lower = requirement.lower()
        for dept in self._departments:
            for kw in dept.get("keywords", []):
                if isinstance(kw, str) and kw.lower() in lower:
                    return dept["name"]  # type: ignore[no-any-return]
        return None

    async def classify(self, requirement: str) -> str | None:
        """Return the matching department name, or None if unclassifiable."""
        # The requirement is the caller's own words — only its size is logged.
        logger.info(f"[CLASSIFIER] input_chars={len(requirement)}")
        if not requirement.strip():
            logger.info("[CLASSIFIER] result=None reason=empty_input")
            return None

        # Fast path: keyword matching avoids an LLM call for common cases.
        matched = self._keyword_match(requirement)
        if matched is not None:
            logger.info(f"[CLASSIFIER] result={matched!r} reason=keyword_match")
            return matched

        dept_json = json.dumps(
            [{"name": d["name"], "keywords": d.get("keywords", [])} for d in self._departments],
            ensure_ascii=False,
        )
        prompt = self._prompt_manager.department_classification(
            company_name=self._company,
            departments_json=dept_json,
            requirement=requirement,
        )

        logger.info("[AI_CALL] purpose=department_classification")
        try:
            name = await self._client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=50,
            )
            name = name.strip().strip('"\'.').lower()
            # Return the canonical (configured) spelling — DB lookups by
            # name are exact-match, so a lower-cased "Sales" never matched.
            valid_names = {str(d["name"]).lower(): str(d["name"]) for d in self._departments}
            if name in valid_names:
                logger.info(f"[CLASSIFIER] result={name!r} reason=llm_classified")
                return valid_names[name]
            logger.warning(f"[classifier] unknown department returned chars={len(name)}")
            logger.info("[CLASSIFIER] result=None reason=llm_returned_unknown")
            return None
        except Exception as exc:
            logger.error(f"[classifier] classification error: {describe_error(exc)}")
            logger.info("[CLASSIFIER] result=None reason=error")
            return None
