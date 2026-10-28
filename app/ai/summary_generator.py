"""Call summary generation using OpenAI."""

from __future__ import annotations

from loguru import logger

from app.ai.openai_client import OpenAIClient, get_openai_client
from app.ai.prompt_manager import PromptManager
from app.core.call_logging import describe_error


class SummaryGenerator:
    """Generates a 2-3 sentence English summary of a call transcript."""

    def __init__(self, client: OpenAIClient | None = None) -> None:
        self._client = client or get_openai_client()
        self._prompt_manager = PromptManager()

    async def generate(self, transcript: str) -> str:
        if not transcript.strip():
            return "No transcript available."

        prompt = self._prompt_manager.summary(transcript)
        logger.info("[AI_CALL] purpose=summary")
        try:
            return await self._client.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
        except Exception as exc:
            logger.error(f"[summary] generation error: {describe_error(exc)}")
            return "Summary unavailable."
