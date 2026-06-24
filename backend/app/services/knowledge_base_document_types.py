"""Document types for knowledge-base metadata (single Supermemory container)."""

from app.core.config import settings

KNOWLEDGE_BASE_DOCUMENT_TYPES: dict[str, str] = {
    "verified_facts": "Verified Facts",
    "case_study": "Case Studies",
    "team_bio": "Team Bios",
    "pricing": "Pricing",
    "won_proposal": "Won Proposal",
    "finalist_proposal": "Finalist Proposal",
    "lost_proposal": "Lost + FOIA",
    "scoring_debrief": "Scoring & Debriefs",
    "active_rfp": "Active RFP",
    "reference": "Reference / Guides",
}

LEGACY_CATEGORY_LABELS: dict[str, str] = {
    "00_": "Reference / Guides",
    "01_": "Verified Facts",
    "02_": "Reference / Guides",
    "03_": "Case Studies",
    "04_": "Team Bios",
    "05_": "Pricing",
    "06_": "Won Proposal",
    "07_": "Finalist Proposal",
    "08_": "Lost + FOIA",
    "09_": "Scoring & Debriefs",
    "10_": "Active RFP",
    "11_": "Reference / Guides",
}


def container_tag() -> str:
    return settings.resolved_container_tag


def category_title(value: str) -> str:
    return (
        KNOWLEDGE_BASE_DOCUMENT_TYPES.get(value)
        or LEGACY_CATEGORY_LABELS.get(value)
        or value
    )


def is_valid_category(value: str) -> bool:
    return value in KNOWLEDGE_BASE_DOCUMENT_TYPES or value in LEGACY_CATEGORY_LABELS


def document_type_options() -> list[dict[str, str]]:
    return [
        {"value": key, "label": label}
        for key, label in KNOWLEDGE_BASE_DOCUMENT_TYPES.items()
    ]
