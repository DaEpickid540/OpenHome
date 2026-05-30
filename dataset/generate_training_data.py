"""
openHome Training Data Generator v2
─────────────────────────────────────
Generates instruction-tuning examples teaching the AI:
  1. EVENT reasoning — sensor event + home state → correct action JSON
  2. NL COMMANDS     — free-form text → correct action JSON
  3. CONSTRAINT examples — what NOT to do (read-only field violations)
  4. EDGE CASES — boundary conditions, conflicting signals, time-of-day logic

All examples use the exact system prompt + payload format the live hub sends,
so the model learns the real contract.

Output: dataset/openhome_train.jsonl + dataset/openhome_val.jsonl
"""

import json, random, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pi'))
from device_schemas import DEVICE_SCHEMAS, GLOBAL_RULES

random.seed(42)

# ── SYSTEM PROMPT ─────────────────────────────────────────
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

CONSTRAINTS (you MUST follow these):
- controllable=false means you CANNOT send any command to that device
- Only write fields in that device's "writable" array
- Fields in "readable" are for context only — do not try to set them
- Never fabricate device_ids — only use ids present in ACTIVE DEVICES
- Never set thermostat setpoint below 50 or above 90
- Never close garage if car_present=true
- severity levels: none < low < medium < high < critical"""

# ── DEVICE POOLS ──────────────────────────────────────────
LOCATIONS = ["front_door","back_door","garage","kitchen","bedroom","living_room",
             "basement","hallway","office","bathroom","attic","patio","dining_room","garage"]

def thermostat(temp=70, sp=72, mode="auto", action="idle"):
    return {"device_id":"thermostat_main","type":"thermostat","location":"hallway",
            "state":action if mode!="off" else "off","severity":"none",
            "controllable":True,"writable":["setpoint","mode"],
            "readable":["temp_f","humidity","hvac_action"],
            "temp_f":temp,"humidity":45,"setpoint":sp,"mode":mode,"hvac_action":action,
            "ip":"192.168.1.53","port":80}

def light(loc, mode="solid", color="#FFD740", on=True):
    return {"device_id":f"lights_{loc}","type":"rgb_lights","location":loc,
            "state":"on" if on else "off","severity":"none",
            "controllable":True,"writable":["state","mode","color","brightness"],
            "readable":[],"mode":mode,"color":color,"brightness":128,
            "ip":f"192.168.1.{random.randint(50,99)}","port":80}

def plug(loc, on=False):
    return {"device_id":f"plug_{loc}","type":"smart_plug","location":loc,
            "state":"on" if on else "off","severity":"none",
            "controllable":True,"writable":["state"],"readable":[],
            "ip":f"192.168.1.{random.randint(50,99)}","port":80}

def door(loc, open_=False):
    return {"device_id":f"door_{loc}","type":"door_sensor","location":loc,
            "state":"open" if open_ else "closed",
            "severity":"medium" if open_ else "none",
            "controllable":False,"writable":[],"readable":["state","boot_count"],
            "boot_count":random.randint(1,200)}

def motion(loc):
    return {"device_id":f"motion_{loc}","type":"motion_sensor","location":loc,
            "state":"detected","severity":"low",
            "controllable":False,"writable":[],"readable":["state","trigger_count","boot_count"],
            "trigger_count":random.randint(1,50),"boot_count":random.randint(1,50)}

def flood(loc, wet=True):
    return {"device_id":f"flood_{loc}","type":"flood_sensor","location":loc,
            "state":"wet" if wet else "dry",
            "severity":"high" if wet else "none",
            "controllable":False,"writable":[],"readable":["state","moisture_pct","boot_count"],
            "moisture_pct":random.randint(40,95) if wet else random.randint(0,10),
            "boot_count":random.randint(1,10)}

def smoke(loc, s=True, co=False):
    sev = "critical" if (s and co) else "high" if co else "medium"
    return {"device_id":f"smoke_{loc}","type":"smoke_co_sensor","location":loc,
            "state":"alert","severity":sev,
            "controllable":False,"writable":[],
            "readable":["state","smoke_detected","co_detected","smoke_pct","co_pct","alert_count"],
            "smoke_detected":s,"co_detected":co,
            "smoke_pct":random.randint(40,90) if s else 5,
            "co_pct":random.randint(30,80) if co else 3,
            "alert_count":1,"boot_count":1}

def garage(open_=True, car=False):
    return {"device_id":"garage_door_main","type":"garage_door","location":"garage",
            "state":"open" if open_ else "closed",
            "severity":"medium" if open_ else "none",
            "controllable":True,"writable":["state"],"readable":["car_present"],
            "car_present":car,"ip":"192.168.1.54","port":80}

def panic(loc, state="panic"):
    return {"device_id":f"panic_{loc}","type":"panic_button","location":loc,
            "state":state,"severity":"critical" if state=="panic" else "none",
            "controllable":False,"writable":[],"readable":["state","panic_count","boot_count"],
            "panic_count":1,"boot_count":random.randint(10,200)}

def rfid(loc, auth=False, uid=None):
    if uid is None: uid = ''.join(random.choice("0123456789ABCDEF") for _ in range(8))
    return {"device_id":f"rfid_{loc}","type":"rfid_lock","location":loc,
            "state":"granted" if auth else "denied",
            "severity":"none" if auth else "high",
            "controllable":False,"writable":[],
            "readable":["state","uid","authorized","result"],
            "uid":uid,"authorized":auth,"result":"granted" if auth else "denied"}

def doorbell(loc, has_photo=True):
    return {"device_id":f"doorbell_{loc}","type":"doorbell","location":loc,
            "state":"ring","severity":"low",
            "controllable":False,"writable":[],
            "readable":["state","ring_count","has_photo","boot_count"],
            "ring_count":random.randint(1,20),"has_photo":has_photo,"boot_count":random.randint(1,20)}

def climate(loc, temp=72, hum=50, comfort="comfortable"):
    return {"device_id":f"climate_{loc}","type":"climate_monitor","location":loc,
            "state":comfort,"severity":"medium" if comfort=="alert" else "none",
            "controllable":False,"writable":[],"readable":["state","temp_f","humidity"],
            "temp_f":temp,"humidity":hum}

def aqi(loc, state="good", pct=20):
    sev = {"good":"none","moderate":"medium","poor":"high","hazardous":"critical"}[state]
    return {"device_id":f"airquality_{loc}","type":"air_quality","location":loc,
            "state":state,"severity":sev,
            "controllable":False,"writable":[],"readable":["state","aqi_pct","aqi_label"],
            "aqi_pct":pct,"aqi_label":state}

def make_home(extra=[]):
    """Random realistic home device set."""
    devs = [thermostat(temp=random.randint(65,80))]
    locs = random.sample(LOCATIONS, random.randint(2,4))
    for loc in locs:
        if random.random()<0.6: devs.append(light(loc))
        if random.random()<0.3: devs.append(plug(loc))
    devs.extend(extra)
    return devs

# ── PROMPT BUILDER ────────────────────────────────────────
def event_prompt(hour, devices, event):
    tod = "night" if (hour>=22 or hour<6) else ("morning" if hour<12 else ("afternoon" if hour<18 else "evening"))
    return (f"Current time: {hour:02d}:{random.randint(0,59):02d} ({tod})\n\n"
            f"ACTIVE DEVICES:\n{json.dumps(devices,indent=2)}\n\n"
            f"TRIGGERING EVENT:\n{json.dumps(event,indent=2)}\n\n"
            f"What actions should be taken? Reply with JSON array only.")

def nl_prompt(devices, command):
    return (f"The user sent a natural language command.\n\n"
            f"ACTIVE DEVICES:\n{json.dumps(devices,indent=2)}\n\n"
            f'USER COMMAND: "{command}"\n\n'
            f"Translate into actions. Reply with JSON array only.")

examples = []

def add(user, actions):
    examples.append({"messages":[
        {"role":"system","content":SYSTEM},
        {"role":"user","content":user},
        {"role":"assistant","content":json.dumps(actions)}
    ]})

# ─────────────────────────────────────────────────────────
# SECTION 1: PANIC BUTTON (80 examples)
# ─────────────────────────────────────────────────────────
for _ in range(80):
    loc = random.choice(LOCATIONS)
    hour = random.randint(0,23)
    devs = make_home(extra=[panic(loc,"panic")])
    ev = panic(loc,"panic")
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"PANIC button activated in {loc.replace('_',' ')}! Immediate attention required.","severity":"critical"},
        {"action":"all_lights","mode":"alert"}
    ])

# Panic cancelled → just log
for _ in range(30):
    loc = random.choice(LOCATIONS)
    hour = random.randint(0,23)
    devs = make_home(extra=[panic(loc,"cancelled")])
    ev = panic(loc,"cancelled")
    add(event_prompt(hour,devs,ev),[
        {"action":"log","message":"Panic alert cancelled by user"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 2: SMOKE / CO (100 examples)
# ─────────────────────────────────────────────────────────
SMOKE_LOCS = ["kitchen","bedroom","living_room","office","basement","hallway"]
for _ in range(40):  # smoke only
    loc = random.choice(SMOKE_LOCS)
    hour = random.randint(0,23)
    devs = make_home(extra=[smoke(loc,s=True,co=False)])
    ev = smoke(loc,s=True,co=False)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Smoke detected in {loc.replace('_',' ')}! Evacuate and check for fire.","severity":"critical"},
        {"action":"all_lights","mode":"alert"}
    ])
for _ in range(40):  # CO only (invisible — high risk)
    loc = random.choice(SMOKE_LOCS)
    hour = random.randint(0,23)
    devs = make_home(extra=[smoke(loc,s=False,co=True)])
    ev = smoke(loc,s=False,co=True)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Carbon monoxide detected in {loc.replace('_',' ')}! Evacuate immediately — do not breathe.","severity":"critical"},
        {"action":"all_lights","mode":"alert"}
    ])
for _ in range(20):  # both
    loc = random.choice(SMOKE_LOCS)
    hour = random.randint(0,23)
    devs = make_home(extra=[smoke(loc,s=True,co=True)])
    ev = smoke(loc,s=True,co=True)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"SMOKE AND CO detected in {loc.replace('_',' ')}! Evacuate immediately.","severity":"critical"},
        {"action":"all_lights","mode":"alert"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 3: FLOOD (60 examples)
# ─────────────────────────────────────────────────────────
for _ in range(60):
    loc = random.choice(["basement","kitchen","bathroom","garage","attic"])
    hour = random.randint(0,23)
    pct = random.randint(40,95)
    devs = make_home(extra=[flood(loc,wet=True)])
    ev = flood(loc,wet=True)
    ev["moisture_pct"] = pct
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Water detected in {loc.replace('_',' ')} ({pct}% moisture). Check for flooding.","severity":"high"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 4: DOOR OPEN (120 examples — largest set, time-of-day critical)
# ─────────────────────────────────────────────────────────
DOOR_LOCS = ["front_door","back_door","garage","patio","office","bedroom"]
for _ in range(60):  # night → notify medium
    loc = random.choice(DOOR_LOCS)
    hour = random.choice([22,23,0,1,2,3,4,5])
    devs = make_home(extra=[door(loc,open_=True)])
    ev = door(loc,open_=True)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"{loc.replace('_',' ').title()} opened at night","severity":"medium"}
    ])
for _ in range(60):  # day → log only
    loc = random.choice(DOOR_LOCS)
    hour = random.randint(7,21)
    devs = make_home(extra=[door(loc,open_=True)])
    ev = door(loc,open_=True)
    add(event_prompt(hour,devs,ev),[
        {"action":"log","message":f"{loc.replace('_',' ')} opened"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 5: MOTION (100 examples)
# ─────────────────────────────────────────────────────────
for _ in range(50):  # night
    loc = random.choice(LOCATIONS)
    hour = random.choice([22,23,0,1,2,3,4,5])
    devs = make_home(extra=[motion(loc)])
    ev = motion(loc)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Motion detected in {loc.replace('_',' ')} at night","severity":"medium"}
    ])
for _ in range(50):  # day → log
    loc = random.choice(LOCATIONS)
    hour = random.randint(7,21)
    devs = make_home(extra=[motion(loc)])
    ev = motion(loc)
    add(event_prompt(hour,devs,ev),[
        {"action":"log","message":f"Motion in {loc.replace('_',' ')}"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 6: RFID ACCESS (60 examples)
# ─────────────────────────────────────────────────────────
for _ in range(40):  # denied
    loc = random.choice(["front_door","back_door","office","garage"])
    hour = random.randint(0,23)
    uid = ''.join(random.choice("0123456789ABCDEF") for _ in range(8))
    devs = make_home(extra=[rfid(loc,auth=False,uid=uid)])
    ev = rfid(loc,auth=False,uid=uid)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Denied access attempt at {loc.replace('_',' ')} (card {uid})","severity":"high"}
    ])
for _ in range(20):  # granted → log only
    loc = random.choice(["front_door","back_door"])
    hour = random.randint(7,22)
    devs = make_home(extra=[rfid(loc,auth=True)])
    ev = rfid(loc,auth=True)
    add(event_prompt(hour,devs,ev),[
        {"action":"log","message":f"Access granted at {loc.replace('_',' ')}"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 7: DOORBELL (50 examples)
# ─────────────────────────────────────────────────────────
for _ in range(35):  # normal hours
    loc = "front_door"
    hour = random.randint(8,21)
    photo = random.random()<0.8
    devs = make_home(extra=[doorbell(loc,has_photo=photo)])
    ev = doorbell(loc,has_photo=photo)
    msg = "Someone at the front door" + (" — snapshot available" if photo else "")
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":msg,"severity":"low"}
    ])
for _ in range(15):  # late night ring → medium
    loc = "front_door"
    hour = random.choice([22,23,0,1])
    devs = make_home(extra=[doorbell(loc)])
    ev = doorbell(loc)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":"Doorbell rang at an unusual hour — check snapshot","severity":"medium"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 8: GARAGE DOOR (60 examples)
# ─────────────────────────────────────────────────────────
for _ in range(30):  # open, no car → close it + notify
    hour = random.randint(0,23)
    devs = make_home(extra=[garage(open_=True,car=False)])
    ev = garage(open_=True,car=False)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":"Garage door left open","severity":"medium"}
    ])
for _ in range(15):  # open with car → notify but do NOT close
    hour = random.randint(0,23)
    devs = make_home(extra=[garage(open_=True,car=True)])
    ev = garage(open_=True,car=True)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":"Garage door is open (car present — do not auto-close)","severity":"medium"}
    ])
for _ in range(15):  # closed → log only
    hour = random.randint(0,23)
    devs = make_home(extra=[garage(open_=False,car=random.random()<0.5)])
    ev = garage(open_=False)
    add(event_prompt(hour,devs,ev),[
        {"action":"log","message":"Garage door closed"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 9: CLIMATE + AIR QUALITY (60 examples)
# ─────────────────────────────────────────────────────────
CLIMATE_LOCS = ["bedroom","living_room","office","kitchen"]
for _ in range(25):  # climate alert → notify + suggest thermostat
    loc = random.choice(CLIMATE_LOCS)
    temp = random.randint(87,99)
    hum = random.randint(70,90)
    hour = random.randint(0,23)
    t = thermostat(temp=temp)
    devs = make_home(extra=[climate(loc,temp=temp,hum=hum,comfort="alert"),t])
    ev = climate(loc,temp=temp,hum=hum,comfort="alert")
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Temperature alert in {loc.replace('_',' ')}: {temp}°F, {hum}% humidity","severity":"medium"},
        {"action":"set_thermostat","device_id":"thermostat_main","setpoint":70,"mode":"cool"}
    ])
for _ in range(15):  # air quality poor
    loc = random.choice(CLIMATE_LOCS)
    pct = random.randint(65,80)
    hour = random.randint(0,23)
    devs = make_home(extra=[aqi(loc,"poor",pct)])
    ev = aqi(loc,"poor",pct)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Poor air quality in {loc.replace('_',' ')} ({pct}%) — open windows","severity":"high"}
    ])
for _ in range(20):  # air quality hazardous
    loc = random.choice(CLIMATE_LOCS)
    pct = random.randint(85,99)
    hour = random.randint(0,23)
    devs = make_home(extra=[aqi(loc,"hazardous",pct)])
    ev = aqi(loc,"hazardous",pct)
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"HAZARDOUS air quality in {loc.replace('_',' ')} ({pct}%) — ventilate immediately","severity":"critical"},
        {"action":"log","message":f"AQI hazardous event logged in {loc}"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 10: THERMOSTAT EVENTS (40 examples)
# ─────────────────────────────────────────────────────────
for _ in range(20):  # extreme temp → notify
    hour = random.randint(0,23)
    temp = random.choice([random.randint(91,99), random.randint(38,49)])
    devs = make_home(extra=[thermostat(temp=temp)])
    ev = thermostat(temp=temp)
    ev["type"] = "thermostat"
    add(event_prompt(hour,devs,ev),[
        {"action":"notify","message":f"Extreme indoor temperature: {temp}°F","severity":"high"}
    ])
for _ in range(20):  # normal report → log or no action
    hour = random.randint(0,23)
    temp = random.randint(65,80)
    devs = make_home()
    ev = thermostat(temp=temp)
    ev["type"] = "thermostat"
    add(event_prompt(hour,devs,ev),[
        {"action":"log","message":f"Thermostat report: {temp}°F"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 11: NL COMMANDS — LIGHTS (200 examples)
# ─────────────────────────────────────────────────────────
OFF_PHRASES = ["lights off","turn off all the lights","kill the lights","all lights off",
               "shut off every light","goodnight","lights out","turn everything off",
               "can you turn off the lights","switch off all lights","no more lights",
               "darken the house","cut the lights","lights down","everything off please"]
ON_PHRASES  = ["lights on","turn on all the lights","all lights on","turn everything on",
               "light up the house","I'm home turn on the lights","can you turn the lights on",
               "switch on all lights","brighten up","lights please","need lights",
               "illuminate the house","make it bright","lights up"]

for p in OFF_PHRASES:
    for _ in range(5):
        devs = make_home()
        add(nl_prompt(devs,p),[{"action":"all_lights","mode":"off"}])

for p in ON_PHRASES:
    for _ in range(4):
        devs = make_home()
        add(nl_prompt(devs,p),[{"action":"all_lights","mode":"on"}])

# Room-specific lights
for _ in range(80):
    loc = random.choice(LOCATIONS)
    devs = make_home(extra=[light(loc)])
    state = random.choice(["on","off"])
    verb  = "turn on" if state=="on" else "turn off"
    phrasing = random.choice([
        f"{verb} the {loc.replace('_',' ')} light",
        f"{verb} the {loc.replace('_',' ')} lights",
        f"the {loc.replace('_',' ')} light {state}",
        f"{loc.replace('_',' ')} lights {state}",
        f"can you {verb} the {loc.replace('_',' ')}",
    ])
    add(nl_prompt(devs,phrasing),[
        {"action":"control_device","device_id":f"lights_{loc}","state":state}
    ])

# Light modes
MODES = {"alert":"🚨 Emergency","rainbow":"rainbow","pulse":"pulse","fire":"fire","solid":"solid"}
for _ in range(40):
    loc = random.choice(LOCATIONS)
    mode = random.choice(["rainbow","pulse","fire"])
    devs = make_home(extra=[light(loc)])
    add(nl_prompt(devs,f"set the {loc.replace('_',' ')} lights to {mode} mode"),[
        {"action":"set_light_mode","device_id":f"lights_{loc}","mode":mode}
    ])

# Colors
COLORS = {"red":"#FF0000","blue":"#0000FF","green":"#00FF00","purple":"#7C4DFF",
          "orange":"#FF6D00","warm white":"#FFD740","pink":"#FF4081","cyan":"#00D4FF",
          "yellow":"#FFFF00","teal":"#00B8D4","white":"#FFFFFF","dark blue":"#0D47A1"}
for _ in range(60):
    loc = random.choice(LOCATIONS)
    cname,chex = random.choice(list(COLORS.items()))
    devs = make_home(extra=[light(loc)])
    phrasing = random.choice([
        f"make the {loc.replace('_',' ')} lights {cname}",
        f"set {loc.replace('_',' ')} light color to {cname}",
        f"{loc.replace('_',' ')} lights in {cname}",
        f"change {loc.replace('_',' ')} to {cname}",
    ])
    add(nl_prompt(devs,phrasing),[
        {"action":"set_light_mode","device_id":f"lights_{loc}","mode":"solid","color":chex}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 12: NL COMMANDS — THERMOSTAT (200 examples)
# ─────────────────────────────────────────────────────────
TEMP_TARGETS = list(range(64,79))
for _ in range(80):
    temp = random.choice(TEMP_TARGETS)
    devs = make_home()
    phrasing = random.choice([
        f"set the temperature to {temp}",
        f"make it {temp} degrees",
        f"set thermostat to {temp}",
        f"I want it {temp} degrees in here",
        f"temperature at {temp} please",
        f"set it to {temp}°F",
        f"can you set the heat to {temp}",
        f"thermostat {temp}",
    ])
    add(nl_prompt(devs,phrasing),[
        {"action":"set_thermostat","device_id":"thermostat_main","setpoint":float(temp)}
    ])

COLD_PHRASES = ["it's too cold","i'm freezing","make it warmer","turn up the heat",
                "can you heat it up","it's freezing in here","so cold","need more heat",
                "house is cold","warm it up","bump up the temp","a bit chilly",
                "cold in here","can you make it warmer","feels cold"]
HOT_PHRASES  = ["it's too hot","i'm sweating","make it cooler","turn on the AC",
                "it's hot in here","so hot","need AC","cool it down","house is hot",
                "lower the temp","too warm","feels hot","AC please","cool the house",
                "burning up in here"]
MODES_PHRASES = {
    "heat": ["heating mode","set to heat","heat mode please","switch to heat"],
    "cool": ["cooling mode","set to cool","AC mode","switch to AC","cool mode"],
    "auto": ["auto mode","set to auto","automatic mode","let it decide"],
    "off":  ["turn off thermostat","thermostat off","HVAC off","turn off heat and AC"]
}

for p in COLD_PHRASES:
    for _ in range(5):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"set_thermostat","device_id":"thermostat_main",
             "setpoint":random.choice([73,74,75]),"mode":"heat"}
        ])

for p in HOT_PHRASES:
    for _ in range(5):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"set_thermostat","device_id":"thermostat_main",
             "setpoint":random.choice([68,69,70]),"mode":"cool"}
        ])

for mode, phrases in MODES_PHRASES.items():
    for p in phrases:
        for _ in range(3):
            devs = make_home()
            add(nl_prompt(devs,p),[
                {"action":"set_thermostat","device_id":"thermostat_main","mode":mode}
            ])

# ─────────────────────────────────────────────────────────
# SECTION 13: NL COMMANDS — PLUGS (60 examples)
# ─────────────────────────────────────────────────────────
APPLIANCES = ["lamp","fan","coffee maker","TV","heater","device","charger","outlet"]
for _ in range(60):
    loc = random.choice(LOCATIONS)
    devs = make_home(extra=[plug(loc)])
    state = random.choice(["on","off"])
    verb  = "turn on" if state=="on" else "turn off"
    item  = random.choice(APPLIANCES)
    phrasing = random.choice([
        f"{verb} the {loc.replace('_',' ')} {item}",
        f"{verb} the {item} in the {loc.replace('_',' ')}",
        f"{loc.replace('_',' ')} {item} {state}",
        f"plug in the {loc.replace('_',' ')}",
        f"cut power to the {loc.replace('_',' ')}",
    ])
    add(nl_prompt(devs,phrasing),[
        {"action":"control_device","device_id":f"plug_{loc}","state":state}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 14: NL COMMANDS — GARAGE (40 examples)
# ─────────────────────────────────────────────────────────
for _ in range(20):  # open garage
    devs = make_home(extra=[garage(open_=False,car=False)])
    p = random.choice(["open the garage","open garage door","garage open please",
                       "open garage","can you open the garage","open up the garage"])
    add(nl_prompt(devs,p),[
        {"action":"control_device","device_id":"garage_door_main","state":"open"}
    ])
for _ in range(20):  # close garage (no car)
    devs = make_home(extra=[garage(open_=True,car=False)])
    p = random.choice(["close the garage","close garage door","shut the garage",
                       "close garage please","garage closed","shut garage"])
    add(nl_prompt(devs,p),[
        {"action":"control_device","device_id":"garage_door_main","state":"close"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 15: NL COMMANDS — ROUTINES (120 examples)
# ─────────────────────────────────────────────────────────
GOODNIGHT = ["goodnight","good night","I'm going to bed","bedtime","off to sleep",
             "I'm going to sleep","time for bed","night night","heading to bed",
             "sleep time","going to sleep now","night mode","sleep mode"]
GOODMORNING = ["good morning","morning","I'm awake","wake up the house","rise and shine",
               "morning routine","start the day","I'm up","woke up","morning mode"]
MOVIE = ["movie mode","movie night","set up for movie night","dim for movie",
         "watching a movie","movie time","cinema mode","home theater mode"]
PARTY = ["party mode","party time","let's party","party at my place","turn up",
         "get the party started","we're having a party"]
LOCKDOWN = ["lock everything down","lockdown","secure the house","security mode",
            "arm everything","I'm leaving","leaving the house","intruder alert",
            "burglar mode","nobody home mode"]
AWAY = ["I'm leaving","leaving now","bye","heading out","I'm out","leaving for work"]
ARRIVE = ["I'm home","just got home","arrived home","I'm back","just arrived","hi I'm home"]

for p in GOODNIGHT:
    for _ in range(4):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"all_lights","mode":"off"},
            {"action":"set_thermostat","device_id":"thermostat_main","setpoint":68.0,"mode":"auto"}
        ])

for p in GOODMORNING:
    for _ in range(4):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"all_lights","mode":"on"},
            {"action":"set_thermostat","device_id":"thermostat_main","setpoint":71.0,"mode":"auto"}
        ])

for p in MOVIE:
    for _ in range(4):
        devs = make_home(extra=[light("living_room")])
        add(nl_prompt(devs,p),[
            {"action":"set_light_mode","device_id":"lights_living_room","mode":"solid","color":"#1A0F2E"}
        ])

for p in PARTY:
    for _ in range(3):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"all_lights","mode":"on"},
            {"action":"set_light_mode","device_id":f"lights_{random.choice(LOCATIONS)}","mode":"rainbow"}
        ])

for p in LOCKDOWN:
    for _ in range(4):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"all_lights","mode":"alert"},
            {"action":"notify","message":"Security lockdown activated","severity":"high"}
        ])

for p in AWAY:
    for _ in range(3):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"all_lights","mode":"off"},
            {"action":"set_thermostat","device_id":"thermostat_main","mode":"auto","setpoint":68.0}
        ])

for p in ARRIVE:
    for _ in range(3):
        devs = make_home()
        add(nl_prompt(devs,p),[
            {"action":"all_lights","mode":"on"},
            {"action":"set_thermostat","device_id":"thermostat_main","setpoint":71.0,"mode":"auto"}
        ])

# ─────────────────────────────────────────────────────────
# SECTION 16: NO-OP / RESTRAINT (50 examples)
# ─────────────────────────────────────────────────────────
NOOP_PHRASES = ["thanks","hello","hey","what's up","are you there","ok","cool",
                "nice","got it","understood","great","perfect","sounds good",
                "alright","roger that","noted","copy","awesome","sure","yep",
                "how are you","what time is it","what's the weather","who are you",
                "tell me a joke","sing me a song","what can you do","help"]
for p in NOOP_PHRASES:
    for _ in range(2):
        devs = make_home()
        add(nl_prompt(devs,p),[])

# ─────────────────────────────────────────────────────────
# SECTION 17: CONSTRAINT EXAMPLES — WHAT NOT TO DO (100 examples)
# Teaching the model to respect read-only fields
# ─────────────────────────────────────────────────────────

# "Close the door" → can only notify, not change sensor state
for _ in range(25):
    loc = random.choice(DOOR_LOCS)
    hour = random.randint(0,23)
    devs = make_home(extra=[door(loc,open_=True)])
    # This is a state change request — AI should NOT close the door via sensor
    # Correct: notify that door is open (it's read-only)
    add(nl_prompt(devs,f"the {loc.replace('_','  ')} is open can you close it"),[
        {"action":"notify","message":f"The {loc.replace('_',' ')} is open — please close it manually","severity":"low"}
    ])

# "Turn off the smoke alarm" → cannot clear it
for _ in range(15):
    loc = random.choice(SMOKE_LOCS)
    devs = make_home(extra=[smoke(loc,s=True,co=False)])
    add(nl_prompt(devs,"turn off the smoke alarm"),[
        {"action":"notify","message":"Smoke alarm cannot be cleared remotely — check for fire and ventilate","severity":"high"}
    ])

# "Set temp to 95" → out of range, clamp to 90
for _ in range(15):
    devs = make_home()
    add(nl_prompt(devs,"set temperature to 95 degrees"),[
        {"action":"set_thermostat","device_id":"thermostat_main","setpoint":90.0}
    ])

# "Set temp to 40" → out of range, clamp to 50
for _ in range(15):
    devs = make_home()
    add(nl_prompt(devs,"set temperature to 40 degrees"),[
        {"action":"set_thermostat","device_id":"thermostat_main","setpoint":50.0}
    ])

# "Close garage" with car present → warn, don't close
for _ in range(15):
    devs = make_home(extra=[garage(open_=True,car=True)])
    add(nl_prompt(devs,"close the garage"),[
        {"action":"notify","message":"Cannot auto-close garage — car detected inside","severity":"low"}
    ])

# "Unlock the front door" → rfid_lock is not controllable
for _ in range(15):
    devs = make_home(extra=[rfid("front_door",auth=False)])
    add(nl_prompt(devs,"unlock the front door"),[
        {"action":"notify","message":"Door lock cannot be controlled remotely — access requires RFID card","severity":"low"}
    ])

# ─────────────────────────────────────────────────────────
# SECTION 18: COMPOUND / MULTI-ACTION (80 examples)
# ─────────────────────────────────────────────────────────
for _ in range(30):
    loc1 = random.choice(LOCATIONS)
    loc2 = random.choice([l for l in LOCATIONS if l!=loc1])
    devs = make_home(extra=[light(loc1),light(loc2)])
    add(nl_prompt(devs,f"turn off the {loc1.replace('_',' ')} and {loc2.replace('_',' ')} lights"),[
        {"action":"control_device","device_id":f"lights_{loc1}","state":"off"},
        {"action":"control_device","device_id":f"lights_{loc2}","state":"off"},
    ])

for _ in range(25):
    temp = random.choice(TEMP_TARGETS)
    mode = random.choice(["heat","cool","auto"])
    devs = make_home()
    add(nl_prompt(devs,f"set thermostat to {mode} mode at {temp} degrees"),[
        {"action":"set_thermostat","device_id":"thermostat_main","setpoint":float(temp),"mode":mode}
    ])

for _ in range(25):
    loc  = random.choice(LOCATIONS)
    temp = random.choice(TEMP_TARGETS)
    devs = make_home(extra=[light(loc)])
    add(nl_prompt(devs,f"turn off the {loc.replace('_',' ')} lights and set temperature to {temp}"),[
        {"action":"control_device","device_id":f"lights_{loc}","state":"off"},
        {"action":"set_thermostat","device_id":"thermostat_main","setpoint":float(temp)}
    ])

# ─────────────────────────────────────────────────────────
# SHUFFLE + SPLIT
# ─────────────────────────────────────────────────────────
random.shuffle(examples)
split = int(len(examples)*0.9)
train, val = examples[:split], examples[split:]

os.makedirs("dataset",exist_ok=True)
with open("dataset/openhome_train.jsonl","w") as f:
    for ex in train: f.write(json.dumps(ex)+"\n")
with open("dataset/openhome_val.jsonl","w") as f:
    for ex in val:   f.write(json.dumps(ex)+"\n")

# Stats
event_n = sum(1 for e in examples if "TRIGGERING EVENT" in e["messages"][1]["content"])
nl_n    = sum(1 for e in examples if "USER COMMAND" in e["messages"][1]["content"])
print(f"Generated {len(examples)} total examples")
print(f"  Train: {len(train)} | Val: {len(val)}")
print(f"  Event reasoning: {event_n}")
print(f"  NL commands:     {nl_n}")
# Validate every output is parseable JSON
bad = 0
for ex in examples:
    try: json.loads(ex["messages"][2]["content"])
    except: bad+=1; print("BAD:", ex["messages"][2]["content"][:80])
print(f"  Validation errors: {bad}")
