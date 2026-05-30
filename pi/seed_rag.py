"""
openHome RAG Seeder
────────────────────
Loads device schemas, rules, and constraints into RAG memory so the AI
can always look up what it can/cannot do for any device type.

Run automatically on hub startup (called from server.py).
Safe to re-run — checks if already seeded by looking for existing entries.

Usage:
  from seed_rag import seed_on_startup
  await seed_on_startup()
"""

import storage
import rag_memory
from device_schemas import get_rag_entries, DEVICE_SCHEMAS

SEED_MARKER = "SCHEMA_SEED_V4"   # bump version to force re-seed


async def seed_on_startup():
    """Seed RAG with device schemas if not already done."""
    memories = storage.get("ai_memory")

    # Check if already seeded with this version
    already_seeded = any(m.get("text", "").startswith(SEED_MARKER) for m in memories)
    if already_seeded:
        print(f"[RAG] Schema seed already present ({len(memories)} total memories)")
        return

    print("[RAG] Seeding device schemas into RAG memory...")
    count = 0

    # Seed marker entry
    await rag_memory.add_memory(
        f"{SEED_MARKER}: device schema knowledge base loaded",
        category="system"
    )

    # Load all schema entries
    for text, category in get_rag_entries():
        await rag_memory.add_memory(text, category=category)
        count += 1

    # Extra high-value entries for common AI mistakes
    critical_constraints = [
        ("A door_sensor with state='open' means the door is physically open. The AI did NOT open it. The AI CANNOT close it by changing the state field. Doors are physical objects.", "constraint"),
        ("A flood_sensor with state='wet' means there is real water. The AI cannot dry a floor by writing state='dry'. It can only notify the user.", "constraint"),
        ("A smoke_co_sensor with state='alert' means gas was detected. The AI cannot clear this by writing state='clear'. It can only notify and set lights to alert.", "constraint"),
        ("The rfid_lock is hardware-controlled. The whitelist is compiled into the firmware. The AI cannot grant access, deny access, or unlock the door via any command.", "constraint"),
        ("The panic_button fires when a person holds the physical button. The AI cannot trigger or cancel panic by writing state. It can only notify and set alert lighting.", "constraint"),
        ("temp_f and humidity on the thermostat are sensor readings. The AI can only change setpoint (target temp, 50-90°F) and mode (heat/cool/auto/off).", "constraint"),
        ("hvac_action on the thermostat ('heating'/'cooling'/'idle') is the physical relay state. The AI cannot set hvac_action — it changes automatically based on setpoint.", "constraint"),
        ("car_present on the garage_door is an ultrasonic sensor reading. The AI cannot change it. The AI should NOT close the garage if car_present=true.", "constraint"),
        ("For rgb_lights, color must be a hex string like '#FF6600'. Brightness must be 0-255. Mode must be: solid, rainbow, pulse, fire, or alert.", "constraint"),
        ("The all_lights action is a shortcut. mode='alert' sets all lights to flashing red. mode='off' turns everything off. mode='on' turns everything on.", "constraint"),
        ("Severity levels: none < low < medium < high < critical. Only use critical for life-safety events: smoke, CO, panic, flood.", "constraint"),
        ("Never fabricate a device_id. Only use device_ids that appear in the ACTIVE DEVICES list in the prompt.", "constraint"),
        ("If the action list would be empty because no action is needed, return []. Do not invent actions.", "constraint"),
        ("For a goodnight routine: turn all lights off, set thermostat to a comfortable sleeping temp (user preference if known, otherwise 68-70°F).", "routine"),
        ("For a good morning routine: turn all lights on, set thermostat to wake temp (user preference if known, otherwise 70-72°F).", "routine"),
        ("For movie mode: set living room lights to solid dark purple (#1A0F2E) or very dim, do not change thermostat.", "routine"),
        ("For lockdown/security mode: set all lights to alert mode, notify at high severity.", "routine"),
        ("For party mode: set all lights to rainbow mode.", "routine"),
    ]

    for text, category in critical_constraints:
        await rag_memory.add_memory(text, category=category)
        count += 1

    print(f"[RAG] Seeded {count} schema entries across {len(DEVICE_SCHEMAS)} device types")


async def reseed():
    """Force re-seed by removing existing schema entries and re-running."""
    memories = storage.get("ai_memory")
    schema_categories = {"device_rules", "device_schema", "device_states",
                         "command_format", "global_rule", "constraint", "routine", "system"}
    filtered = [m for m in memories if m.get("category") not in schema_categories]
    storage.set_collection("ai_memory", filtered)
    print(f"[RAG] Cleared schema entries. {len(filtered)} user memories preserved.")
    await seed_on_startup()
