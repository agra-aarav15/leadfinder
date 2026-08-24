"""Real-time conversation agent for website visitors.

Human-like, one question at a time, quietly qualifies the visitor
(name / email / phone / need / budget / urgency), saves everything to
the lead record, and flags converted leads. Discloses it's an AI when
directly asked - required by law in several jurisdictions.
"""
import json
import logging

from . import db

log = logging.getLogger("leadfinder")

SYSTEM_TEMPLATE = """You are {brand}, the friendly sales assistant on {company}'s website.
You chat with visitors in real time. Offer: {offer}.

RULES
- Talk like a real person: short messages (1-3 sentences), contractions, natural warmth. Never sound corporate or robotic.
- ONE question per message.
- Goal path: understand what they need -> show how we help -> get their email/phone -> suggest a quick call or next step.
- Quietly collect: their name, best email, phone (optional), what they need, budget/timeline hints.
- If asked "are you a bot/AI?", answer honestly and warmly ("Yep, I'm {brand}, the AI assistant here - but I can get you a real human on a call anytime!") then continue helping.
- Never invent prices, dates, guarantees or fake humans. If you don't know something, say you'll have someone confirm.
- If they want to buy/book NOW with contact info given, mark converted.

OUTPUT FORMAT (always):
{{"reply": "<your message>", "extract": {{"name": null, "email": null, "phone": null, "need": null, "budget": null, "urgency": null, "converted": false}}}}
Use extract only for values clearly present in the WHOLE conversation; null otherwise; converted=true only if they gave contact + clear intent to move forward."""

FALLBACK_SCRIPT = [
    "Hey! Thanks for stopping by 👋 What brings you here today?",
    "Nice! And what's the best email to reach you on, so I can send over the details?",
    "Perfect, got it. Someone from the team will follow up shortly - anything else I can help with?",
]


def _history_text(msgs) -> str:
    return "\n".join(f"{'VISITOR' if m['role'] == 'user' else 'YOU'}: {m['body']}" for m in msgs)


def _apply_extract(lead_id: int | None, visitor_id: str, extract: dict | None, channel: str = "chat"):
    """Persist extracted fields onto the lead; create lead for anon visitors."""
    if not isinstance(extract, dict):
        return None
    clean = {k: v for k, v in extract.items() if k != "converted" and v}
    lid = lead_id
    if not lid:
        email = clean.get("email")
        existing = db.find_lead_by_email(email) if email else None
        if existing:
            lid = existing["id"]
            db.attach_lead_to_visitor(visitor_id, lid)
        elif any(clean.values()):
            s = db.get_settings()
            lid, _ = db.upsert_lead({
                "source": "website",
                "contact_name": clean.get("name"),
                "email": email,
                "phone": clean.get("phone"),
                "channel": channel,
                "niche": s.get("niche", ""),
                "notes": json.dumps({k: v for k, v in clean.items() if k not in ("name", "email", "phone")}),
                "score": 55,
            })
            db.attach_lead_to_visitor(visitor_id, lid)
    else:
        fields = {}
        if clean.get("name"):
            fields["contact_name"] = clean["name"]
        if clean.get("email"):
            fields["email"] = clean["email"]
        if clean.get("phone"):
            fields["phone"] = clean["phone"]
        note_bits = [clean.get(k) for k in ("need", "budget", "urgency") if clean.get(k)]
        if note_bits:
            lead = db.get_lead(lid)
            prev = (lead["notes"] or "") if lead else ""
            fields["notes"] = (prev + " | " if prev else "") + " | ".join(note_bits)
        if fields:
            db.update_lead(lid, **fields)

    if lid and extract.get("converted") is True:
        cur = db.get_lead(lid)
        if cur and cur["status"] not in ("opted_out",):
            db.update_lead(lid, status="qualified" if not cur["email"] else "converted",
                           last_contact_at=db.now())
            db.log_activity("convert", f"lead converted via {channel}", lid)
    return lid


def reply_to_visitor(visitor_id: str, text: str, channel: str = "chat") -> dict:
    """Main entry: store user msg, produce agent reply dict."""
    from . import brain
    lead = db.find_lead_by_visitor(visitor_id)
    cid = db.get_or_create_conversation(visitor_id, lead_id=lead["id"] if lead else None, channel=channel)
    db.add_message(cid, "user", text)
    msgs = db.recent_messages(cid, 20)
    turn_no = sum(1 for m in msgs if m["role"] == "user")

    s = db.get_settings()
    system = SYSTEM_TEMPLATE.format(
        brand=s.get("brand_name", "Alex"),
        company=s.get("company_name", "our team"),
        offer=s.get("offer", "helping businesses grow"),
    )
    raw = brain.ask(system, f"Conversation so far:\n{_history_text(msgs)}\n\nReply as JSON now.")
    data = brain.extract_json(raw) if raw else None

    if data and isinstance(data.get("reply"), str) and data["reply"].strip():
        reply = data["reply"].strip()
        extract = data.get("extract")
    else:
        idx = min(turn_no - 1, len(FALLBACK_SCRIPT) - 1)
        low = text.lower()
        m_email = None
        import re
        found = re.search(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+", text)
        if found:
            m_email = found.group(0).lower()
        extract = {"email": m_email} if m_email else None
        if any(w in low for w in ("unsubscribe", "stop", "human", "agent")) and turn_no > 1:
            reply = "Absolutely - I'll have a real teammate reach out. What's the best email for them?"
            extract = None
        else:
            reply = FALLBACK_SCRIPT[idx]

    lid = _apply_extract(lead["id"] if lead else None, visitor_id, extract, channel=channel)
    db.add_message(cid, "agent", reply)
    if lid:
        lead_now = db.get_lead(lid)
        if lead_now and lead_now["status"] in ("new", "contacted"):
            db.update_lead(lid, status="engaging", channel=channel, last_contact_at=db.now())
    db.log_activity("chat", f"visitor said: {text[:80]}", lid)
    return {"reply": reply, "conversation_id": cid, "lead_id": lid}
