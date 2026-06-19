"""
Overnight orchestrator: train → verify → retry up to 3 times.
Results are written to overnight_results.txt.

Usage:  python run_overnight.py
"""

import subprocess, sys, json, os
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent
LOG_FILE  = REPO_ROOT / "overnight_results.txt"
HF_TOKEN  = os.getenv("HF_TOKEN", "")

# Hyperparams for each attempt — escalating epochs, lower lr if previous attempt failed
ATTEMPTS = [
    {"OHM_EPOCHS": "2", "OHM_LR": "2e-4", "OHM_MAX_LEN": "1024"},  # fast baseline
    {"OHM_EPOCHS": "3", "OHM_LR": "1e-4", "OHM_MAX_LEN": "1024"},  # more epochs, cautious lr
    {"OHM_EPOCHS": "4", "OHM_LR": "5e-5", "OHM_MAX_LEN": "1024"},  # conservative, many epochs
]

FAILURE_ADVICE = """
POSSIBLE CAUSES AND FIXES
──────────────────────────
1. Sequence truncation cutting training signal
   Fix: The system prompt is ~600 tokens. Try max_len=2048 if you have time,
        or shorten SYSTEM in generate_training_data.py and regenerate.

2. Not enough epochs for CPU training
   Fix: Increase OHM_EPOCHS to 5-10 in ATTEMPTS and re-run run_overnight.py.

3. Learning rate too high — loss spikes or diverges
   Fix: Try lr=1e-5 and check training_attempt_*.log for loss values.

4. Base model mismatch / tokenizer issues
   Fix: Delete ~/.cache/huggingface/hub/models--Qwen* and re-download.

5. Dataset quality issue
   Fix: python dataset/generate_training_data.py  (regenerate data)
        Then re-run this script.

6. Hardware is too slow for meaningful overnight training
   Fix: Use Google Colab (free T4 GPU, ~15 min):
        - Upload dataset/openhome_train.jsonl + openhome_val.jsonl
        - Run dataset/train_unsloth.py (change device_map to auto)
        - Download openhome-model/lora/ back to this machine.
"""


def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str):
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def make_env(overrides: dict) -> dict:
    env = os.environ.copy()
    env["HF_TOKEN"]         = HF_TOKEN
    env["TOKENIZERS_PARALLELISM"] = "false"  # suppress HF warning
    env["OMP_NUM_THREADS"]  = "8"
    env["MKL_NUM_THREADS"]  = "8"
    env.update(overrides)
    return env


def run_training(attempt_num: int, params: dict) -> bool:
    log(f"--- Training attempt {attempt_num}/3 | {params} ---")
    env = make_env({**params, "OHM_ATTEMPT": str(attempt_num)})
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "train_overnight.py")],
        cwd=str(REPO_ROOT),
        env=env,
    )
    if result.returncode != 0:
        log(f"Training exited with code {result.returncode}")
        return False
    return True


def run_verification() -> dict | None:
    log("Running verification...")
    env = make_env({})
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "verify_overnight.py")],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    # Stream output to console + log
    for line in result.stdout.splitlines():
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if result.stderr:
        for line in result.stderr.splitlines()[:20]:  # cap error output
            print("  STDERR:", line, flush=True)

    # The last JSON line is the machine-readable verdict
    for line in reversed(result.stdout.strip().splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass
    return None


if __name__ == "__main__":
    log("=" * 60)
    log("OpenHome Overnight Training Run Started")
    log("=" * 60)

    success = False

    for i, params in enumerate(ATTEMPTS, start=1):
        trained = run_training(i, params)
        if not trained:
            log(f"Attempt {i} training failed. Moving to next attempt.")
            continue

        verdict = run_verification()
        if verdict is None:
            log(f"Attempt {i}: verification produced no parseable output.")
            continue

        log(
            f"Attempt {i} result: usable={verdict.get('usable')} "
            f"json={verdict.get('json_rate', 0):.0%} "
            f"accuracy={verdict.get('action_rate', 0):.0%} "
            f"({verdict.get('passed',0)}/{verdict.get('total',0)} tests)"
        )

        if verdict.get("usable"):
            log("=" * 60)
            log(f"SUCCESS on attempt {i}!")
            log(f"  JSON valid:   {verdict.get('json_rate',0):.0%}")
            log(f"  Test accuracy:{verdict.get('action_rate',0):.0%}")
            log(f"  Model saved:  openhome-model/lora/")
            log("  Next step: test with Ollama or load via ai_brain.py")
            log("=" * 60)
            success = True
            break
        else:
            if i < len(ATTEMPTS):
                log(f"Model not usable. Adjusting hyperparams and retrying...")

    if not success:
        log("=" * 60)
        log("FAILED after 3 attempts — model did not meet quality threshold.")
        log("Check overnight_results.txt and training_attempt_*.log for details.")
        for line in FAILURE_ADVICE.strip().splitlines():
            log(line)
        log("=" * 60)

    log("Overnight run complete.")
