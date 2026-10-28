"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
import ipaddress
import json
from typing import Any, Literal

from pydantic import EmailStr, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Safe, obviously-fake values used ONLY when APP_ENV=development and the real
# variable is not set. This lets the app boot (docs, DB, routing, mocked
# pipelines) without any real external credentials. Never used in production —
# see `_fill_dev_defaults` below, which only fires when app_env is development.
_DEV_MOCK_DEFAULTS: dict[str, str] = {
    "secret_key": "dev-insecure-secret-key-do-not-use-in-production-0000000000",
    "openai_api_key": "sk-dev-mock-not-a-real-key",
    "elevenlabs_api_key": "el-dev-mock-not-a-real-key",
    "elevenlabs_voice_id_en": "dev-mock-voice-en",
    "elevenlabs_voice_id_hi": "dev-mock-voice-hi",
    "exotel_sid": "dev-mock-sid",
    "exotel_token": "dev-mock-token",
    "exotel_api_key": "dev-mock-api-key",
    "exotel_caller_id": "+910000000000",
    "exotel_webhook_secret": "dev-mock-webhook-secret",
    "smtp_username": "dev@localhost",
    "smtp_password": "dev-mock-password",
    "smtp_from_email": "dev-mock@example.com",
    "jwt_secret_key": "dev-insecure-jwt-secret-key-do-not-use-in-production-000",
    "admin_email": "dev-admin@example.com",
    # bcrypt hash of "dev-only-changeme" — dev/mock mode only.
    "admin_password_hash": "$2b$12$2OTwnBBhKsYAHfxDIf3pS.GiG1yKl5a1o9/eU.HDJsa9YvZ0TNwBu",
    "stream_token_secret": "dev-mock-stream-token-secret-00000000000000",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = "Kinikh IVR"
    app_env: str = "production"
    app_debug: bool = False
    app_host: str = "0.0.0.0"  # noqa: S104
    app_port: int = 8000
    secret_key: str = Field(..., min_length=32)

    # Database
    database_url: str = Field(..., description="Async PostgreSQL URL")
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 20

    # OpenAI
    openai_api_key: str = Field(..., description="OpenAI API key")
    openai_model: str = "gpt-4o"
    # Live receptionist replies can use a faster model independently of
    # post-call lead extraction/summaries. Empty inherits OPENAI_MODEL.
    openai_live_model: str = ""
    openai_realtime_model: str = "gpt-4o-realtime-preview"
    openai_temperature: float = 0.2
    openai_max_tokens: int = 1024
    # Live replies use the smallest supported reasoning budget. Post-call
    # summary/lead extraction deliberately omit this override.
    openai_reasoning_effort: str = "minimal"
    # Whether SpeechHandler speaks a greeting immediately on 'start', before
    # any caller speech. This is the receptionist's intended, documented
    # behavior (see PromptManager/ConversationEngine) — not a bug — but is
    # made explicitly configurable so it can be disabled for debugging an
    # audio-loop/premature-response issue without touching the AI pipeline.
    ai_auto_greeting: bool = True
    tts_streaming_enabled: bool = True
    # Minimum PCM16 RMS amplitude (0-32767 scale) a flushed audio buffer must
    # have before it is sent to Whisper. Real PSTN calls showed Whisper
    # hallucinating filler transcripts ("you", "thank you", "bye-bye") when
    # handed near-silent audio (line noise / faint TTS bleed-back) — this
    # rejects that audio before STT ever sees it. Genuine speech is far
    # louder than typical PSTN line noise/comfort noise, so this does not
    # block real caller speech, including speech that arrives while TTS is
    # playing (barge-in). See app/voice/vad.py.
    stt_min_rms_energy: float = 150.0
    # Barge-in detector — used ONLY while our own TTS is playing (see
    # app/voice/vad.py:BargeInDetector). A real PSTN call showed that
    # blob-level RMS over several accumulated seconds cannot tell brief
    # genuine speech apart from TTS echo/line noise averaged together, so a
    # caller must sustain speech-level energy across this many consecutive
    # ~20ms frames (not just one loud packet) before we treat it as a real
    # interruption and cancel our own TTS. Deliberately a stricter/separate
    # threshold from stt_min_rms_energy, not a replacement for it.
    barge_in_frame_ms: int = 20
    barge_in_min_rms_energy: float = 600.0
    # 350 ms: a short "haan"/"hmm"/"ji"/"acha" while the assistant talks is a
    # backchannel, not an interruption — it never stops the reply.
    barge_in_min_active_ms: int = 350
    # Natural speech dips below the energy floor between syllables (stop
    # consonants, breaths). Without tolerance, one quiet 20ms frame reset the
    # sustained-speech streak, so ordinary syllabic caller speech during
    # playback could never accumulate barge_in_min_active_ms and was lost.
    # Sustained silence longer than this still resets the streak.
    barge_in_gap_tolerance_ms: int = 120
    # The first part of every assistant sentence is when echo of our own
    # voice is loudest (the line's echo canceller is still adapting). Caller
    # audio in this window is still captured, but cannot confirm a barge-in
    # on its own — the interruption has to continue past it.
    barge_in_grace_ms: int = Field(default=500, ge=0)
    # Outbound pacing: assistant audio is sent at most this far ahead of what
    # the caller has actually heard. Keeps Exotel's buffer (what `clear` must
    # discard on barge-in) small, stops a cancelled reply's later sentences
    # from ever leaving the server, and makes the history record only what
    # the caller could have heard. Does not delay the first audio.
    playback_max_lead_ms: int = Field(default=600, ge=200)
    # The next sentence of a multi-sentence reply is sent to TTS once the
    # audio still to be played drops to this much — enough to cover TTS
    # time-to-first-audio so there is no gap between sentences.
    tts_lookahead_ms: int = Field(default=600, ge=200)
    # Real-time primary turn detector (app/voice/vad.py:UtteranceSegmenter) —
    # replaces the old "accumulate up to max_utterance_bytes or 800ms of no
    # new pushes, then check RMS once" design. Exotel's 20ms media frames are
    # classified continuously using the SAME stt_min_rms_energy threshold
    # (never raised) but per-frame with a duration requirement, so a short
    # "Hello" isn't diluted by silence padding the way one blob-level
    # average over several seconds was.
    # Live calls showed both false positives on brief noise and short
    # code-switched Hindi/English pauses ("मुझे... billing department से...")
    # occasionally splitting one utterance in two. Raising speech_start_ms
    # costs nothing (the pre-roll buffer captures the lead-in regardless of
    # how many frames it took to confirm), and a modest — not "significant"
    # — bump to end_of_speech_ms tolerates natural bilingual pauses better.
    speech_start_ms: int = 200  # sustained active frames required to confirm speech began
    end_of_speech_ms: int = 900  # sustained silent frames required to confirm the caller stopped
    # A transcript that sounds unfinished ("... chahiye aur", "so,", a lone
    # "haan") gets this much more time for the caller to continue before we
    # reply; if they do, both parts are answered as one turn. 0 disables.
    unfinished_turn_extra_ms: int = Field(default=1200, ge=0)
    pre_roll_ms: int = 250  # audio kept just before speech-start so the first word isn't clipped
    # Safety cap only — not the primary turn boundary. Prevents unbounded
    # memory if a line never goes silent; deliberately generous (~20s) so it
    # essentially never fires during a normal caller sentence.
    max_utterance_bytes: int = 320_000
    # Development-only diagnostic: when true, every finalized utterance's
    # exact WAV payload (the bytes actually sent to Whisper) is saved to
    # debug_audio/call_<call_sid>_turn_<NN>.wav, so a real call's audio
    # accuracy question ("what did Whisper actually hear?") can be answered
    # by listening, not guessing. Never enable in production; files are
    # never committed (see .gitignore).
    voice_debug_audio: bool = False
    # Live-call provider deadlines. The shared OpenAI client allows 30s plus
    # retries, which is fine for post-call work (summary, lead extraction) but
    # leaves a caller in dead air for the live path. Past these limits the
    # caller hears a short spoken fallback instead.
    stt_timeout_seconds: float = Field(default=8.0, gt=0)
    llm_first_token_timeout_seconds: float = Field(default=8.0, gt=0)
    # Adaptive noise gate (app/voice/vad.py:NoiseFloor). Caller speech must
    # be NOISE_FLOOR_MULTIPLIER x the line's measured background level (and
    # never less than the fixed thresholds above). Stops background noise
    # from being transcribed/answered or interrupting the assistant on
    # noisy lines. 0 disables it (fixed thresholds only).
    # If the caller says nothing for this long after the assistant finishes
    # speaking, ask "are you still there?"; after MAX_SILENCE_REPROMPTS
    # unanswered prompts, say goodbye and end the call. 0 disables.
    # How many times a not-yet-audible reply may be abandoned because the
    # caller started speaking again (their pause was mid-sentence). After
    # this, the reply is kept so a caller who keeps talking still gets one.
    max_resume_merges: int = Field(default=1, ge=0)
    silence_reprompt_seconds: float = Field(default=8.0, ge=0)
    max_silence_reprompts: int = Field(default=2, ge=0)
    noise_floor_multiplier: float = Field(default=3.0, ge=0)
    noise_gate_max_rms: float = Field(default=2000.0, gt=0)
    
    # Language Stability (see app/ai/language_profile.py)
    # Content words (excluding acknowledgements like "haan"/"okay") a turn
    # needs before it can set or switch the language.
    language_min_words: int = Field(default=3, ge=1)
    # Once the call language is set, a caller turn switches it only with at
    # least this many content words, this share of them in the other
    # language, or an explicit request ("Hindi mein baat karo").
    language_switch_min_words: int = Field(default=4, ge=1)
    language_switch_ratio: float = Field(default=0.7, gt=0, le=1)
    # Extra comma-separated acknowledgement words/phrases that never change
    # the call language, on top of the built-in list.
    language_extra_acknowledgements: str = ""

    # Text normalization right before TTS (app/voice/tts_text.py):
    # ";"-separated "from=to" whole-word, case-sensitive replacements,
    # applied in order. The _HI map is applied first for hi / hi-en speech.
    tts_pronunciations: str = (
        "KINIKH=Kinikh;Kinikh=किनिख;evolKun=evol-kun;KRIL=K R I L;"
        "BrandWorks=Brand Works;Landcraft=Land craft"
    )
    tts_pronunciations_hi: str = "Kinikh=किनिख;KINIKH=किनिख"
    # ALL-CAPS words (4+ letters) are spoken as words, not spelled out,
    # except these acronyms.
    tts_keep_acronyms: str = "CRM,ERP,MIS,GST,HR,EA,NGO"

    # Speech-style (accent) detection — OPTIONAL, for TTS personalisation
    # only (app/voice/accent_detector.py). Never on the response critical
    # path. Off unless ACCENT_DETECTION_ENABLED=true; even then the default
    # mode only logs. Modes: disabled | log_only | adaptive_tts.
    accent_detection_enabled: bool = False
    accent_detection_mode: str = "log_only"
    # Registered acoustic detector to use. "none" = no model: every result
    # is neutral_indian (a real model must be selected and registered).
    accent_detector_provider: str = "none"
    # Voiced caller speech accumulated before each analysis window.
    accent_min_audio_ms: int = Field(default=4000, ge=1000)
    accent_timeout_ms: int = Field(default=1500, ge=50)
    # Lowest detector confidence that counts as evidence: low|medium|high.
    accent_min_confidence: str = "high"
    # Consecutive agreeing qualifying detections before a style is applied.
    accent_min_evidence: int = Field(default=2, ge=1)
    accent_max_analyses: int = Field(default=3, ge=1)
    # Styles the detector may report (anything else is treated as unknown).
    accent_styles: str = (
        "neutral_indian,gujarati_influenced,punjabi_influenced,bengali_influenced,"
        "marathi_influenced,south_indian_influenced"
    )
    
    # Speech-to-text. gpt-4o-transcribe / gpt-4o-mini-transcribe are far
    # more accurate than whisper-1 on 8 kHz phone audio, Hindi/Hinglish and
    # noisy lines, and hallucinate much less on non-speech. whisper-1 is
    # used automatically if the configured model is rejected.
    stt_model: str = "gpt-4o-transcribe"
    stt_fallback_model: str = "whisper-1"
    # Public base URL of this backend (e.g. https://ivr.yourdomain.com).
    # When set, the Voicebot resolver builds the media WebSocket URL from it
    # instead of guessing from request headers — correct behind any
    # tunnel/proxy. Leave empty to derive from the request.
    public_base_url: str = ""

    # Text-to-speech provider for live calls: "elevenlabs" |
    # "openai". OpenAI TTS is the automatic fallback for ElevenLabs.
    tts_provider: str = "elevenlabs"

    # ElevenLabs — only used when TTS_PROVIDER=elevenlabs, so the app starts
    # without these.
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id_en: str = ""
    elevenlabs_voice_id_hi: str = ""
    elevenlabs_model_id: str = "eleven_multilingual_v2"
    # voice_settings: slightly lower stability = more natural intonation.
    elevenlabs_stability: float = Field(default=0.45, ge=0, le=1)
    elevenlabs_similarity: float = Field(default=0.8, ge=0, le=1)
    elevenlabs_style: float = Field(default=0.15, ge=0, le=1)
    elevenlabs_speed: float = Field(default=1.0, ge=0.7, le=1.2)
    # Optional curated voices per (language, speech style). Empty = not
    # configured; selection falls back to the neutral voice for the language,
    # then to ELEVENLABS_VOICE_ID_EN. Neutral EN/HI default to the IDs above;
    # neutral Hinglish defaults to the Hindi voice (existing behaviour).
    elevenlabs_voice_en_neutral: str = ""
    elevenlabs_voice_hi_neutral: str = ""
    elevenlabs_voice_hinglish_neutral: str = ""
    elevenlabs_voice_en_gujarati: str = ""
    elevenlabs_voice_hinglish_gujarati: str = ""
    elevenlabs_voice_en_punjabi: str = ""
    elevenlabs_voice_hinglish_punjabi: str = ""

    # Exotel — standard REST API auth is Account SID + API Key (username) +
    # API Token (password) over Basic Auth, per Exotel's documented
    # `https://<api_key>:<api_token>@<subdomain>/v1/Accounts/<sid>/...`.
    exotel_sid: str = Field(..., description="Exotel Account SID")
    exotel_api_key: str = Field(..., description="Exotel API key (Basic Auth username)")
    exotel_token: str = Field(..., description="Exotel API token (Basic Auth password)")
    exotel_subdomain: str = "api.exotel.com"
    # Outbound-calling only (CallerId on Calls/connect) — not needed for
    # inbound webhooks, REST auth, or the connection test. This project is
    # primarily an inbound receptionist; kept for when outbound calling is
    # wired up (see BaseTelephonyProvider.make_call).
    exotel_caller_id: str = Field(
        ..., description="Exotel ExoPhone caller ID (outbound calls only)"
    )
    # Application-side secret — NOT an Exotel credential. Exotel's standard
    # webhooks carry no signature of their own; this is used only if/when
    # our own HMAC check on incoming webhooks is wired up. Generate it
    # yourself (e.g. `openssl rand -hex 32`); never obtained from Exotel.
    exotel_webhook_secret: str = Field(..., description="App-generated webhook HMAC secret")
    # Exotel does not sign its webhooks, so a request is authenticated by one
    # of: our HMAC header (signing proxy), `?webhook_token=<secret>` in the URL
    # configured in the Exotel flow, or HTTP Basic auth whose password is the
    # secret (see ExotelAdapter.authenticate_webhook). Optionally also require
    # the source IP to be in Exotel's published egress ranges
    # (comma-separated CIDRs); empty disables the IP check.
    exotel_allowed_cidrs: str = ""
    # The stream URL is fetched by Exotel and connected within a second or
    # two; a short lifetime limits replay of a leaked URL.
    stream_token_ttl_seconds: int = Field(default=120, ge=30, le=3600)
    # Live call transfer (docs/call_transfer.md). When the assistant ends a
    # call with [TRANSFER:<area>], the Exotel flow's Passthru checks
    # /telephony/transfer-check and its Connect applet dials the number from
    # /telephony/transfer-number. TRANSFER_NUMBERS maps business areas to
    # numbers ("infrastructure=+91...,hospitality=+91..."); unknown or empty
    # areas use TRANSFER_DEFAULT_NUMBER. Outside TRANSFER_HOURS (Asia/Kolkata,
    # e.g. "Mon-Sat 10:00-19:00" or "Mon-Fri 10:00-19:00, Sat 10:00-14:00")
    # the assistant offers a callback instead.
    transfer_enabled: bool = True
    transfer_default_number: str = "+918958815375"
    transfer_numbers: str = "rural=+917088075375;stay=+918394945375;tech=+918650075375;brand=+919568365375;infra=+918449375375;land=+917500005375;story=+919837375375;ngo=+918011161199;accounts=+917618445375;support=+918958815375;ea=+919012345375"
    transfer_hours: str = "Mon-Sat 10:00-19:00"
    # Unused since stream auth moved to one-time DB sessions
    # (app/services/stream_session_service.py); optional so a missing value
    # no longer stops the app from starting.
    stream_token_secret: str | None = None
    stream_token_algorithm: str = "HS256"
    # Deprecated: earlier versions of this codebase incorrectly used this as
    # the Basic Auth password instead of `exotel_token`. No longer read by
    # any code — kept optional so existing `.env` files with it set still
    # load without error.
    exotel_api_secret: str | None = Field(
        default=None, description="Deprecated, unused — see exotel_token"
    )

    # SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = Field(..., description="SMTP username")
    smtp_password: str = Field(..., description="SMTP password")
    smtp_from_email: EmailStr = Field(..., description="Sender email")
    smtp_from_name: str = "Kinikh IVR"
    smtp_use_tls: bool = True

    # JWT
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # Admin login (single operator account — no multi-user auth yet)
    admin_email: EmailStr = Field(..., description="Admin login email")
    admin_password_hash: str = Field(..., description="Bcrypt hash of the admin password")

    # Rate Limiting
    rate_limit_calls_per_minute: int = 60
    rate_limit_burst: int = 10

    # Company
    company_name: str = "Kinikh"
    company_website: str = "https://kinikh.com"
    company_phone: str = ""

    # Call recording storage — audio files are stored under this directory;
    # CallRecording.audio_file_path is always a path relative to it, never
    # an absolute filesystem path (see app/core/media.py).
    recordings_dir: str = "storage/recordings"

    # RAG / company knowledge (app/rag/). Used today only by the local browser
    # test harness (/rag-test, /api/v1/dev/rag/*) — the live voice pipeline
    # does not call it yet.
    rag_knowledge_dir: str = "app/data/kinikh_group"
    # Cached chunk embeddings live here so the knowledge base is embedded once
    # (scripts/index_kinikh_knowledge.py), never per question.
    rag_index_dir: str = "storage/rag_index"
    rag_embedding_model: str = "text-embedding-3-small"
    rag_top_k: int = Field(default=4, ge=1, le=10)
    rag_mode: Literal["auto", "full", "retrieval", "off"] = "auto"
    rag_full_context_max_chars: int = 40000
    rag_query_timeout_ms: int = 400
    # "vector" | "lexical" | "hybrid" — see app/rag/retrieval.py.
    rag_retrieval_mode: str = "hybrid"
    # How much weight the lexical (keyword) score gets in hybrid mode.
    rag_hybrid_lexical_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    # Conversation window passed to retrieval and the LLM: the most recent N
    # user/assistant exchanges, each message cut to rag_history_max_chars.
    rag_history_max_turns: int = Field(default=3, ge=0, le=10)
    rag_history_max_chars: int = Field(default=600, ge=50)
    # Model override for RAG answers; empty = openai_model.
    rag_llm_model: str = ""
    # The dev RAG endpoints answer only requests that come straight from this
    # machine (loopback client, no proxy/tunnel headers), so a development
    # server exposed through a tunnel for Exotel does not publish them.
    rag_dev_local_only: bool = True

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    # Names of fields silently backfilled with mock values (dev mode only).
    mock_credentials_used: list[str] = Field(default_factory=list, exclude=True)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    @model_validator(mode="before")
    @classmethod
    def _fill_dev_mock_defaults(cls, data: Any) -> Any:
        """Allow booting without real external API keys when APP_ENV=development.

        Only fires for the mock/development environment — production still
        requires every credential to be explicitly set, so this can never
        mask a missing secret in a real deployment.
        """
        if not isinstance(data, dict):
            return data
        app_env = str(data.get("app_env", "production")).lower()
        if app_env not in ("development", "dev"):
            return data
        used: list[str] = list(data.get("mock_credentials_used") or [])
        for key, default in _DEV_MOCK_DEFAULTS.items():
            if not data.get(key):
                data[key] = default
                used.append(key)
        data.setdefault(
            "database_url",
            "postgresql+asyncpg://kinikh:password@localhost:5432/kinikh_ivr_dev",
        )
        data["mock_credentials_used"] = used
        return data

    @field_validator("accent_detection_mode")
    @classmethod
    def _validate_accent_detection_mode(cls, v: str) -> str:
        mode = v.strip().lower()
        if mode not in ("disabled", "log_only", "adaptive_tts"):
            raise ValueError("accent_detection_mode must be disabled, log_only or adaptive_tts")
        return mode

    @field_validator("accent_min_confidence")
    @classmethod
    def _validate_accent_min_confidence(cls, v: str) -> str:
        level = v.strip().lower()
        if level not in ("low", "medium", "high"):
            raise ValueError("accent_min_confidence must be low, medium or high")
        return level

    @field_validator("rag_retrieval_mode")
    @classmethod
    def _validate_rag_retrieval_mode(cls, v: str) -> str:
        mode = v.strip().lower()
        if mode not in ("vector", "lexical", "hybrid"):
            raise ValueError("rag_retrieval_mode must be vector, lexical or hybrid")
        return mode

    @field_validator("tts_provider")
    @classmethod
    def _validate_tts_provider(cls, v: str) -> str:
        provider = v.strip().lower()
        if provider not in ("elevenlabs", "openai"):
            raise ValueError("tts_provider must be elevenlabs or openai")
        return provider

    @field_validator("exotel_allowed_cidrs")
    @classmethod
    def _validate_allowed_cidrs(cls, v: str) -> str:
        for cidr in (part.strip() for part in v.split(",")):
            if cidr:
                ipaddress.ip_network(cidr, strict=False)  # raises ValueError on a typo
        return v

    @property
    def exotel_allowed_networks(self) -> list[IPNetwork]:
        return [
            ipaddress.ip_network(cidr, strict=False)
            for cidr in (part.strip() for part in self.exotel_allowed_cidrs.split(","))
            if cidr
        ]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in ("development", "dev")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.mock_credentials_used:
        from loguru import logger

        logger.warning(
            "APP_ENV=development: running with MOCK credentials for: "
            f"{', '.join(settings.mock_credentials_used)}. "
            "Do not use this mode in production."
        )
    return settings


def reload_settings() -> Settings:
    """Force the next `get_settings()` call to re-read `.env`/the process
    environment from scratch, instead of returning the cached instance.

    This is the one, explicit way application config is ever refreshed
    without a process restart — used by the integration connection-test
    flow (`app/services/integration_test_service.py`) so a credential fixed
    in `.env` is picked up by "Test Connection" immediately. Nothing else
    calls this: `get_settings()` stays cached everywhere else, so ordinary
    request handling (and the live call pipeline) never sees configuration
    change out from under it except when this is explicitly invoked.
    """
    get_settings.cache_clear()
    return get_settings()


# Which Settings fields back each external provider. A provider is only
# ever reported "connected"/configured when NONE of its fields are still a
# known dev-mock placeholder (see `_fill_dev_mock_defaults` above) — never
# guessed or assumed. Shared by /system/status and the integration
# connection-test service so there is exactly one definition of "configured".
PROVIDER_FIELDS: dict[str, list[str]] = {
    "openai": ["openai_api_key"],
    "elevenlabs": ["elevenlabs_api_key", "elevenlabs_voice_id_en", "elevenlabs_voice_id_hi"],
    # Only the fields Exotel's REST API auth actually needs. exotel_caller_id
    # (outbound-only) and exotel_webhook_secret (an app-side secret, not an
    # Exotel credential) deliberately do not gate "is Exotel configured" —
    # see their field descriptions above.
    "exotel": ["exotel_sid", "exotel_api_key", "exotel_token"],
    "email": ["smtp_username", "smtp_password", "smtp_from_email"],
}


def is_mock_value(field: str, settings: Settings) -> bool:
    """True if the configured value is a known dev-mock placeholder.

    Catches both cases: the field was silently backfilled (tracked in
    `mock_credentials_used`) AND the field was explicitly set in `.env` to a
    value that still literally matches the placeholder (e.g. a committed
    `.env.example`-style value never replaced with a real credential).
    """
    if field in settings.mock_credentials_used:
        return True
    mock_value = _DEV_MOCK_DEFAULTS.get(field)
    return mock_value is not None and str(getattr(settings, field, None)) == mock_value


def is_provider_configured(provider: str, settings: Settings) -> bool:
    """True if none of `provider`'s backing fields are still a mock placeholder."""
    fields = PROVIDER_FIELDS.get(provider, [])
    return not any(is_mock_value(field, settings) for field in fields)
