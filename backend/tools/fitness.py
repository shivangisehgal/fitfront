"""
FitnessToolProvider — tools active for fitness studio tenants.

These tools extend the platform's default scheduling workflow with
fitness-specific concepts: class availability, membership sign-up, and schedule delivery.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

FITNESS_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "check_class_availability",
            "description": (
                "Check which class slots or programs have open spots. "
                "Use when a caller asks about joining a class or program and wants "
                "to know available timings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "program": {
                        "type": "string",
                        "description": "The program or class type (e.g. 'HIIT', 'Personal Training', 'Yoga Flow').",
                    },
                    "goal": {
                        "type": "string",
                        "description": "Optional. The client's fitness goal (e.g. 'fat loss', 'muscle gain', 'general fitness').",
                    },
                },
                "required": ["program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_membership",
            "description": (
                "Register a new member after they have agreed to join following a trial session. "
                "Only call this after the caller explicitly agrees to sign up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "client_name": {
                        "type": "string",
                        "description": "Full name of the client signing up.",
                    },
                    "client_phone": {
                        "type": "string",
                        "description": "Client's phone number (from caller-ID in your system prompt).",
                    },
                    "program": {
                        "type": "string",
                        "description": "The program or membership type the client is joining.",
                    },
                    "class_id": {
                        "type": "string",
                        "description": "Optional class/program code from check_class_availability.",
                    },
                },
                "required": ["client_name", "client_phone", "program"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_class_schedule",
            "description": (
                "Send the class schedule or membership info to the caller via SMS. "
                "Use when a caller asks to receive more information before booking a trial session."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Phone number to send the schedule to.",
                    },
                    "program": {
                        "type": "string",
                        "description": "Optional. The specific program they're interested in.",
                    },
                },
                "required": ["phone"],
            },
        },
    },
]

_FITNESS_TOOL_NAMES: frozenset[str] = frozenset(
    t["function"]["name"] for t in FITNESS_TOOL_SCHEMAS
)


class FitnessToolProvider:
    """
    Provides fitness-studio tools. Active for fitness_studio tenants.
    """

    TOOL_NAMES: frozenset[str] = _FITNESS_TOOL_NAMES

    def handles(self, tool_name: str) -> bool:
        return tool_name in _FITNESS_TOOL_NAMES

    async def list_tools(self, tenant_ctx: Any) -> list[dict]:
        if not tenant_ctx:
            return []
        return FITNESS_TOOL_SCHEMAS

    async def call_tool(self, name: str, args: dict, session_ctx: dict) -> dict:
        tenant_ctx = session_ctx.get("tenant_ctx")
        call_id: str = session_ctx.get("call_id", "unknown")

        if name == "check_class_availability":
            return await self._check_class_availability(args, tenant_ctx, call_id)
        elif name == "start_membership":
            return await self._start_membership(args, tenant_ctx, call_id)
        elif name == "send_class_schedule":
            return await self._send_class_schedule(args, tenant_ctx, call_id)
        else:
            raise ValueError(f"FitnessToolProvider does not handle tool: {name!r}")

    async def _check_class_availability(
        self, args: dict, tenant_ctx: Any, call_id: str
    ) -> dict:
        program = args.get("program", "")

        classes: list[dict] = []
        if tenant_ctx and tenant_ctx.appointment_types:
            for at in tenant_ctx.appointment_types:
                at_name = at.get("name", "") or at.get("code", "")
                if not program or program.lower() in at_name.lower():
                    classes.append({
                        "class_id": at.get("code", ""),
                        "program": at_name,
                        "duration_minutes": at.get("duration_minutes", 60),
                        "slot_capacity": at.get("slot_capacity", 1),
                        "note": "Contact us to confirm spot availability.",
                    })

        if not classes:
            return {
                "ok": False,
                "summary_for_assistant": (
                    f"No class information found for '{program}'. "
                    f"Tell the caller you'll have someone from the front desk "
                    f"call them back with class details. Offer to schedule a callback or trial session."
                ),
                "classes": [],
            }

        class_lines = [
            f"{c['program']} (class: {c['class_id']}, {c['duration_minutes']} min)"
            for c in classes
        ]
        logger.info("[Call %s] check_class_availability → %d classes for '%s'", call_id, len(classes), program)
        return {
            "ok": True,
            "summary_for_assistant": (
                f"Available classes for {program or 'your query'}: "
                + "; ".join(class_lines) + ". "
                f"Ask the caller if they'd like to book a trial session or get more details."
            ),
            "classes": classes,
            "program_filter": program,
        }

    async def _start_membership(
        self, args: dict, tenant_ctx: Any, call_id: str
    ) -> dict:
        from backend.services import sms_service

        client_name = args.get("client_name", "")
        client_phone = args.get("client_phone", "")
        program = args.get("program", "")
        class_id = args.get("class_id", program)

        if not client_name or not client_phone or not program:
            return {
                "ok": False,
                "summary_for_assistant": (
                    "To start a membership, I need the client's name, phone number, and program. "
                    "Please confirm these details."
                ),
            }

        logger.info(
            "[Call %s] start_membership: client=%s, program=%s, class=%s",
            call_id, client_name, program, class_id,
        )

        try:
            sms_service.send_office_alert(
                reason=(
                    f"New membership sign-up: {client_name} for {program} "
                    f"(class: {class_id}). Phone: {client_phone}"
                ),
                caller_number=client_phone,
                tenant_ctx=tenant_ctx,
            )
        except Exception as exc:
            logger.warning("[Call %s] Membership SMS alert failed: %s", call_id, exc)

        return {
            "ok": True,
            "summary_for_assistant": (
                f"Membership sign-up for {client_name} in {program} has been registered. "
                f"Our membership team will contact {client_phone} within 24 hours to "
                f"confirm details and share payment options. "
                f"Tell the caller we're excited to have them join and ask if there's anything else."
            ),
            "client_name": client_name,
            "program": program,
            "class_id": class_id,
            "client_phone": client_phone,
        }

    async def _send_class_schedule(
        self, args: dict, tenant_ctx: Any, call_id: str
    ) -> dict:
        from backend.services import sms_service

        phone = args.get("phone", "")
        program = args.get("program", "")

        if not phone:
            return {
                "ok": False,
                "summary_for_assistant": "I need a phone number to send the class schedule.",
            }

        business_name = getattr(tenant_ctx, "business_name", "us") if tenant_ctx else "us"
        program_part = f" for {program}" if program else ""
        message = (
            f"Hi! Here is the class schedule and membership info{program_part} from {business_name}. "
            f"Our front desk team will follow up shortly. "
            f"Please call us if you have any questions!"
        )

        try:
            sms_service.send_custom_sms(
                to=phone,
                message=message,
                tenant_ctx=tenant_ctx,
            )
            logger.info("[Call %s] send_class_schedule → SMS sent to %s", call_id, phone)
            return {
                "ok": True,
                "summary_for_assistant": (
                    f"Class schedule info has been sent to {phone} via SMS. "
                    f"Tell the caller to expect a message shortly and ask if they'd like to "
                    f"book a trial session while they're on the call."
                ),
            }
        except Exception as exc:
            logger.warning("[Call %s] Class schedule SMS failed: %s", call_id, exc)
            return {
                "ok": False,
                "summary_for_assistant": (
                    "I wasn't able to send the SMS right now. "
                    "Please ask the caller to contact us directly or visit our website for class details."
                ),
            }
