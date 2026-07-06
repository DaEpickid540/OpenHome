"""
openHome AI Brain
─────────────────
Wraps a locally-running LLM (via Ollama) and provides:
  - query_llm(alert, devices)  → structured action list
  - natural_language_cmd(text) → execute free-form commands
  - get_log()                  → recent reasoning history

Now integrated with:
  - storage.py   (persistent reasoning log + settings)
  - rag_memory.py (injects household context into prompts)

The AI's name comes from settings (default "NOVA").
"""

import json
import os
import httpx
from datetime import datetime

import storage
import rag_memory

OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
MAX_TOKENS   = 512
TIMEOUT_S    = 30
CONTEXT_EVENTS = 8

recent_events = []   # rolling window (also persisted via reasoning_log)


def _model_name():
    return storage.get_setting("model_name", "llama3.1:1b")

def _ai_name():
    return storage.get_setting("ai_name", "NOVA")

def _system_prompt():
    name = _ai_name()
    return f"""You are {name}, the AI brain of a local smart home security system called openHome.
You receive real-time sensor events and the current state of all devices.
Your job is to decide what actions to take in response.

RULES:
- Reply ONLY with a valid JSON array of action objects. No explanation, no preamble, no markdown.
- If no action is needed, reply with an empty array: []
- Never make up device IDs. Only use device_ids from the provided device list.
- Actions must be from the ALLOWED ACTIONS list below.
- Use KNOWN HOUSEHOLD CONTEXT to personalize decisions (preferred temps, routines, pets).

ALLOWED ACTIONS:
  {{"action": "control_device", "device_id": "...", "state": "on|off|toggle|open|close"}}
  {{"action": "set_thermostat", "device_id": "...", "setpoint": 72, "mode": "heat|cool|auto|off"}}
  {{"action": "set_light_mode", "device_id": "...", "mode": "alert|rainbow|pulse|fire|solid", "color": "#RRGGBB"}}
  {{"action": "notify", "message": "...", "severity": "low|medium|high|critical"}}
  {{"action": "log", "message": "..."}}
  {{"action": "all_lights", "mode": "alert|off|on"}}

REASONING GUIDE:
- Panic button -> notify critical + all_lights alert
- Smoke/CO -> notify critical + all_lights alert
- Flood -> notify high
- Door open at night (after 10pm) -> notify medium
- Door open during day -> log only
- Motion at night -> notify medium
- Denied RFID scan -> notify high
- Doorbell ring -> notify low
- Climate/thermostat: adjust setpoint toward household preferred temp if known
- Air quality hazardous -> notify high + log
- Garage left open -> notify medium
"""


# ── MAIN ENTRY ────────────────────────────────────────────
async def query_llm(alert: dict, devices: dict) -> list[dict]:
    recent_events.append(alert)
    if len(recent_events) > CONTEXT_EVENTS:
        recent_events.pop(0)

    device_summary = _summarize_devices(devices)

    hour = datetime.now().hour
    n_start = storage.get_setting("night_start_hour", 22)
    n_end   = storage.get_setting("night_end_hour", 6)
    is_night = hour >= n_start or hour < n_end
    time_ctx = "night" if is_night else ("morning" if hour < 12 else ("afternoon" if hour < 18 else "evening"))

    # RAG: pull relevant household context
    query_text = f"{alert.get('type','')} {alert.get('location','')}"
    context_block = await rag_memory.get_context_block(query_text)

    prompt = f"""Current time: {datetime.now().strftime('%H:%M')} ({time_ctx})

{context_block}

ACTIVE DEVICES:
{json.dumps(device_summary, indent=2)}

RECENT EVENTS (last {len(recent_events)}):
{json.dumps(recent_events[-CONTEXT_EVENTS:], indent=2)}

TRIGGERING EVENT:
{json.dumps(alert, indent=2)}

What actions should be taken? Reply with JSON array only."""

    raw = await _call_ollama(prompt)
    actions = _parse_actions(raw)

    storage.append("reasoning_log", {
        "timestamp": datetime.now().isoformat(),
        "event":     alert,
        "raw":       raw,
        "actions":   actions
    })

    print(f"[AI] Event: {alert.get('type')} | Actions: {len(actions)}")
    for a in actions:
        print(f"  -> {a}")
    return actions


async def natural_language_cmd(text: str, devices: dict) -> list[dict]:
    device_summary = _summarize_devices(devices)
    context_block = await rag_memory.get_context_block(text)

    prompt = f"""The user sent a natural language command to their smart home.

{context_block}

ACTIVE DEVICES:
{json.dumps(device_summary, indent=2)}

USER COMMAND: "{text}"

Translate this into the appropriate actions. Reply with JSON array only."""

    raw = await _call_ollama(prompt)
    actions = _parse_actions(raw)

    storage.append("reasoning_log", {
        "timestamp": datetime.now().isoformat(),
        "event":     {"type": "nl_command", "text": text},
        "raw":       raw,
        "actions":   actions
    })

    print(f"[AI] NL cmd: '{text}' | Actions: {len(actions)}")
    return actions


def get_log(limit: int = 20) -> list:
    return storage.get("reasoning_log")[-limit:]


# ── OLLAMA CALL ───────────────────────────────────────────
async def _call_ollama(user_prompt: str) -> str:
    payload = {
        "model":  _model_name(),
        "prompt": f"{_system_prompt()}\n\n{user_prompt}",
        "stream": False,
        "options": {"num_predict": MAX_TOKENS, "temperature": 0.1, "top_p": 0.9}
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(OLLAMA_URL, json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "[]").strip()
    except httpx.TimeoutException:
        print("[AI] Ollama timeout — returning empty actions")
        return "[]"
    except Exception as e:
        print(f"[AI] Ollama error: {e}")
        return await _call_groq_fallback(user_prompt)


# ── GROQ FALLBACK (cloud) ─────────────────────────────────
# Used only when Ollama is unreachable AND GROQ_API_KEY is set in the
# environment. Keeps the hub usable while the local model is down
# (e.g. the GPU is busy fine-tuning). Never hardcode the key here.
GROQ_URL   = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"

async def _call_groq_fallback(user_prompt: str) -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return "[]"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            resp = await client.post(GROQ_URL,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": GROQ_MODEL,
                    "messages": [
                        {"role": "system", "content": _system_prompt()},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": MAX_TOKENS,
                    "temperature": 0.1,
                })
            resp.raise_for_status()
            print("[AI] Ollama down — answered via Groq fallback")
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI] Groq fallback error: {e}")
        return "[]"


# ── PARSE + VALIDATE ──────────────────────────────────────
def _parse_actions(raw: str) -> list[dict]:
    clean = raw.strip()
    if "```" in clean:
        lines = clean.split("\n")
        clean = "\n".join(l for l in lines if not l.strip().startswith("```"))

    start = clean.find("[")
    end   = clean.rfind("]") + 1
    if start == -1 or end == 0:
        print(f"[AI] No JSON array found in: {clean[:100]}")
        return []

    try:
        actions = json.loads(clean[start:end])
    except json.JSONDecodeError as e:
        print(f"[AI] JSON parse error: {e} | raw: {clean[:200]}")
        return []

    if not isinstance(actions, list):
        return []

    valid = []
    allowed = {"control_device", "set_thermostat", "set_light_mode", "notify", "alert", "log", "all_lights"}
    for a in actions:
        if not isinstance(a, dict): continue
        if a.get("action") not in allowed: continue
        valid.append(a)
    return valid


# ── DEVICE SUMMARY ────────────────────────────────────────
def _summarize_devices(devices: dict) -> list[dict]:
    skip_keys = {"last_seen", "registered", "boot_count", "raw_level",
                 "aqi_raw", "aqi_smooth", "smoke_raw", "co_raw", "timestamp",
                 "embedding"}
    out = []
    for did, d in devices.items():
        summary = {"device_id": did}
        for k, v in d.items():
            if k not in skip_keys:
                summary[k] = v
        out.append(summary)
    return out
