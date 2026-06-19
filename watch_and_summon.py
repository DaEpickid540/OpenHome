"""
Watches overnight_results.txt for training completion.
When done: shows a Windows alert popup, then opens a new terminal
window running Claude Code with the results pre-loaded as context.

Launch once and leave it running — it sleeps between checks.
"""

import time, ctypes, subprocess, sys
from pathlib import Path
from datetime import datetime

REPO_ROOT      = Path(__file__).parent
RESULTS_FILE   = REPO_ROOT / "overnight_results.txt"
DONE_MARKER    = "Overnight run complete."
CHECK_EVERY    = 90   # seconds between polls

def ts():
    return datetime.now().strftime("%H:%M:%S")

def alert_popup(msg: str):
    """Blocking Windows message box — wakes a sleeping monitor."""
    ctypes.windll.user32.MessageBoxW(
        0, msg, "OpenHome Training Complete", 0x40 | 0x1000
        # 0x40 = MB_ICONINFORMATION, 0x1000 = MB_SYSTEMMODAL (stays on top)
    )

def build_context_prompt() -> str:
    """Reads overnight_results.txt and the latest attempt log to give Claude full context."""
    summary = ""
    if RESULTS_FILE.exists():
        summary = RESULTS_FILE.read_text(encoding="utf-8")[-3000:]  # last 3k chars

    # Find the highest-numbered attempt log
    logs = sorted(REPO_ROOT.glob("training_attempt_*.log"))
    log_tail = ""
    if logs:
        latest = logs[-1].read_text(encoding="utf-8")
        log_tail = latest[-2000:]  # last 2k chars of training log

    prompt = (
        "OpenHome overnight training just finished. Here are the results:\n\n"
        f"=== overnight_results.txt (tail) ===\n{summary}\n\n"
        f"=== {logs[-1].name if logs else 'no log found'} (tail) ===\n{log_tail}\n\n"
        "Please:\n"
        "1. Tell me if the model passed or failed the quality checks.\n"
        "2. If it passed — confirm openhome-model/lora/ exists and is ready.\n"
        "3. If it failed — run `python verify_overnight.py` to get fresh test results "
        "and tell me specifically which test cases are failing and why.\n"
        "4. Recommend next steps (deploy to Pi, retrain, or use Colab)."
    )
    return prompt

def summon_claude():
    prompt = build_context_prompt()
    prompt_escaped = prompt.replace('"', '\\"').replace("\n", "\\n")

    # Open a NEW console window: claude -p "context" (non-interactive analysis)
    # The /k keeps the window open after claude exits so the user can keep chatting
    cmd = f'claude -p "{prompt_escaped}"'
    subprocess.Popen(
        ["cmd", "/k", cmd],
        cwd=str(REPO_ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )
    print(f"[{ts()}] Claude Code summoned in new window.", flush=True)

if __name__ == "__main__":
    print(f"[{ts()}] Watcher started. Checking every {CHECK_EVERY}s for: '{DONE_MARKER}'", flush=True)
    print(f"[{ts()}] Watching: {RESULTS_FILE}", flush=True)

    already_done = (
        RESULTS_FILE.exists()
        and DONE_MARKER in RESULTS_FILE.read_text(encoding="utf-8")
    )
    if already_done:
        print(f"[{ts()}] Training already finished — summoning Claude immediately.", flush=True)
        alert_popup("OpenHome training already complete!\nSummoning Claude Code to review results.")
        summon_claude()
        sys.exit(0)

    while True:
        try:
            if RESULTS_FILE.exists():
                content = RESULTS_FILE.read_text(encoding="utf-8")
                if DONE_MARKER in content:
                    print(f"[{ts()}] Detected completion!", flush=True)
                    alert_popup(
                        "OpenHome training is done!\n\n"
                        "Claude Code is opening to review results.\n"
                        "(Check the new console window.)"
                    )
                    summon_claude()
                    break
                else:
                    # Print last status line so the watcher window shows progress
                    lines = [l for l in content.splitlines() if l.strip()]
                    if lines:
                        print(f"[{ts()}] Still running... last: {lines[-1][-80:]}", flush=True)
            else:
                print(f"[{ts()}] Waiting for results file...", flush=True)
        except Exception as e:
            print(f"[{ts()}] Watcher error (will retry): {e}", flush=True)

        time.sleep(CHECK_EVERY)

    print(f"[{ts()}] Watcher done.", flush=True)
