---
language:
  - en
license: mit
base_model: Qwen/Qwen2.5-0.5B-Instruct
tags:
  - peft
  - lora
  - smart-home
  - iot
  - action-dispatch
  - qwen2
  - text-generation
  - openhome
pipeline_tag: text-generation
---

# openhome-nova

LoRA fine-tune of [Qwen/Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) for smart home event reasoning and action dispatch. Built for **[OpenHome](https://github.com/DaEpickid540/OpenHome)** — a fully local, open-source smart home security system running on a Raspberry Pi with no cloud dependency.

---

## What it does

Given a sensor event (door opened, motion detected, smoke alarm, etc.) and the current state of all smart home devices, NOVA returns a structured JSON action list:

```json
[
  {"action": "notify", "message": "Smoke detected in kitchen — evacuate!", "severity": "critical"},
  {"action": "all_lights", "mode": "alert"}
]
```

It handles:
- **Sensor events** — panic, smoke/CO, flood, motion, door, doorbell, RFID, climate, air quality
- **Natural language commands** — "goodnight", "movie mode", "I'm cold", "lock everything down"
- **Routines** — away mode, lockdown, morning, party, arrive-home sequences
- **Constraints** — refuses to write read-only sensors, won't invent device IDs

---

## Intended use

Deployed in [OpenHome](https://github.com/DaEpickid540/OpenHome) via [Ollama](https://ollama.com) on a Raspberry Pi. The hub loads NOVA as the reasoning layer between raw sensor events and actuator commands.

Load it:
```bash
# After cloning the OpenHome repo and downloading this adapter to openhome-model/lora/
ollama create openhome-nova -f pi/Modelfile
```

Or load directly with PEFT:
```python
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base = AutoModelForCausalLM.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
model = PeftModel.from_pretrained(base, "Xx-Vexento-xX/openhome-nova")
tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct")
```

---

## Training details

| | |
|---|---|
| **Base model** | Qwen/Qwen2.5-0.5B-Instruct |
| **Method** | LoRA (PEFT) |
| **LoRA rank** | 8 |
| **LoRA alpha** | 16 |
| **Target modules** | q_proj, v_proj |
| **Training examples** | 1718 |
| **Validation examples** | 191 |
| **Epochs** | 2 |
| **Learning rate** | 2e-4 |
| **Batch size** | 1 (gradient accumulation 4) |
| **Max sequence length** | 1024 (truncation_side=left) |
| **Hardware** | CPU only — AMD Ryzen 7 5800X3D, 34 GB RAM |
| **Training time** | ~9 hours |
| **Final eval loss** | 0.044 |

Training was done entirely on CPU using `transformers` + `peft` + `trl` (no CUDA, no Unsloth). The `truncation_side="left"` setting was critical to preserve the assistant's response tokens when sequences exceeded max length.

---

## Evaluation

Tested against 10 OpenHome scenarios immediately after training:

| Scenario | Result |
|---|---|
| Panic button pressed | PASS |
| Smoke alarm triggered | PASS |
| Lights off command | PASS |
| Goodnight routine | PASS |
| Thermostat adjustment | PASS |
| Flood sensor wet | PASS |
| No-op (no action needed) | PASS |
| Door open at night | PASS |
| Movie mode | PASS |
| I'm home routine | PASS |

**10/10 valid JSON, 9/10 action-correct** (90%). The one partial-pass was smoke/CO outputting `"alert"` instead of `"notify"` as the action type — handled in the OpenHome receiver with an alias rather than requiring retraining.

---

## Training data

The dataset covers 18 categories. Key examples:

- All 13 device types with correct sensor → action mappings
- Time-of-day logic (door open at night = alert, daytime = log)
- Light commands: 200+ variations (color, brightness, mode, all-lights)
- Thermostat commands: 200+ (setpoint, mode, hold, schedule)
- Household routines: goodnight, morning, movie, party, lockdown, away, arrive
- **100 constraint examples** — teaching what the model must NOT do (can't unlock RFID, can't clear smoke alarm, can't write read-only sensors)
- **50 no-op examples** — so it returns `[]` instead of inventing actions for benign events

Regenerate or expand: `python3 dataset/generate_training_data.py` in the [OpenHome repo](https://github.com/DaEpickid540/OpenHome).

---

## Action schema

NOVA outputs arrays of these action types:

```json
{"action": "control_device", "device_id": "plug_kitchen", "state": "on|off|toggle"}
{"action": "set_thermostat", "device_id": "thermostat_main", "setpoint": 72, "mode": "heat|cool|auto|off"}
{"action": "set_light_mode", "device_id": "rgb_living", "mode": "alert|rainbow|pulse|fire|solid", "color": "#FF0000"}
{"action": "notify", "message": "...", "severity": "low|medium|high|critical"}
{"action": "all_lights", "mode": "alert|off|on"}
{"action": "log", "message": "..."}
```

---

## Limitations

- Trained for the OpenHome device schema. Device type names and field names must match the OpenHome standard for best results.
- At 0.5B parameters, complex multi-step reasoning can occasionally miss edge cases — the RAG memory layer in OpenHome compensates with household context.
- Does not handle arbitrary home automation platforms (Home Assistant, SmartThings, etc.) out of the box.

---

## Repository

Full system source, ESP32 firmware, Pi hub, dashboard, and training pipeline:
**[github.com/DaEpickid540/OpenHome](https://github.com/DaEpickid540/OpenHome)**

---

## License

MIT
