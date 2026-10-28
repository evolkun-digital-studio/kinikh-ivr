"""Business rules for department routing — keyword and priority overrides."""

from __future__ import annotations

ROUTING_RULES: dict[str, list[str]] = {
    "rural": ["dairy", "milk", "doodh", "farm", "kheti", "organic", "fish", "machhli", "fisheries", "handicraft", "hastshilp", "kril", "agro", "artisan", "rural enterprise"],
    "stay": ["homestay", "hotel", "room", "kamra", "resort", "retreat", "booking", "slow tuesday", "hospitality"],
    "tech": ["website", "software", "app", "crm", "erp", "automation", "dashboard", "evolkun", "technology", "it"],
    "brand": ["branding", "marketing", "ads", "advertising", "social media", "brandworks", "media", "brand & media"],
    "infra": ["construction", "nirman", "building", "civil", "contractor", "kinikh infra", "infrastructure"],
    "land": ["plot", "zameen", "estate", "real estate", "landcraft"],
    "story": ["documentary", "story", "people with purpose", "unsung legacy"],
    "ngo": ["empower society", "education", "welfare", "volunteer", "social impact", "nonprofit"],
    "accounts": ["bill", "invoice", "payment", "bhugtan", "receipt", "rasid", "dues", "refund status", "salary status"],
    "support": ["complaint", "escalation", "hr", "job", "naukri", "partnership", "human", "unknown", "general"],
    "ea": ["meeting", "management", "founder"]
}


def keyword_route(text: str) -> str | None:
    """Fast keyword-based routing before hitting OpenAI for classification."""
    lower = text.lower()
    scores: dict[str, int] = {}
    for dept, keywords in ROUTING_RULES.items():
        hits = sum(1 for kw in keywords if kw in lower)
        if hits:
            scores[dept] = hits
    if not scores:
        return None
    return max(scores, key=lambda d: scores[d])
