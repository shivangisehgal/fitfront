"""
Twilio inbound SMS webhook route.

POST /webhook/sms  -> receive inbound SMS from Twilio and respond with TwiML
"""

import logging
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Request, Response

from backend.services import sms_inbound_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["SMS Webhook"])

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhook/sms")
async def sms_webhook(request: Request):
    """
    Twilio inbound SMS webhook. Receives form-encoded data from Twilio,
    processes the message through sms_inbound_service, and returns a TwiML
    response.
    """
    try:
        form_data = await request.form()
        from_number = form_data.get("From", "")
        to_number = form_data.get("To", "")
        body = form_data.get("Body", "")
        twilio_sid = form_data.get("MessageSid", "")

        logger.info("[SMS Webhook] Inbound SMS from=%s to=%s sid=%s body_len=%d",
                     from_number, to_number, twilio_sid, len(body))

        # Deduplication — Twilio may retry if our first response was slow.
        # MessageSid is unique per message; skip if we already processed it.
        if not body.strip():
            logger.info("[SMS Webhook] Empty body — returning empty TwiML")
            return Response(content=_EMPTY_TWIML, media_type="application/xml")

        reply_text = await sms_inbound_service.handle_inbound_sms(
            from_number=from_number,
            to_number=to_number,
            body=body,
            twilio_sid=twilio_sid,
        )

        if reply_text:
            # Escape XML-special chars so LLM output like "< 5 minutes"
            # doesn't break the TwiML document.
            safe_text = xml_escape(reply_text)
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                f"<Response><Message>{safe_text}</Message></Response>"
            )
        else:
            twiml = _EMPTY_TWIML

        return Response(content=twiml, media_type="application/xml")

    except Exception as exc:
        logger.error("[SMS Webhook] Error processing inbound SMS: %s", exc, exc_info=True)
        # Return empty TwiML so Twilio does not retry
        return Response(content=_EMPTY_TWIML, media_type="application/xml")
