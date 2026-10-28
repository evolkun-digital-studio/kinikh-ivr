"""Live call transfer: which number to dial, and whether a person is available.

The assistant ends a call with [TRANSFER:<business area>]; the Exotel flow's
Passthru/Connect applets then dial the number resolved here. See
docs/call_transfer.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
import re
from zoneinfo import ZoneInfo

from loguru import logger

from app.core.config import Settings, get_settings

TRANSFER_TIMEZONE = ZoneInfo("Asia/Kolkata")
GENERAL_DEPARTMENT = "support"

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HOURS_ENTRY = re.compile(
    r"^(?P<days>[a-z]{3}(?:\s*-\s*[a-z]{3})?(?:\s*,\s*[a-z]{3}(?:\s*-\s*[a-z]{3})?)*)\s+"
    r"(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})$"
)

ROUTE_SYNONYMS = {
    "rural": ["dairy", "milk", "doodh", "farm", "kheti", "organic", "fish", "machhli", "fisheries", "handicraft", "hastshilp", "kril", "agro", "artisan", "rural enterprise"],
    "stay": ["homestay", "hotel", "room", "kamra", "resort", "retreat", "booking", "slow tuesday", "hospitality"],
    "tech": ["website", "software", "app", "crm", "erp", "automation", "dashboard", "evolkun", "technology", "it"],
    "brand": ["branding", "marketing", "ads", "advertising", "social media", "brandworks", "media", "brand & media"],
    "infra": ["construction", "nirman", "building", "civil", "contractor", "kinikh infra", "infrastructure"],
    "land": ["plot", "zameen", "estate", "real estate", "landcraft"],
    "story": ["documentary", "story", "people with purpose", "unsung legacy"],
    "ngo": ["empower society", "education", "welfare", "volunteer", "social impact", "nonprofit"],
    "accounts": ["bill", "invoice", "payment", "bhugtan", "receipt", "rasid", "dues", "refund status", "salary status"],
    "support": [
        "complaint", "escalation", "hr", "job", "naukri", "partnership",
        "human", "unknown", "general", "general manager", "gm", "gen mgr",
        "gen. mgr.", "manager", "manager se baat", "senior", "senior person",
        "boss", "in-charge", "head",
    ],
    "ea": [
        "meeting", "meeting with management", "meeting with founder", "founder",
        "appointment with management", "founder se milna",
    ],
}


def _match_text(value: str) -> str:
    """Normalize punctuation and whitespace for phrase matching."""
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def normalize_department(department: str) -> str:
    """Find an official route in free text, preferring the longest phrase."""
    dep = _match_text(department or "")
    if not dep:
        return "support"

    candidates = [
        (_match_text(phrase), route)
        for route, synonyms in ROUTE_SYNONYMS.items()
        for phrase in (route, *synonyms)
    ]
    for phrase, route in sorted(candidates, key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", dep):
            return route
    return "support"



@dataclass(frozen=True)
class TransferWindow:
    days: frozenset[int]  # 0 = Monday
    start: time
    end: time  # exclusive


def normalize_e164(number: str) -> str:
    digits = "".join(c for c in number if c.isdigit())
    if len(digits) == 10:
        return f"+91{digits}"
    if len(digits) == 12 and digits.startswith("91"):
        return f"+{digits}"
    if digits:
        return f"+{digits}"
    return number


def parse_transfer_numbers(raw: str, settings: Settings | None = None) -> dict[str, list[str]]:
    """ "infrastructure=+91...|+91..., hospitality=+91..." -> {"infrastructure": ["+91...", ...], ...}.

    Keys are lowercased; malformed pairs are skipped with a warning.
    """
    settings = settings or get_settings()
    invalid_numbers = {"+919761615375"}
    if settings.exotel_caller_id:
        invalid_numbers.add(normalize_e164(settings.exotel_caller_id))

    numbers: dict[str, list[str]] = {}
    for pair in (raw or "").split(";"):
        if not pair.strip():
            continue
        key, sep, nums_str = pair.partition("=")
        key = key.strip().lower()
        if not sep or not key or not nums_str:
            logger.warning("[TRANSFER] ignoring malformed TRANSFER_NUMBERS entry")
            continue
        
        valid_nums = []
        for n in nums_str.split("|"):
            n = n.strip()
            if not n: continue
            norm_n = normalize_e164(n)
            if norm_n in invalid_numbers:
                logger.error(f"[TRANSFER] Dropping transfer number for {key} because it equals the AI's own number {norm_n}")
                continue
            valid_nums.append(norm_n)
            
        if valid_nums:
            numbers[key] = valid_nums
    return numbers


def resolve_transfer_number(department: str | None, settings: Settings | None = None) -> list[str]:
    """The numbers for `department`; unknown, empty or "general" -> the default."""
    settings = settings or get_settings()
    key = normalize_department(department)
    
    numbers_dict = parse_transfer_numbers(settings.transfer_numbers, settings)
    default_num = normalize_e164(settings.transfer_default_number)
    
    primary = numbers_dict.get(key)
    if not primary:
        primary = [default_num]
    
    support_number = numbers_dict.get("support", [default_num])
    
    result = list(primary)
    if key not in ("support", "ea"):
        for num in support_number:
            if num not in result:
                result.append(num)
        
    return result


def _day_set(spec: str) -> set[int]:
    days: set[int] = set()
    for part in spec.split(","):
        first, _, last = (p.strip() for p in part.partition("-"))
        if first not in _DAYS or (last and last not in _DAYS):
            raise ValueError(f"unknown day in {part.strip()!r}")
        start = _DAYS.index(first)
        end = _DAYS.index(last) if last else start
        span = (end - start) % 7  # "Sat-Mon" wraps over Sunday
        days.update((start + i) % 7 for i in range(span + 1))
    return days


def _clock(value: str) -> time:
    hours, minutes = (int(v) for v in value.split(":"))
    if hours == 24 and minutes == 0:
        return time.max
    return time(hours, minutes)


def parse_transfer_hours(raw: str) -> list[TransferWindow]:
    """ "Mon-Fri 10:00-19:00, Sat 10:00-14:00" -> windows. Raises ValueError."""
    windows: list[TransferWindow] = []
    # Entries are separated by commas, but day lists use commas too
    # ("Mon,Wed 10:00-12:00"): an entry always ends in a time, so split only
    # on a comma that follows a digit.
    entries = re.split(r"(?<=\d)\s*,\s*", (raw or "").strip())
    for entry in entries:
        entry = entry.strip().lower()
        if not entry:
            continue
        match = _HOURS_ENTRY.match(entry)
        if not match:
            raise ValueError(f"cannot parse transfer hours entry {entry!r}")
        start, end = _clock(match["start"]), _clock(match["end"])
        if end <= start:
            raise ValueError(f"transfer hours end before they start in {entry!r}")
        windows.append(TransferWindow(frozenset(_day_set(match["days"])), start, end))
    if not windows:
        raise ValueError("no transfer hours configured")
    return windows


def is_transfer_available(now: datetime | None = None, settings: Settings | None = None) -> bool:
    """True if transfers are enabled and `now` (default: current time) is
    within TRANSFER_HOURS in India time. A malformed TRANSFER_HOURS means
    unavailable — the assistant must never promise a transfer it can't make."""
    settings = settings or get_settings()
    if not settings.transfer_enabled:
        return False
    try:
        windows = parse_transfer_hours(settings.transfer_hours)
    except ValueError as exc:
        logger.warning(f"[TRANSFER] invalid TRANSFER_HOURS, transfers disabled: {exc}")
        return False
    local = (now or datetime.now(TRANSFER_TIMEZONE)).astimezone(TRANSFER_TIMEZONE)
    clock = local.time()
    return any(
        local.weekday() in w.days and w.start <= clock < w.end for w in windows
    )
