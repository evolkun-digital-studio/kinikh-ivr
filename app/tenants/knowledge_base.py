"""Department knowledge bases — contextual information for AI responses."""

from __future__ import annotations

KNOWLEDGE_BASES: dict[str, str] = {
    "rural": "KINIKH Rural: farm produce, dairy, organic and natural products, fisheries, handicrafts, rural sourcing and distribution.",
    "stay": "Slow Tuesday: homestays, eco-resorts, eco-cottages, retreats and stay bookings.",
    "tech": "evolKun (Kinikh Technologies): websites, business software, CRM, MIS, automation, dashboards.",
    "brand": "BrandWorks: branding, marketing, ad campaigns, social media, content, events.",
    "infra": "Kinikh Infra: turnkey construction, civil and structural works, project delivery.",
    "land": "Kinikh Landcraft: land and estate enquiries, estate and land development.",
    "story": "People With Purpose: documentary stories about small entrepreneurs, workers and makers.",
    "ngo": "Empower Society (nonprofit): education, welfare, grassroots empowerment, volunteering.",
    "accounts": "Common Accounts: invoices, payments, receipts, dues, refund payment status.",
    "support": "Central Support / General Manager: general enquiries, complaints, escalation, HR coordination, unclear requests, failed transfers.",
    "ea": "Founder's Executive Assistant: explicit management meeting requests only.",
}


def get_knowledge_base(department: str) -> str:
    return KNOWLEDGE_BASES.get(department.lower(), "")
