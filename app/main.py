"""LeadFinder server: REST API + real-time chat WebSocket + workers."""
import asyncio
import json
import logging
import os

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import brain
from . import chat as chat_agent
from . import db
from . import discovery
from . import outreach

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("leadfinder")

app = FastAPI(title="LeadFinder", version="1.0")
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)


def _load_env():
    """Tiny .env loader (no dependency). Existing env vars win."""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

_state = {"discovering": False}


@app.on_event("startup")
async def startup():
    db.init_db()
    app.state.workers = [
        asyncio.create_task(_inbox_worker()),
        asyncio.create_task(_sequence_worker()),
        asyncio.create_task(_automation_worker()),
    ]
    log.info("LeadFinder up - workers running (inbox poller, follow-up sequencer, auto-hunter)")


@app.on_event("shutdown")
async def shutdown():
    for t in getattr(app.state, "workers", []):
        t.cancel()


async def _inbox_worker():
    while True:
        try:
            await asyncio.to_thread(outreach.poll_inbox_once)
        except Exception as e:
            log.warning(f"inbox worker: {e}")
        await asyncio.sleep(int(os.getenv("INBOX_POLL_SECONDS", "60")))


async def _sequence_worker():
    while True:
        try:
            await asyncio.to_thread(outreach.run_due_followups)
        except Exception as e:
            log.warning(f"sequence worker: {e}")
        await asyncio.sleep(300)


async def _automation_worker():
    """Fully-automatic mode: hunts fresh leads + sends the due outreach batch
    every auto_hunt_hours. Runs ~20s after boot, then re-checks every 10 min."""
    from datetime import datetime, timezone
    await asyncio.sleep(20)
    while True:
        try:
            s = db.get_settings()
            if s.get("auto_hunt", "1") == "1":
                hours = float(s.get("auto_hunt_hours", "12") or 12)
                due = True
                if s.get("last_auto_run"):
                    try:
                        elapsed = (datetime.now(timezone.utc)
                                   - datetime.fromisoformat(s["last_auto_run"])).total_seconds()
                        due = elapsed >= hours * 3600
                    except ValueError:
                        due = True
                if due and not _state["discovering"]:
                    db.log_activity("auto", "automatic cycle starting (discover + outreach)")
                    try:
                        res = await asyncio.to_thread(
                            discovery.discover,
                            s.get("niche", ""), s.get("location", ""), 10,
                        )
                        db.log_activity("auto", f"auto-discovery: +{res['created']} new leads")
                    except Exception as e:
                        db.log_activity("error", f"auto-discovery failed: {e}")
                    try:
                        r = await asyncio.to_thread(outreach.run_outreach_batch, 10)
                        db.log_activity("auto",
                                        f"auto-outreach: {r['sent']} sent, {r['simulated']} simulated")
                    except Exception as e:
                        db.log_activity("error", f"auto-outreach failed: {e}")
                    db.set_setting("last_auto_run", db.now())
        except Exception as e:
            log.warning(f"automation worker: {e}")
        await asyncio.sleep(600)


# ---------------- dashboard ----------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return open(os.path.join(ROOT, "dashboard", "index.html"), encoding="utf-8").read()


@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    return open(os.path.join(ROOT, "dashboard", "demo.html"), encoding="utf-8").read()


# ---------------- stats / activity / settings ----------------

@app.get("/api/stats")
async def api_stats():
    return {
        "stats": db.stats(),
        "activity": [dict(r) for r in db.recent_activity(40)],
        "brain": brain.status(),
        "mail_live": outreach.live_mode(),
        "discovering": _state["discovering"],
    }


@app.get("/api/settings")
async def get_settings():
    return db.get_settings()


@app.post("/api/settings")
async def post_settings(payload: dict):
    allowed = {"niche", "location", "offer", "brand_name", "company_name",
               "max_touches", "followup_days", "daily_email_limit", "brain_disclosure",
               "auto_hunt", "auto_hunt_hours"}
    for k, v in payload.items():
        if k in allowed:
            db.set_setting(k, str(v))
    if payload.get("recheck_brain"):
        brain.reset_cache()
    return db.get_settings()


# ---------------- leads ----------------

@app.get("/api/leads")
async def api_leads(q: str = "", status: str = ""):
    rows = db.list_leads(q=q, status=status)
    out = []
    for r in rows:
        d = dict(r)
        d["socials"] = json.loads(d.get("socials") or "{}")
        out.append(d)
    return out


@app.get("/api/leads/{lid}")
async def lead_detail(lid: int):
    lead = db.get_lead(lid)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    convs = db.get_conn().execute(
        "SELECT id FROM conversations WHERE lead_id=?", (lid,)
    ).fetchall()
    msgs = []
    for cv in convs:
        msgs += [dict(m) for m in db.conversation_messages(cv["id"])]
    msgs.sort(key=lambda m: m["created_at"])
    d = dict(lead)
    d["socials"] = json.loads(d.get("socials") or "{}")
    d["messages"] = msgs
    return d


# ---------------- discovery / outreach triggers ----------------

@app.post("/api/discover")
async def api_discover(payload: dict):
    if _state["discovering"]:
        return JSONResponse({"error": "a discovery run is already active"}, status_code=409)
    niche = payload.get("niche") or db.get_settings().get("niche", "")
    location = payload.get("location") or db.get_settings().get("location", "")
    max_leads = min(int(payload.get("max", 20)), 100)
    if not niche.strip():
        return JSONResponse({"error": "niche required"}, status_code=400)

    async def runner():
        _state["discovering"] = True
        try:
            await asyncio.to_thread(
                lambda: discovery.discover(niche, location, max_leads,
                                           progress=lambda done, total, msg: db.log_activity("discover_progress", f"{done}/{total} {msg}"))
            )
        except Exception as e:
            db.log_activity("error", f"discovery failed: {e}")
        finally:
            _state["discovering"] = False

    asyncio.create_task(runner())
    return {"started": True, "niche": niche, "location": location, "max": max_leads}


@app.post("/api/outreach/start")
async def api_outreach(payload: dict):
    limit = min(int(payload.get("limit", 10)), 50)
    res = await asyncio.to_thread(outreach.run_outreach_batch, limit)
    return res


# ---------------- real-time chat (widget) ----------------

@app.websocket("/ws/{visitor_id}")
async def ws_chat(ws: WebSocket, visitor_id: str):
    await ws.accept()
    # greeting on connect
    s = db.get_settings()
    hello = (f"Hey! 👋 I'm {s.get('brand_name', 'Alex')} from {s.get('company_name', 'the team')}. "
             "What brings you in today?")
    try:
        first = db.recent_messages(db.get_or_create_conversation(visitor_id), 1)
        if not first:
            cid = db.get_or_create_conversation(visitor_id)
            db.add_message(cid, "agent", hello)
            await ws.send_json({"from": "agent", "text": hello})
        while True:
            data = await ws.receive_text()
            res = await asyncio.to_thread(chat_agent.reply_to_visitor, visitor_id, data, "chat")
            await ws.send_json({"from": "agent", "text": res["reply"]})
    except WebSocketDisconnect:
        return


@app.post("/api/chat/{visitor_id}")
async def http_chat(visitor_id: str, payload: dict):
    res = await asyncio.to_thread(chat_agent.reply_to_visitor, visitor_id, payload.get("text", ""), "chat")
    return {"reply": res["reply"]}


# ---------------- compliance ----------------

@app.get("/unsubscribe/{lid}", response_class=PlainTextResponse)
async def unsubscribe(lid: int):
    lead = db.get_lead(lid)
    contact = None
    if lead:
        contact = lead["email"] or lead["phone"] or lead["domain"]
        db.add_optout(contact)
        db.log_activity("optout", f"unsubscribe via link: {contact}", lid)
    return ("You're unsubscribed and won't hear from us again. Sorry to have bothered you!\n"
            "(This page processed your request automatically.)")


# ---------------- widget script ----------------

@app.get("/widget.js")
async def widget_js(request: Request):
    origin = request.headers.get("origin") or f"http://{request.headers.get('host', 'localhost:8787')}"
    js = open(os.path.join(ROOT, "widget", "widget.js"), encoding="utf-8").read()
    return PlainTextResponse(js.replace("__SERVER__", origin), media_type="application/javascript")
