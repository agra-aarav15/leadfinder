"""SQLite storage layer - free forever, zero config."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DATA_DIR, "leadfinder.db")

_local = threading.local()


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = get_conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT DEFAULT 'discovery',          -- discovery | website | inbound_email | manual
            company TEXT,
            domain TEXT UNIQUE,
            contact_name TEXT,
            email TEXT,
            phone TEXT,
            socials TEXT DEFAULT '{}',                -- JSON {instagram, facebook, twitter, linkedin}
            location TEXT,
            niche TEXT,
            score INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',                -- new|contacted|engaging|qualified|converted|lost|opted_out
            channel TEXT,                             -- last active channel: email|chat|whatsapp|dm
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            last_contact_at TEXT,
            next_followup_at TEXT,
            touches INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER REFERENCES leads(id),
            visitor_id TEXT UNIQUE,                   -- anonymous web-visitor session key
            channel TEXT DEFAULT 'chat',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES conversations(id),
            role TEXT,                                -- user | agent | system
            body TEXT,
            meta TEXT DEFAULT '{}',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS optouts (
            contact TEXT UNIQUE,                      -- email or phone or handle
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,                                -- discover|email_sent|reply_received|chat|convert|error...
            detail TEXT,
            lead_id INTEGER,
            created_at TEXT
        );
        """
    )
    c.commit()
    defaults = {
        "niche": "digital marketing agency",
        "location": "",
        "offer": "we help businesses grow with done-for-you marketing",
        "brand_name": "Alex",
        "company_name": "GrowthLab",
        "max_touches": "3",
        "followup_days": "3",
        "daily_email_limit": "40",
        "brain_disclosure": "1",
        "auto_hunt": "1",
        "auto_hunt_hours": "12",
        "use_maps": "1",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    c.commit()


def log_activity(kind: str, detail: str = "", lead_id: int | None = None):
    c = get_conn()
    c.execute(
        "INSERT INTO activity(kind,detail,lead_id,created_at) VALUES(?,?,?,?)",
        (kind, detail[:500], lead_id, now()),
    )
    c.commit()


def get_settings() -> dict:
    return {r["key"]: r["value"] for r in get_conn().execute("SELECT key,value FROM settings")}


def set_setting(key: str, value: str):
    c = get_conn()
    c.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    c.commit()


def is_opted_out(*contacts: str) -> bool:
    vals = [c.lower().strip() for c in contacts if c]
    if not vals:
        return False
    q = ",".join("?" * len(vals))
    rows = get_conn().execute(f"SELECT 1 FROM optouts WHERE LOWER(contact) IN ({q})", vals).fetchall()
    return len(rows) > 0


def add_optout(contact: str):
    c = get_conn()
    c.execute("INSERT OR IGNORE INTO optouts(contact,created_at) VALUES(?,?)", (contact.strip().lower(), now()))
    # also flip any matching lead
    c.execute(
        "UPDATE leads SET status='opted_out', updated_at=? WHERE LOWER(email)=? OR LOWER(phone)=?",
        (now(), contact.strip().lower(), contact.strip().lower()),
    )
    c.commit()


# ---------- leads ----------

def upsert_lead(data: dict) -> tuple[int, bool]:
    """Insert lead keyed on unique domain/email; returns (id, created?)."""
    c = get_conn()
    data = {k: v for k, v in data.items() if v not in (None, "")}
    socials = data.pop("socials", None)
    keys = ["source", "company", "domain", "contact_name", "email", "phone", "location", "niche", "score", "status", "channel"]
    payload = {k: data[k] for k in keys if k in data}
    payload["updated_at"] = now()

    existing = None
    if payload.get("domain"):
        existing = c.execute("SELECT id FROM leads WHERE domain=?", (payload["domain"],)).fetchone()
    if not existing and payload.get("email"):
        existing = c.execute("SELECT id FROM leads WHERE LOWER(email)=LOWER(?)", (payload["email"],)).fetchone()

    if existing:
        sets = ", ".join(f"{k}=?" for k in payload)
        c.execute(f"UPDATE leads SET {sets} WHERE id=?", (*payload.values(), existing["id"]))
        c.commit()
        return existing["id"], False

    payload.setdefault("created_at", now())
    payload.setdefault("status", "new")
    cols = list(payload.keys())
    c.execute(
        f"INSERT INTO leads({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
        list(payload.values()),
    )
    lid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    if socials:
        c.execute("UPDATE leads SET socials=? WHERE id=?", (json.dumps(socials), lid))
    c.commit()
    return lid, True


def update_lead(lid: int, **fields):
    fields["updated_at"] = now()
    c = get_conn()
    sets = ", ".join(f"{k}=?" for k in fields)
    c.execute(f"UPDATE leads SET {sets} WHERE id=?", (*fields.values(), lid))
    c.commit()


def get_lead(lid: int):
    return get_conn().execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()


def find_lead_by_email(email: str):
    return get_conn().execute("SELECT * FROM leads WHERE LOWER(email)=LOWER(?)", (email,)).fetchone()


def find_lead_by_company(name: str):
    return get_conn().execute(
        "SELECT id FROM leads WHERE LOWER(company)=LOWER(?)", (name.strip(),)
    ).fetchone()


def find_lead_by_visitor(visitor_id: str):
    return get_conn().execute(
        """SELECT l.* FROM leads l JOIN conversations cv ON cv.lead_id=l.id WHERE cv.visitor_id=?""",
        (visitor_id,),
    ).fetchone()


def list_leads(q: str = "", status: str = "", limit: int = 300):
    sql = "SELECT * FROM leads WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if q:
        like = f"%{q}%"
        sql += " AND (company LIKE ? OR email LIKE ? OR domain LIKE ? OR location LIKE ?)"
        args += [like, like, like, like]
    sql += " ORDER BY score DESC, updated_at DESC LIMIT ?"
    args.append(limit)
    return get_conn().execute(sql, args).fetchall()


# ---------- conversations / messages ----------

def get_or_create_conversation(visitor_id: str, lead_id: int | None = None, channel: str = "chat"):
    c = get_conn()
    row = c.execute("SELECT * FROM conversations WHERE visitor_id=?", (visitor_id,)).fetchone()
    if row:
        if lead_id and not row["lead_id"]:
            c.execute("UPDATE conversations SET lead_id=? WHERE id=?", (lead_id, row["id"]))
            c.commit()
        return row["id"]
    c.execute(
        "INSERT INTO conversations(lead_id,visitor_id,channel,created_at) VALUES(?,?,?,?)",
        (lead_id, visitor_id, channel, now()),
    )
    cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit()
    return cid


def add_message(conversation_id: int, role: str, body: str, meta: dict | None = None):
    c = get_conn()
    c.execute(
        "INSERT INTO messages(conversation_id,role,body,meta,created_at) VALUES(?,?,?,?,?)",
        (conversation_id, role, body, json.dumps(meta or {}), now()),
    )
    c.commit()


def recent_messages(conversation_id: int, n: int = 20):
    rows = get_conn().execute(
        "SELECT role,body,created_at FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
        (conversation_id, n),
    ).fetchall()
    return list(reversed(rows))


def conversation_messages(conversation_id: int):
    return get_conn().execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC", (conversation_id,)
    ).fetchall()


def attach_lead_to_visitor(visitor_id: str, lead_id: int):
    c = get_conn()
    c.execute("UPDATE conversations SET lead_id=? WHERE visitor_id=?", (lead_id, visitor_id))
    c.commit()


# ---------- stats / activity ----------

def stats() -> dict:
    c = get_conn()
    out = {}
    for st in ["new", "contacted", "engaging", "qualified", "converted", "lost", "opted_out"]:
        out[st] = c.execute("SELECT COUNT(*) FROM leads WHERE status=?", (st,)).fetchone()[0]
    out["total"] = sum(out.values())
    out["messages_today"] = c.execute(
        "SELECT COUNT(*) FROM messages WHERE created_at >= date('now')"
    ).fetchone()[0]
    return out


def recent_activity(n: int = 30):
    return get_conn().execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (n,)).fetchall()
