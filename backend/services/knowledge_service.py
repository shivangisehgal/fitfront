"""
Knowledge base service — reads per-tenant KB from the database (JSONB column)
and converts it into context strings for system prompt injection.

Multi-tenant: each tenant stores their knowledge_base in the Tenant.knowledge_base
JSONB column. Falls back to the legacy default_kb.json file for backwards compat.
"""
from __future__ import annotations


import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KB_PATH = Path(__file__).resolve().parent.parent / "knowledge" / "default_kb.json"


# ── Load / save (legacy disk-based for default tenant) ───────────────────────


def load_knowledge_base() -> dict[str, Any]:
    """Load the knowledge base JSON from disk. Returns empty dict on error."""
    try:
        with open(KB_PATH, "r") as f:
            data = json.load(f)
        logger.info("Knowledge base loaded (%d top-level keys).", len(data))
        return data
    except FileNotFoundError:
        logger.warning("Knowledge base file not found at %s — using defaults.", KB_PATH)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("Malformed knowledge base JSON: %s", exc)
        return {}


def save_knowledge_base(data: dict[str, Any]) -> bool:
    """Persist updated knowledge base to disk. Returns True on success."""
    try:
        with open(KB_PATH, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("Knowledge base saved.")
        return True
    except Exception as exc:
        logger.error("Failed to save knowledge base: %s", exc)
        return False


# ── Per-tenant KB ────────────────────────────────────────────────────────────


def get_tenant_kb(tenant_ctx: Any | None) -> dict[str, Any]:
    """
    Get the knowledge base for a given tenant.

    - If a tenant is provided, ALWAYS use the tenant's KB (even if empty).
      This ensures one tenant's KB doesn't leak into another's prompt.
    - Only fall back to the legacy file when no tenant context at all.
    """
    if tenant_ctx is not None:
        return tenant_ctx.knowledge_base or {}
    return load_knowledge_base()


# ── Context string builder ───────────────────────────────────────────────────


def kb_to_context_string(
    kb: dict[str, Any] | None = None,
    tenant_ctx: Any | None = None,
) -> str:
    """
    Convert the knowledge base dict into a readable string for the system prompt.

    If tenant_ctx is provided, uses the tenant's KB from the database.
    Otherwise falls back to the legacy file or provided kb dict.
    """
    if kb is None:
        kb = get_tenant_kb(tenant_ctx)
    if not kb:
        return ""

    sections: list[str] = []

    # Office info
    if "office_info" in kb:
        info = kb["office_info"]
        sections.append(
            f"OFFICE INFORMATION:\n"
            f"  Name: {info.get('name', 'N/A')}\n"
            f"  Address: {info.get('address', 'N/A')}\n"
            f"  Phone: {info.get('phone', 'N/A')}\n"
            f"  Hours: {info.get('hours', 'N/A')}\n"
            f"  Emergency: {info.get('emergency_info', 'N/A')}"
        )

    # Services & pricing
    if "services" in kb:
        lines = ["SERVICES & PRICING:"]
        for svc in kb["services"]:
            lines.append(f"  - {svc['name']}: ${svc.get('price_min', '?')}–${svc.get('price_max', '?')}")
        sections.append("\n".join(lines))

    # FAQs
    if "faqs" in kb:
        lines = ["FREQUENTLY ASKED QUESTIONS:"]
        for faq in kb["faqs"]:
            lines.append(f"  Q: {faq['question']}\n  A: {faq['answer']}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
