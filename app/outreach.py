"""Outreach engine: email sequences + AI inbox replies.

Dry-run by default: until SMTP/IMAP env vars are set, every email is
*simulated* (logged to the DB and visible in the dashboard) so you can
test the whole pipeline safely before connecting a real free mailbox.
"""
import email as email_lib
import email.header
import email.utils
import imaplib
import logging
import os
import re
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import db

log = logging.getLogger("leadfinder")

UNSUB_FOOTER = (
    "\n\n---\nYou're receiving this one-time business introduction. "
    "Reply \"unsubscribe\" or click here to never hear from us again: {unsub_url}"
)


def mail_config() -> dict:
    return {
        "host": os.getenv("SMTP_HOST", ""),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", ""),
        "pass": os.getenv("SMTP_PASS", ""),
        "imap_host": os.getenv("IMAP_HOST", ""),
    }


def live_mode() -> bool:
    mc = mail_config()
    return bool(mc["host"] and mc["user"] and mc["pass"])


# ---------- writing ----------

def _lead_context(lead) -> str:
    socials = lead["socials"] or "{}"
    return (
        f"Company: {lead['company']} (website: {lead['domain']})\n"
        f"Location: {lead['location'] or 'unknown'}\n"
        f"Contact: {lead['contact_name'] or 'unknown'}\n"
        f"Social presence: {socials}\n"
        f"Niche we target them for: {lead['niche']}"
    )


def write_first_touch(lead) -> tuple[str, str]:
    """Returns (subject, body). LLM-personalized when brain is available."""
    from . import brain
    s = db.get_settings()
    system = (
        f"You are {s.get('brand_name','Alex')} from {s.get('company_name','our team')}. "
        "Write a short cold email (max 90 words) to a business owner. Rules: "
        "sound like a real busy human - lowercase casual opener ok, no corporate buzzwords, "
        "no 'I hope this finds you well', reference something specific about THEIR site/situation, "
        "one clear soft call-to-action question at the end. "
        f"Our offer: {s.get('offer','helping businesses grow')}. "
        "Output format exactly:\nSUBJECT: <subject under 8 words>\nBODY: <email body>"
    )
    txt = brain.ask(system, _lead_context(lead))
    if txt:
        m = re.search(r"SUBJECT:\s*(.+?)\s*BODY:\s*(.+)", txt, re.S)
        if m:
            return m.group(1).strip()[:120], m.group(2).strip()
    company = lead["company"]
    where = f" in {lead['location']}" if lead["location"] else ""
    subject = f"quick idea for {company}"
    body = (
        f"Hi {lead['contact_name'] or 'there'},\n\n"
        f"I came across {company} while looking at {lead['niche']} businesses{where}, and had one thought: "
        f"{s.get('offer', 'we help businesses grow')}.\n\n"
        "Worth a quick chat this week? If not, totally fine - I'll leave you alone.\n\n"
        f"- {s.get('brand_name', 'Alex')}\n{s.get('company_name', '')}"
    )
    return subject, body


def write_followup(lead, touch_no: int) -> tuple[str, str]:
    s = db.get_settings()
    openers = [
        "Just floating this back up - know things get busy.",
        "One quick nudge and then I'll stop.",
        "Last note from me, promise.",
    ]
    body = (
        f"Hi {lead['contact_name'] or 'there'},\n\n{openers[min(touch_no, 3) - 1]} "
        f"If making {lead['niche']} work easier is on the roadmap, I can show you in 15 minutes. "
        f"If not, no hard feelings at all.\n\n- {s.get('brand_name', 'Alex')}"
    )
    return f"re: {lead['company'] or 'your business'}", body


# ---------- sending ----------

def send_email(lead, subject: str, body: str) -> str:
    """Returns 'sent' | 'simulated'. Enforces opt-out. Records activity."""
    if lead["email"] and db.is_opted_out(lead["email"], lead["domain"]):
        db.update_lead(lead["id"], status="opted_out")
        return "blocked_optout"

    unsub_url = f"{os.getenv('PUBLIC_URL', 'http://localhost:8787')}/unsubscribe/{lead['id']}"
    full_body = body + UNSUB_FOOTER.format(unsub_url=unsub_url)

    if live_mode():
        mc = mail_config()
        msg = MIMEMultipart("alternative")
        msg["From"] = f"{db.get_settings().get('brand_name', 'Sales')} <{mc['user']}>"
        msg["To"] = lead["email"]
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate()
        msg.attach(MIMEText(full_body, "plain"))
        ctx = ssl.create_default_context()
        try:
            if mc["port"] == 465:
                with smtplib.SMTP_SSL(mc["host"], mc["port"], context=ctx) as srv:
                    srv.login(mc["user"], mc["pass"])
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(mc["host"], mc["port"]) as srv:
                    srv.starttls(context=ctx)
                    srv.login(mc["user"], mc["pass"])
                    srv.send_message(msg)
            result = "sent"
        except Exception as e:
            log.error(f"SMTP send failed to {lead['email']}: {e}")
            db.log_activity("error", f"send failed {lead['email']}: {e}", lead["id"])
            return "failed"
    else:
        result = "simulated"

    touches = (lead["touches"] or 0) + 1
    from datetime import datetime, timedelta, timezone
    nxt = (datetime.now(timezone.utc) + timedelta(days=int(db.get_settings().get("followup_days", "3")))).isoformat(timespec="seconds")
    status = "contacted" if lead["status"] == "new" else lead["status"]
    db.update_lead(lead["id"], touches=touches, last_contact_at=db.now(),
                   next_followup_at=nxt if touches < int(db.get_settings().get("max_touches", "3")) else None,
                   channel="email", status=status)
    db.add_message(
        db.get_or_create_conversation(visitor_id=f"email:{lead['email']}", lead_id=lead["id"], channel="email"),
        "agent", f"[email out] {subject}\n\n{body}", {"result": result},
    )
    db.log_activity("email_sent" if result == "sent" else "email_simulated",
                    f"to {lead['email']}: {subject}", lead["id"])
    return result


def run_outreach_batch(limit: int = 10) -> dict:
    """Send first-touch (or next touch) to leads that are due."""
    sent = simulated = failed = 0
    rows = db.list_leads(limit=200)
    daily_cap = int(db.get_settings().get("daily_email_limit", "40"))
    already_today = db.get_conn().execute(
        "SELECT COUNT(*) FROM messages WHERE role='agent' AND created_at >= date('now')"
    ).fetchone()[0]
    room = max(0, daily_cap - already_today)

    for lead in rows[: limit + 5]:
        if sent + simulated + failed >= min(limit, room):
            break
        # opted-out/converted never get mail; 'engaging' means a live conversation
        # (chat or inbox) is already running - don't blast sequences over it.
        if not lead["email"] or lead["status"] in ("opted_out", "converted", "lost", "engaging"):
            continue
        if lead["status"] == "contacted" and lead["last_contact_at"]:
            continue  # waiting on reply / sequence timing; sequencer handles follow-ups
        subject, body = write_first_touch(lead) if (lead["touches"] or 0) == 0 else write_followup(lead, lead["touches"])
        res = send_email(lead, subject, body)
        if res == "sent":
            sent += 1
        elif res == "simulated":
            simulated += 1
        elif res == "failed":
            failed += 1
    return {"sent": sent, "simulated": simulated, "failed": failed}


def run_due_followups() -> int:
    now_iso = db.now()
    rows = db.get_conn().execute(
        "SELECT * FROM leads WHERE next_followup_at IS NOT NULL AND next_followup_at <= ? "
        "AND status IN ('contacted','engaging') AND email IS NOT NULL",
        (now_iso,),
    ).fetchall()
    n = 0
    for lead in rows:
        subject, body = write_followup(lead, (lead["touches"] or 0) + 1)
        send_email(lead, subject, body)
        n += 1
    return n


# ---------- inbound (AI reads your inbox and answers) ----------

def _ai_reply(lead, history_text: str, incoming: str) -> str | None:
    from . import brain
    s = db.get_settings()
    system = (
        f"You are {s.get('brand_name','Alex')} from {s.get('company_name','our team')} replying by email "
        f"to a business lead. Offer: {s.get('offer','helping businesses grow')}.\n"
        "Style: human, warm, brief (under 80 words), match their energy. Goal: move them to a "
        "15-min call or get their specific need. Ask ONE question per email. Never invent facts, "
        "prices or guarantees. If they want to unsubscribe or say stop, apologize kindly and confirm it's done. "
        "Reply with the email body text only."
    )
    return brain.ask(system, f"Conversation so far:\n{history_text}\n\nNew message from lead:\n{incoming}")


def _decode_subject(msg) -> str:
    raw = msg.get("Subject", "") or ""
    try:
        out = ""
        for data, enc in email.header.decode_header(raw):
            out += data.decode(enc or "utf-8", "ignore") if isinstance(data, bytes) else data
        return out
    except Exception:
        return str(raw)


def poll_inbox_once() -> int:
    """Read unseen mail, thread it to the right lead, auto-reply with AI."""
    mc = mail_config()
    if not (mc["imap_host"] and mc["user"] and mc["pass"]):
        return 0
    handled = 0
    try:
        conn = imaplib.IMAP4_SSL(mc["imap_host"])
        conn.login(mc["user"], mc["pass"])
        conn.select("INBOX")
        typ, data = conn.search(None, "(UNSEEN)")
        for num in data[0].split():
            typ, msg_data = conn.fetch(num, "(RFC822)")
            raw = msg_data[0][1]
            msg = email_lib.message_from_bytes(raw)
            from_hdr = email.utils.parseaddr(msg.get("From", ""))[1].lower()
            subj = _decode_subject(msg)
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        body = payload.decode(errors="ignore") if payload else ""
                        break
            else:
                payload = msg.get_payload(decode=True)
                body = payload.decode(errors="ignore") if payload else ""
            body = re.sub(r"\s+", " ", body)[:4000]

            lead = db.find_lead_by_email(from_hdr)
            if not lead:
                continue
            cid = db.get_or_create_conversation(visitor_id=f"email:{from_hdr}", lead_id=lead["id"], channel="email")
            db.add_message(cid, "user", f"[email in] {subj}\n\n{body}")
            low = (subj + " " + body).lower()
            if any(w in low for w in ("unsubscribe", "stop emailing", "remove me", "take me off")):
                db.add_optout(from_hdr)
                db.log_activity("optout", from_hdr, lead["id"])
                conn.store(num, "+FLAGS", "\\Seen")
                continue

            history = "\n".join(f"{m['role'].upper()}: {m['body']}" for m in db.recent_messages(cid, 12))
            reply = _ai_reply(lead, history, body) or (
                f"Thanks for getting back to me! Happy to share more - what's the biggest thing you'd want fixed first?"
            )
            fake_lead = lead  # reuse sender path
            send_email(fake_lead, f"Re: {subj}".replace("Re: Re:", "Re:"), reply)
            db.update_lead(lead["id"], status="engaging")
            handled += 1
            conn.store(num, "+FLAGS", "\\Seen")
        conn.logout()
    except Exception as e:
        log.warning(f"inbox poll failed: {e}")
    return handled
