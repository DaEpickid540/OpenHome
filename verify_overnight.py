"""
Verifies the trained OpenHome model against 10 hand-crafted test cases.
Outputs a JSON verdict on the last line (parsed by run_overnight.py).
"""

import json, sys, os, torch, io
# Force UTF-8 stdout so Unicode chars don't crash on Windows cp1252
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

HF_TOKEN = os.getenv("HF_TOKEN", "")
BASE     = "Qwen/Qwen2.5-0.5B-Instruct"
LORA_DIR = "openhome-model/lora"

SYSTEM = """You are NOVA, the AI brain of a local smart home security system called openHome.
You receive real-time sensor events and the current state of all devices in your home.
Reply ONLY with a valid JSON array of action objects. No explanation, no preamble, no markdown.
If no action is needed, reply with [].

ALLOWED ACTIONS:
  {"action": "control_device", "device_id": "...", "state": "on|off|toggle|open|close"}
  {"action": "set_thermostat", "device_id": "...", "setpoint": 72.0, "mode": "heat|cool|auto|off"}
  {"action": "set_light_mode", "device_id": "...", "mode": "solid|rainbow|pulse|fire|alert", "color": "#RRGGBB"}
  {"action": "notify", "message": "...", "severity": "low|medium|high|critical"}
  {"action": "log", "message": "..."}
  {"action": "all_lights", "mode": "alert|off|on"}

CONSTRAINTS:
- controllable=false means you CANNOT send any command to that device
- Never fabricate device_ids - only use ids present in ACTIVE DEVICES
- Never set thermostat setpoint below 50 or above 90"""

VALID_ACTIONS = {"control_device", "set_thermostat", "set_light_mode", "notify", "log", "all_lights"}

# 10 test cases covering the main scenario categories in training data
TEST_CASES = [
    {
        "desc": "Panic button -> critical alert + lights",
        "user": (
            'Current time: 02:30 (night)\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"panic_bedroom","type":"panic_button","location":"bedroom",'
            '"state":"panic","severity":"critical","controllable":false,"writable":[],"readable":["state"],"panic_count":1}]\n\n'
            'TRIGGERING EVENT:\n{"device_id":"panic_bedroom","state":"panic","severity":"critical"}\n\n'
            'What actions should be taken? Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") == "notify" and a.get("severity") == "critical" for a in acts),
            "expected critical notify"
        ),
    },
    {
        "desc": "Smoke detected -> critical notify",
        "user": (
            'Current time: 14:00 (afternoon)\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"smoke_kitchen","type":"smoke_co_sensor","location":"kitchen",'
            '"state":"alert","severity":"high","controllable":false,"smoke_detected":true,"co_detected":false}]\n\n'
            'TRIGGERING EVENT:\n{"device_id":"smoke_kitchen","state":"alert","smoke_detected":true}\n\n'
            'What actions should be taken? Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") == "notify" and a.get("severity") in ("critical","high") for a in acts),
            "expected high/critical notify for smoke"
        ),
    },
    {
        "desc": "NL 'lights off' -> all_lights off",
        "user": (
            'The user sent a natural language command.\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"lights_living_room","type":"rgb_lights","location":"living_room",'
            '"state":"on","controllable":true,"writable":["state","mode","color","brightness"]}]\n\n'
            'USER COMMAND: "lights off"\n\n'
            'Translate into actions. Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") == "all_lights" and a.get("mode") == "off" for a in acts)
            or any(a.get("action") == "control_device" and a.get("state") == "off" for a in acts),
            "expected lights-off action"
        ),
    },
    {
        "desc": "NL 'goodnight' -> lights off + thermostat",
        "user": (
            'The user sent a natural language command.\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"thermostat_main","type":"thermostat","location":"hallway",'
            '"state":"idle","controllable":true,"writable":["setpoint","mode"],"temp_f":72,"setpoint":72,"mode":"auto"}]\n\n'
            'USER COMMAND: "goodnight"\n\n'
            'Translate into actions. Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") in ("all_lights", "control_device") for a in acts),
            "expected at least a lights or thermostat action"
        ),
    },
    {
        "desc": "NL 'set temperature to 72' -> set_thermostat",
        "user": (
            'The user sent a natural language command.\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"thermostat_main","type":"thermostat","location":"hallway",'
            '"controllable":true,"writable":["setpoint","mode"],"temp_f":70}]\n\n'
            'USER COMMAND: "set temperature to 72"\n\n'
            'Translate into actions. Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") == "set_thermostat" for a in acts),
            "expected set_thermostat action"
        ),
    },
    {
        "desc": "Flood in basement -> high severity notify",
        "user": (
            'Current time: 03:00 (night)\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"flood_basement","type":"flood_sensor","location":"basement",'
            '"state":"wet","severity":"high","controllable":false,"moisture_pct":75}]\n\n'
            'TRIGGERING EVENT:\n{"device_id":"flood_basement","state":"wet","moisture_pct":75}\n\n'
            'What actions should be taken? Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") == "notify" and a.get("severity") in ("high","critical") for a in acts),
            "expected high severity notify for flood"
        ),
    },
    {
        "desc": "NL 'thanks' -> no-op []",
        "user": (
            'The user sent a natural language command.\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"thermostat_main","type":"thermostat","controllable":true}]\n\n'
            'USER COMMAND: "thanks"\n\n'
            'Translate into actions. Reply with JSON array only.'
        ),
        "check": lambda acts: (
            len(acts) == 0 or all(a.get("action") == "log" for a in acts),
            "expected empty or log-only for no-op"
        ),
    },
    {
        "desc": "Door open at night -> medium notify",
        "user": (
            'Current time: 01:30 (night)\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"door_front_door","type":"door_sensor","location":"front_door",'
            '"state":"open","severity":"medium","controllable":false}]\n\n'
            'TRIGGERING EVENT:\n{"device_id":"door_front_door","state":"open"}\n\n'
            'What actions should be taken? Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") in ("notify","log") for a in acts),
            "expected notify or log for door open"
        ),
    },
    {
        "desc": "NL 'movie mode' -> set_light_mode",
        "user": (
            'The user sent a natural language command.\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"lights_living_room","type":"rgb_lights","location":"living_room",'
            '"controllable":true,"writable":["state","mode","color"]}]\n\n'
            'USER COMMAND: "movie mode"\n\n'
            'Translate into actions. Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") in ("set_light_mode", "control_device") for a in acts),
            "expected a light action for movie mode"
        ),
    },
    {
        "desc": "NL 'I'm home' -> lights on",
        "user": (
            'The user sent a natural language command.\n\n'
            'ACTIVE DEVICES:\n[{"device_id":"thermostat_main","type":"thermostat","controllable":true,'
            '"writable":["setpoint","mode"]}]\n\n'
            "USER COMMAND: \"I'm home\"\n\n"
            'Translate into actions. Reply with JSON array only.'
        ),
        "check": lambda acts: (
            any(a.get("action") in ("all_lights", "control_device", "set_thermostat") for a in acts),
            "expected at least one action for arriving home"
        ),
    },
]


def run_inference(model, tok, user_msg: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user",   "content": user_msg},
    ]
    prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=1024)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=150,
            do_sample=False,
            temperature=1.0,
            pad_token_id=tok.eos_token_id,
        )
    generated = tok.decode(
        out[0][inputs["input_ids"].shape[1]:],
        skip_special_tokens=True,
    ).strip()
    return generated


if __name__ == "__main__":
    if not Path(LORA_DIR).exists():
        print("ERROR: No trained model found at openhome-model/lora/", flush=True)
        print('{"usable": false, "error": "no model found", "json_rate": 0, "action_rate": 0}')
        sys.exit(1)

    print("Loading base model + LoRA for verification...", flush=True)
    tok = AutoTokenizer.from_pretrained(BASE, token=HF_TOKEN or None)
    tok.pad_token = tok.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(
        BASE, torch_dtype=torch.float32, token=HF_TOKEN or None
    )
    model = PeftModel.from_pretrained(base_model, LORA_DIR)
    model.eval()

    total    = len(TEST_CASES)
    json_ok  = 0
    passed   = 0

    print(f"\n{'='*60}", flush=True)
    print(f"Running {total} verification tests", flush=True)
    print(f"{'='*60}", flush=True)

    for i, case in enumerate(TEST_CASES):
        raw = run_inference(model, tok, case["user"])

        # JSON parse check
        try:
            actions = json.loads(raw)
            if not isinstance(actions, list):
                raise ValueError("not a list")
            json_ok += 1
        except Exception:
            actions = []
            print(f"  [{i+1:2d}/{total}] FAIL (invalid JSON) | {case['desc']}", flush=True)
            print(f"         Raw: {raw[:120]}", flush=True)
            continue

        # Action validity check
        invalid = [a.get("action") for a in actions if a.get("action") not in VALID_ACTIONS]
        if invalid:
            print(f"  [{i+1:2d}/{total}] FAIL (bad action types: {invalid}) | {case['desc']}", flush=True)
            print(f"         Raw: {raw[:120]}", flush=True)
            continue

        ok, reason = case["check"](actions)
        if ok:
            passed += 1
            print(f"  [{i+1:2d}/{total}] PASS | {case['desc']}", flush=True)
        else:
            print(f"  [{i+1:2d}/{total}] FAIL ({reason}) | {case['desc']}", flush=True)
        print(f"         Output: {raw[:120]}", flush=True)

    json_rate   = json_ok / total
    action_rate = passed  / total
    usable      = json_rate >= 0.8 and action_rate >= 0.7

    print(f"\n{'='*60}", flush=True)
    print(f"VERDICT: {'USABLE' if usable else 'NOT USABLE'}", flush=True)
    print(f"  JSON valid:   {json_ok}/{total} ({json_rate:.0%})", flush=True)
    print(f"  Tests passed: {passed}/{total} ({action_rate:.0%})", flush=True)
    print(f"  Threshold:    80% JSON + 70% accuracy", flush=True)
    print(f"{'='*60}\n", flush=True)

    # Last line is machine-readable for the orchestrator
    print(json.dumps({
        "usable":      usable,
        "json_rate":   json_rate,
        "action_rate": action_rate,
        "json_ok":     json_ok,
        "passed":      passed,
        "total":       total,
    }))
