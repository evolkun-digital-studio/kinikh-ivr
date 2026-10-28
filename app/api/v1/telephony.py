"""Exotel webhook and audio stream endpoints."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.call_logging import log_call_event, mask_phone
from app.core.config import get_settings
from app.core.dependencies import get_db
from app.core.exceptions import AuthenticationError, ValidationError
from app.services.call_recording_service import CallRecordingService
from app.services.call_service import CallService, is_terminal_status, normalize_call_status
from app.services.stream_session_service import StreamSessionService
from app.telephony.exotel.stream import ExotelAudioStream
from app.telephony.exotel.webhook import WEBHOOK_TOKEN_PARAM, ExotelAdapter
from app.telephony.transfer import resolve_transfer_number
from app.voice.speech_handler import SpeechHandler

router = APIRouter(prefix="/telephony", tags=["Telephony"])

_adapter: ExotelAdapter | None = None


def _get_adapter() -> ExotelAdapter:
    global _adapter
    if _adapter is None:
        _adapter = ExotelAdapter()
    return _adapter


def _build_stream_url(request: Request, sid: str) -> str:
    """Build the wss://.../telephony/stream URL for Exotel's Voicebot dynamic
    resolver.

    The scheme follows the inbound request (http -> ws, https -> wss). Behind
    the bundled nginx this relies on uvicorn's `--proxy-headers` (set in
    docker/Dockerfile) so `request.url.scheme` reflects nginx's
    X-Forwarded-Proto. The sid keeps the URL well under Exotel's 256-char
    custom-parameter limit and is never logged.
    """
    public = get_settings().public_base_url.strip().rstrip("/")
    if public:
        # Explicit public URL (e.g. the fixed Cloudflare tunnel hostname) —
        # never depends on how the proxy/tunnel forwards scheme headers.
        base = public.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/api/v1/telephony/stream/{sid}"
    ws_scheme = "wss" if request.url.scheme == "https" else "ws"
    host = request.url.netloc
    return f"{ws_scheme}://{host}/api/v1/telephony/stream/{sid}"


async def _authenticated_params(request: Request) -> dict[str, Any]:
    """Authenticate the original request bytes, then return its parameters.

    Exotel's Voicebot resolver calls with GET + query string; Passthru and
    status callbacks may POST a form. Both are merged (form wins), with the
    webhook credential removed before anything parses or logs the payload.
    """
    raw = await request.body()
    client_ip = request.client.host if request.client else None
    if not _get_adapter().authenticate_webhook(
        body=raw, headers=request.headers, query=request.query_params, client_ip=client_ip
    ):
        log_call_event("exotel.webhook.rejected", path=request.url.path, reason="authentication")
        raise AuthenticationError("Invalid telephony callback authentication")
    params: dict[str, Any] = {
        key: value for key, value in request.query_params.items() if key != WEBHOOK_TOKEN_PARAM
    }
    if raw:
        form = await request.form()
        params.update({key: value for key, value in form.items() if isinstance(value, str)})
    return params


@router.api_route(
    "/incoming", methods=["GET", "POST"], summary="Exotel incoming call webhook / resolver"
)
async def incoming_call(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    payload = await _authenticated_params(request)

    call_info = _get_adapter().parse_incoming_webhook(payload)
    log_call_event(
        "exotel.webhook.received",
        kind="incoming",
        call_sid=call_info.call_sid or None,
        from_number=mask_phone(call_info.caller_number),
        to_number=mask_phone(call_info.callee_number),
        payload_keys=sorted(payload.keys()),
    )
    if not call_info.call_sid:
        raise ValidationError("Missing required field: CallSid")
    call_service = CallService(db)
    await call_service.initiate(
        call_sid=call_info.call_sid,
        caller_number=call_info.caller_number,
        callee_number=call_info.callee_number,
    )
    log_call_event("call.created", call_sid=call_info.call_sid)

    session_service = StreamSessionService(db)
    sid = await session_service.create_session(call_info.call_sid)
    log_call_event("audio.stream.token_generated", token_length=len(sid))

    stream_url = _build_stream_url(request, sid)
    return {
        "statusCode": 200,
        "message": "Call accepted",
        # Exotel's Voicebot Applet HTTP(S) dynamic resolver contract requires
        # the field literally named "url" — a previous version of this
        # endpoint returned "streamUrl" instead, which Exotel's Voicebot
        # Applet does not read, so it never opened the media WebSocket.
        # "streamUrl" is kept alongside for backward compatibility with any
        # existing tooling that reads it; Exotel itself only needs "url".
        "url": stream_url,
        "streamUrl": stream_url,
    }


@router.api_route("/status", methods=["GET", "POST"], summary="Exotel call status webhook")
async def call_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    payload = await _authenticated_params(request)

    call_info = _get_adapter().parse_status_webhook(payload)
    status = normalize_call_status(call_info.status)
    log_call_event(
        "exotel.webhook.received",
        kind="status",
        call_sid=call_info.call_sid or None,
        status=status or "unrecognised",
        has_recording=bool(call_info.recording_url),
        payload_keys=sorted(payload.keys()),
    )
    if not call_info.call_sid:
        raise ValidationError("Missing required field: CallSid")
    if status is None:
        raise ValidationError("Unsupported call status")

    call_service = CallService(db)
    if is_terminal_status(status):
        call = await call_service.complete(
            call_sid=call_info.call_sid,
            status=status,
            duration_seconds=call_info.duration,
            recording_url=call_info.recording_url,
        )
    else:
        call = await call_service.update_status(
            call_info.call_sid, status, recording_url=call_info.recording_url
        )

    # The call row is still locked by complete()/update_status() in this
    # transaction, so a retried callback cannot race this into a duplicate.
    await CallRecordingService(db).record_provider_metadata(call)
    return {"status": "ok"}


def _payload_call_sid(payload: dict[str, Any]) -> str:
    return str(payload.get("CallSid") or "").strip()


@router.api_route(
    "/transfer-check",
    methods=["GET", "POST"],
    summary="Exotel Passthru: should this call be connected to a person?",
)
async def transfer_check(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Called by the Passthru applet after the Voicebot ends. Exotel takes
    the Connect branch on 200 and the Hangup branch on anything else."""
    payload = await _authenticated_params(request)
    call_sid = _payload_call_sid(payload)
    log_call_event(
        "exotel.webhook.received",
        kind="transfer_check",
        call_sid=call_sid or None,
        payload_keys=sorted(payload.keys()),
    )
    if not call_sid:
        return JSONResponse({"transfer": False, "error": "Missing CallSid"}, status_code=400)
    
    from app.services.call_service import get_transfer_fallback
    fallback = get_transfer_fallback(call_sid)
    
    call = await CallService(db).get_by_sid(call_sid)
    if (call is None or not call.transfer_requested) and fallback is None:
        return JSONResponse({"transfer": False}, status_code=404)
    return JSONResponse({"transfer": True})


@router.api_route(
    "/transfer-number",
    methods=["GET", "POST"],
    summary="Exotel Connect: the number to connect the caller to",
)
async def transfer_number(request: Request, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """The saved transfer number(s), else the ones for the saved business area,
    else TRANSFER_DEFAULT_NUMBER — the Connect applet always gets a number."""
    payload = await _authenticated_params(request)
    call_sid = _payload_call_sid(payload)
    call = await CallService(db).get_by_sid(call_sid) if call_sid else None
    
    from app.services.call_service import get_transfer_fallback
    fallback = get_transfer_fallback(call_sid) if call_sid else None
    
    numbers = []
    if call and call.transfer_number:
        numbers = [n for n in call.transfer_number.split(",") if n]
    elif fallback:
        numbers = [n for n in fallback[1].split(",") if n]
    else:
        route = call.transfer_department if call else None
        if fallback:
            route = fallback[0]
        numbers = resolve_transfer_number(route)
        
    route_used = call.transfer_department if call else None
    if fallback:
        route_used = fallback[0]
        
    log_call_event(
        "exotel.webhook.received",
        kind="transfer_number",
        call_sid=call_sid or None,
        call_found=call is not None,
        route=route_used,
        numbers=len(numbers),
    )
    

    
    return JSONResponse(
        {
            "destination": {"numbers": numbers},
            "fetch_after_attempt": False,
            "record": True,
            "max_ringing_duration": 30,
            "music_on_hold": {"type": "operator_tone"},
        }
    )


# call_sids with a live media stream in THIS worker process. A connection
# registry, not call state: it refuses a second concurrent stream (a replayed
# stream URL) for a call that is already streaming. Cross-worker replay is
# bounded by the short token TTL and the terminal-status check.
_active_streams: set[str] = set()


@router.websocket("/stream/{sid}")
async def audio_stream(websocket: WebSocket, sid: str, db: AsyncSession = Depends(get_db)) -> None:
    log_call_event(
        "audio.stream.auth_attempt",
        token_present=bool(sid),
        token_length=len(sid) if sid else 0,
        query_param_names=list(websocket.query_params.keys()),
    )
    if not sid:
        log_call_event("audio.stream.rejected", reason="missing_sid")
        await websocket.close(code=1008)
        return

    session_service = StreamSessionService(db)
    token_call_sid = await session_service.consume_session(sid)
    
    if not token_call_sid:
        log_call_event("audio.stream.rejected", reason="invalid_or_consumed_sid")
        await websocket.close(code=1008)
        return

    log_call_event("audio.stream.authenticated", call_id_present=True)

    if token_call_sid in _active_streams:
        log_call_event("audio.stream.rejected", call_sid=token_call_sid, reason="already_streaming")
        await websocket.close(code=1008)
        return
    try:
        call = await CallService(db).get_by_sid(token_call_sid)
        call_status = call.status if call else None
        await db.rollback()  # release the read transaction before the long-lived stream
    except Exception as exc:
        log_call_event(
            "audio.stream.rejected",
            call_sid=token_call_sid,
            reason="database_unavailable",
            error_type=type(exc).__name__,
        )
        await websocket.close(code=1011)
        return
    if call is None:
        log_call_event("audio.stream.rejected", call_sid=token_call_sid, reason="call_not_found")
        await websocket.close(code=1008)
        return
    if is_terminal_status(call_status):
        log_call_event("audio.stream.rejected", call_sid=token_call_sid, reason="call_not_active")
        await websocket.close(code=1008)
        return

    _active_streams.add(token_call_sid)
    try:
        await _run_stream(websocket, db, token_call_sid)
    finally:
        _active_streams.discard(token_call_sid)


async def _run_stream(websocket: WebSocket, db: AsyncSession, token_call_sid: str) -> None:
    await websocket.accept()
    log_call_event("audio.stream.accepted", call_sid=token_call_sid)

    # Exotel can be configured to pass the caller's number either as a
    # `start` event customParameter (read by ExotelAudioStream itself) or as
    # a query parameter appended to the WSS URL — Exotel's own docs describe
    # the latter as a supported mechanism. Support both without requiring
    # either: caller number must never block the connection or the greeting.
    query_caller_number = websocket.query_params.get("from") or websocket.query_params.get("From")

    speech_handler = SpeechHandler(db)
    authorized_start = False

    async def _on_start(call_sid: str, caller_number: str) -> None:
        nonlocal authorized_start
        if (
            call_sid != token_call_sid
            or not stream.stream_sid
            or (stream.account_sid and stream.account_sid != get_settings().exotel_sid)
        ):
            log_call_event(
                "audio.stream.rejected", call_sid=token_call_sid, reason="start_mismatch"
            )
            await websocket.close(code=1008)
            raise AuthenticationError("Stream call identifier mismatch")
        # `stream` is defined below — safe to reference here because this
        # closure is only ever invoked from within stream.start(), by which
        # point `stream` is already fully constructed (Python closures
        # resolve names at call time, not at function-definition time).
        # start() only loads the session; the greeting plays as a background
        # response task so this receive loop keeps reading caller audio
        # (and can detect a barge-in over the greeting).
        await speech_handler.start(
            call_sid, caller_number or query_caller_number or "", stream.stream_sid
        )
        authorized_start = True
        await db.rollback()  # department lookup must not hold a DB connection for the call

    stream = ExotelAudioStream(
        websocket=websocket,
        on_audio=speech_handler.handle_audio,
        on_dtmf=speech_handler.handle_dtmf,
        on_start=_on_start,
        on_stop=speech_handler.mark_stopped,
    )
    # SpeechHandler sends audio back through ExotelAudioStream.send_audio(), which
    # builds Exotel's required {"event":"media",...} JSON text frame — never the
    # raw WebSocket binary frame this route used to send.
    speech_handler.set_audio_sender(stream.send_audio)
    speech_handler.set_audio_clearer(stream.clear_audio)

    async def _hangup() -> None:
        # Closing the media stream ends the Voicebot applet; Exotel moves on
        # to the next applet in the flow (configure a Hangup applet there).
        await stream.stop()
        try:
            await websocket.close(code=1000)
        except Exception as exc:  # already closed by Exotel
            log_call_event(
                "audio.stream.close_skipped", call_sid=token_call_sid, error_type=type(exc).__name__
            )

    speech_handler.set_hangup(_hangup)

    try:
        await stream.start()
    except Exception as exc:
        log_call_event("audio.stream.error", call_sid=token_call_sid, error_type=type(exc).__name__)
    finally:
        # Real-time work stops first (every per-call task cancelled and
        # awaited); only then does post-call work (lead extraction, DB
        # persistence, email) run — never on the live audio path.
        await speech_handler.mark_stopped(stream.call_sid)
        if authorized_start:
            await speech_handler.finalize(token_call_sid)
        log_call_event("audio.stream.closed", call_sid=token_call_sid)
