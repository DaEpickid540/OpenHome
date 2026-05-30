"""
openHome Scheduler
───────────────────
Cron-style automation engine. Runs on the Pi alongside the hub.

Rules are stored in storage["schedules"] and survive reboots.
Each rule:
  {
    "id":          str,
    "name":        str,         # human label
    "cron":        str,         # standard cron expression: "0 7 * * 1-5"
    "actions":     list[dict],  # same action format the AI uses
    "enabled":     bool,
    "last_run":    str,         # ISO timestamp
    "created_by":  str,         # "user" | "ai" | "system"
  }

NL → cron conversion:
  "every day at 7am"         → "0 7 * * *"
  "weekdays at 7:30am"       → "30 7 * * 1-5"
  "every night at 11pm"      → "0 23 * * *"
  "every hour"               → "0 * * * *"
  "every Monday at 8am"      → "0 8 * * 1"
  "every 30 minutes"         → "*/30 * * * *"

Install: pip install apscheduler
"""

import asyncio
import re
from datetime import datetime

import storage

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APScheduler = True
except ImportError:
    _HAS_APScheduler = False
    print("[SCHED] apscheduler not installed — run: pip install apscheduler")

_scheduler = None


# ── NL → CRON ────────────────────────────────────────────
_DAY_MAP = {
    "monday":1,"tuesday":2,"wednesday":3,"thursday":4,
    "friday":5,"saturday":6,"sunday":0,
    "mon":1,"tue":2,"wed":3,"thu":4,"fri":5,"sat":6,"sun":0
}

def nl_to_cron(text: str) -> str | None:
    """Convert natural language schedule to cron expression. Returns None if unparseable."""
    t = text.lower().strip()

    # "every N minutes"
    m = re.search(r"every (\d+) min", t)
    if m: return f"*/{m.group(1)} * * * *"

    # "every hour"
    if "every hour" in t: return "0 * * * *"

    # "every day / daily"
    is_daily    = "every day" in t or "daily" in t or "every night" in t or "every morning" in t
    is_weekdays = "weekday" in t or "monday through friday" in t or "mon-fri" in t
    is_weekends = "weekend" in t

    # Extract time "at 7:30am" "at 11pm" "7am" "23:00"
    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    hour = minute = None
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = time_match.group(3)
        if ampm == "pm" and hour != 12: hour += 12
        if ampm == "am" and hour == 12: hour = 0

    # Specific day
    day_num = None
    for name, num in _DAY_MAP.items():
        if name in t: day_num = num; break

    if hour is None: return None  # can't make a cron without a time

    if day_num is not None:
        return f"{minute} {hour} * * {day_num}"
    if is_weekdays:
        return f"{minute} {hour} * * 1-5"
    if is_weekends:
        return f"{minute} {hour} * * 0,6"
    if is_daily or "every" in t:
        return f"{minute} {hour} * * *"

    return None


def cron_to_human(cron: str) -> str:
    """Best-effort cron → readable string."""
    parts = cron.split()
    if len(parts) != 5: return cron
    min_, hr, dom, mon, dow = parts
    time_str = f"{int(hr):02d}:{int(min_):02d}" if hr.isdigit() and min_.isdigit() else f"{hr}:{min_}"
    if dow == "*":    day_str = "every day"
    elif dow == "1-5": day_str = "weekdays"
    elif dow == "0,6": day_str = "weekends"
    elif dow.isdigit():
        names = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"]
        day_str = f"every {names[int(dow)]}"
    else: day_str = f"dow={dow}"
    return f"{day_str} at {time_str}"


# ── SCHEDULE MANAGEMENT ───────────────────────────────────
def list_schedules() -> list:
    return storage.get("schedules") if "schedules" in storage._cache else []

def add_schedule(name: str, cron: str, actions: list,
                 enabled: bool = True, created_by: str = "user") -> dict:
    rule = {
        "id":         f"sched_{int(datetime.now().timestamp()*1000)}",
        "name":       name,
        "cron":       cron,
        "human_time": cron_to_human(cron),
        "actions":    actions,
        "enabled":    enabled,
        "last_run":   None,
        "created_by": created_by,
    }
    schedules = list_schedules()
    schedules.append(rule)
    storage.set_collection("schedules", schedules)
    if _scheduler and _HAS_APScheduler:
        _register_job(rule)
    print(f"[SCHED] Added: '{name}' → {cron}")
    return rule

def update_schedule(rule_id: str, patch: dict) -> dict | None:
    schedules = list_schedules()
    for i, s in enumerate(schedules):
        if s["id"] == rule_id:
            schedules[i] = {**s, **patch}
            storage.set_collection("schedules", schedules)
            if _scheduler and _HAS_APScheduler:
                _reschedule_job(schedules[i])
            return schedules[i]
    return None

def delete_schedule(rule_id: str) -> bool:
    schedules = list_schedules()
    new = [s for s in schedules if s["id"] != rule_id]
    if len(new) == len(schedules): return False
    storage.set_collection("schedules", new)
    if _scheduler and _HAS_APScheduler and _scheduler.get_job(rule_id):
        _scheduler.remove_job(rule_id)
    return True


# ── NL SCHEDULE CREATION ──────────────────────────────────
async def create_from_nl(text: str, actions: list) -> dict | None:
    """
    Create a schedule from natural language.
    e.g. "every weekday at 7am" + [turn lights on actions]
    Returns the schedule dict or None if unparseable.
    """
    cron = nl_to_cron(text)
    if not cron:
        return None
    name = text.strip().capitalize()
    return add_schedule(name, cron, actions, created_by="ai")


# ── APSCHEDULER ENGINE ────────────────────────────────────
def _register_job(rule: dict):
    if not rule.get("enabled"): return
    try:
        _scheduler.add_job(
            _run_rule, CronTrigger.from_crontab(rule["cron"]),
            id=rule["id"], args=[rule["id"]],
            replace_existing=True, misfire_grace_time=60
        )
    except Exception as e:
        print(f"[SCHED] Failed to register job {rule['id']}: {e}")

def _reschedule_job(rule: dict):
    if _scheduler.get_job(rule["id"]):
        _scheduler.remove_job(rule["id"])
    if rule.get("enabled"):
        _register_job(rule)


async def _run_rule(rule_id: str):
    """Called by apscheduler when a rule fires."""
    from action_executor import execute_actions
    import storage as st

    schedules = list_schedules()
    rule = next((s for s in schedules if s["id"] == rule_id), None)
    if not rule or not rule.get("enabled"): return

    print(f"[SCHED] Firing: '{rule['name']}'")
    devices = st.get("devices")
    try:
        await execute_actions(rule["actions"], devices)
        update_schedule(rule_id, {"last_run": datetime.now().isoformat()})
    except Exception as e:
        print(f"[SCHED] Error running rule {rule_id}: {e}")


def start_scheduler():
    global _scheduler
    if not _HAS_APScheduler:
        print("[SCHED] apscheduler not available — scheduling disabled")
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    # Load persisted schedules
    for rule in list_schedules():
        _register_job(rule)
    _scheduler.start()
    print(f"[SCHED] Started with {len(list_schedules())} rules")


def stop_scheduler():
    global _scheduler
    if _scheduler: _scheduler.shutdown(wait=False)


# ── SEED DEFAULTS (first run) ─────────────────────────────
def seed_default_schedules():
    """Add sensible defaults if no schedules exist yet."""
    if list_schedules():
        return  # already have some
    defaults = [
        ("Good morning", "0 7 * * 1-5",
         [{"action":"all_lights","mode":"on"},
          {"action":"set_thermostat","device_id":"thermostat_main","setpoint":71,"mode":"auto"}],
         False),   # disabled by default — user opts in
        ("Goodnight", "0 23 * * *",
         [{"action":"all_lights","mode":"off"},
          {"action":"set_thermostat","device_id":"thermostat_main","setpoint":68,"mode":"auto"}],
         False),
        ("Security arm (midnight)", "0 0 * * *",
         [{"action":"notify","message":"Nightly security check — all doors?","severity":"low"}],
         False),
    ]
    for name, cron, actions, enabled in defaults:
        add_schedule(name, cron, actions, enabled=enabled, created_by="system")
    print("[SCHED] Default schedules seeded (all disabled — enable in dashboard)")
