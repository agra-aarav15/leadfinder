"""The AI "brain" - fully free options only.

Priority order (auto-detected, override with LEAD_BRAIN env var):
  1. Ollama running locally (100% free forever)          -> OLLAMA_URL / LEAD_MODEL
  2. Any OpenAI-compatible endpoint (OpenRouter/Groq
     free tiers work great)                              -> OPENAI_BASE_URL + OPENAI_API_KEY + LEAD_MODEL
  3. Scripted fallback (no LLM) - deterministic but
     functional replies so the pipeline never dies.
"""
import json
import os
import re

import httpx

_state = {"provider": None, "checked": False}


def _env_provider() -> str | None:
    forced = os.getenv("LEAD_BRAIN", "").strip().lower()
    if forced in ("ollama", "openai", "none"):
        return forced if forced != "none" else None
    return None


def _detect() -> str | None:
    provider = _env_provider()
    if _state["checked"]:
        return _state["provider"]
    try:
        if provider in (None, "ollama"):
            r = httpx.get(
                os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/tags", timeout=2
            )
            if r.status_code == 200:
                models = [m.get("name", "") for m in r.json().get("models", [])]
                wanted = os.getenv("LEAD_MODEL", "")
                pick = next((m for m in models if wanted and wanted in m), None)
                _state["provider"] = ("ollama", pick or (models[0] if models else None))
                return _state["provider"]
    except Exception:
        pass
    if provider in (None, "openai") and os.getenv("OPENAI_API_KEY"):
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        _state["provider"] = ("openai", os.getenv("LEAD_MODEL", ""))
        _state.setdefault("base_url", base)
        return _state["provider"]
    _state["provider"] = None
    _state["checked"] = True
    return None


def status() -> dict:
    p = _detect()
    if not p:
        return {"mode": "scripted", "model": None,
                "note": "No LLM yet - scripted mode active. Install Ollama (free) for full AI."}
    kind, model = p
    return {"mode": kind, "model": model or "(default)",
            "note": f"Live AI via {kind}: {model}"}


def reset_cache():
    _state.update({"provider": None, "checked": False})


def ask(system: str, user: str, temperature: float = 0.7, max_tokens: int = 600) -> str | None:
    """Returns LLM text or None when unavailable/caller should fall back."""
    p = _detect()
    if not p:
        return None
    kind, model = p
    try:
        if kind == "ollama":
            r = httpx.post(
                os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "options": {"temperature": temperature},
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                },
                timeout=90,
            )
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "").strip() or None
        else:
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            r = httpx.post(
                base.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
                json={
                    "model": model or "gpt-4o-mini",
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                },
                timeout=60,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        import logging
        logging.getLogger("leadfinder").warning(f"brain error: {e}")
        return None


def extract_json(text: str):
    """Pull the first JSON object out of an LLM reply."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except Exception:
            return None
