# ⚡ LeadFinder

> **The vision:** every business deserves a tireless growth team. LeadFinder is that
> team as software — an autonomous employee that **hunts** real businesses on the map
> and the open web, **pitches** them in your voice, **converses** with them in real
> time until they're ready, **closes** the ones who bite, and files every detail —
> name, email, phone, transcript, score — in a command center you actually enjoy opening.
> It runs on free infrastructure forever, plays by the rules (opt-out, disclosure,
> rate limits), and never sleeps.

An autonomous lead-generation and conversion pipeline: it **finds** businesses on the map
and open web, **reaches out**, **talks to them in real time** (outreach replies + live chat
widget on your own site), **qualifies & converts** them, and shows you every detail in a
command-center dashboard.

Built 100% on free-forever infrastructure: SQLite, headless-browser scraping, local AI via
Ollama, and a free mailbox. No paid services required.

---

## Where it goes (roadmap)

- **Phase 1 — shipped.** Maps + web discovery, AI email sequences with auto-replies,
  real-time site chat agent, auto-hunt scheduler, compliance engine, glass dashboard.
- **Phase 2 — plug in and go.** Install Ollama (`ollama pull llama3.1`) for genuinely
  human-like conversation; add a free Gmail app-password to `.env` to move from dry-run
  to live sending.
- **Phase 3 — more channels.** Voice calling via AI phone adapters (Twilio/Vapi),
  WhatsApp Cloud API, and browser-automated social DMs — each slots into the same
  conversation/lead pipeline.
- **Phase 4 — 24/7 in the cloud.** Deploy to any free-tier VM so hunting continues
  while your laptop sleeps; multi-client/white-label mode for agencies.

---

## Quick start

```bash
cd F:\lead
python -m venv .venv                 # first time only
.venv\Scripts\pip install -r requirements.txt   # first time only
.venv\Scripts\python run.py
```

Then open:

| URL | What |
|---|---|
| http://localhost:8787 | Dashboard (command center) |
| http://localhost:8787/demo | Demo website with the live chat widget installed |

## The loop

1. **Discover** — dashboard → *Discover* → enter niche + location.
   **Source 1 — Google Maps (headless Chromium):** real local businesses with name,
   address, phone and website, then each website is crawled for emails/socials and scored.
   **Source 2 — multi-engine web search:** DuckDuckGo → Bing top-up when needed
   (with automatic backoff if engines throttle). **Auto-hunt mode (on by default)**
   repeats everything every N hours with zero clicks.
2. **Outreach** — *Overview* → "Send first-touch batch". Emails are personalized by AI,
   include an unsubscribe link, and follow-ups are sent automatically every N days
   (up to max touches). **Dry-run by default** — nothing real is sent until you connect a mailbox.
3. **Converse & convert** — two ways:
   - A lead **replies to your email** → the inbox poller reads it, threads it to the lead,
     and the AI answers in your voice, moving them toward a call. Unsubscribe words are honored instantly.
   - A visitor **chats on your website** → the widget connects over WebSocket and the agent
     chats human-to-human style, quietly collecting name/email/phone/need and marking the
     lead `engaging` → `qualified` → `converted`.
4. **Review** — every lead has full details + complete conversation history in the dashboard.

## Give it a real AI brain (free)

Scripted mode works out of the box but is basic. For genuinely human-like conversation:

- **Best / free forever:** install [Ollama](https://ollama.com), then:
  ```bash
  ollama pull llama3.1
  ```
  It's auto-detected — no config needed.
- **Or any OpenAI-compatible free API** (OpenRouter / Groq free tiers): set
  `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `LEAD_MODEL` in `.env`.

## Send real email for free

Use a free Gmail account:

1. Enable 2-Step Verification, then create an **App password**
   (Google Account → Security → App passwords).
2. Copy `.env.example` to `.env` and set:
   ```
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=you@gmail.com
   SMTP_PASS=your-app-password
   IMAP_HOST=imap.gmail.com
   ```
3. Restart. The dashboard badge flips from `dry-run` to `LIVE`, and replies are answered automatically.

Keep `daily_email_limit` modest (default 40/day) — new mailboxes need warm-up to avoid spam folders.

## Put the chat widget on any website

```html
<script src="http://YOUR-SERVER-IP:8787/widget.js"></script>
```

That's it — floating bubble, real-time agent, auto lead capture.

## Built-in compliance (keeps you legal)

- Every outbound email carries an **unsubscribe link** (`/unsubscribe/{lead_id}`) that
  immediately blocks future contact.
- Reply-based opt-outs ("unsubscribe", "remove me") are detected and enforced.
- Opted-out emails/domains can never be re-contacted by discovery or outreach.
- The chat agent discloses it's an AI when directly asked (required in several jurisdictions).

## Architecture

```
run.py                  launcher (uvicorn)
app/
  db.py                 SQLite storage (leads, conversations, messages, optouts, activity)
  brain.py              LLM abstraction: Ollama → OpenAI-compatible → scripted fallback
  browser_source.py     Google Maps scraper via headless Chromium (Playwright)
  discovery.py          maps-first discovery + multi-engine search + site crawler + scorer
  outreach.py           SMTP sender, IMAP inbox poller, follow-up sequencer
  chat.py               real-time conversation agent (qualify + extract + convert)
  main.py               FastAPI: REST API, WebSocket, background workers, auto-hunter
dashboard/index.html    command-center UI (no build step)
widget/widget.js        embeddable live-chat script
tests/ws_smoke_test.py  WebSocket smoke test
data/leadfinder.db      your data (delete this file to reset)
```

Note on search: free engines throttle bots. The system rotates engines and backs off
automatically; if hunts keep coming back empty, wait ~15 minutes or run smaller hunts.
(An optional headless-browser upgrade removes this ceiling entirely — ask for it.)

## Roadmap (plugged-in later, same pipeline)

- **Voice calls** — text engine already produces the conversation logic; add Twilio/Vapi adapter.
- **Instagram/Facebook/X DMs** — no free official APIs exist; adapters slot into `outreach.py`
  using the browser automation route. Note: against platform ToS for automation — risk of ban.
- **WhatsApp/SMS** — official WhatsApp Cloud API (paid per conversation after free tier).

## Honest limits

- Discovery scrapes public pages; some sites block bots (empty result = skipped, not an error).
- Scripted mode (no LLM) is deliberately simple — install Ollama for the real experience.
- Cold email laws vary by country (CAN-SPAM/GDPR/…). The compliance features help, but
  sending responsibly (low volume, targeted, honest copy) is on you.
