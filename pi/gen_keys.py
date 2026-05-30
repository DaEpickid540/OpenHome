"""
openHome Key Generator
───────────────────────
Generates strong random keys and writes them to secrets_config.py
(which is gitignored). Run this ONCE during setup.

  python3 gen_keys.py

Then copy the printed keys into:
  - each ESP32's config.h  (API_KEY and HMAC_KEY)
  - the dashboard          (API_KEY only)
"""

import secrets
import os

API_KEY  = secrets.token_hex(24)   # 48 hex chars
HMAC_KEY = secrets.token_hex(32)   # 64 hex chars

OUT = "secrets_config.py"

if os.path.exists(OUT):
    resp = input(f"{OUT} already exists. Overwrite and invalidate old keys? [y/N] ")
    if resp.lower() != "y":
        print("Aborted. Existing keys kept.")
        raise SystemExit(0)

with open(OUT, "w") as f:
    f.write('"""\n')
    f.write("openHome secrets — DO NOT COMMIT. This file is gitignored.\n")
    f.write("Regenerate with: python3 gen_keys.py\n")
    f.write('"""\n\n')
    f.write(f'API_KEY  = "{API_KEY}"\n')
    f.write(f'HMAC_KEY = "{HMAC_KEY}"\n')

print("=" * 60)
print("Keys generated and written to secrets_config.py")
print("=" * 60)
print(f"\nAPI_KEY  = {API_KEY}")
print(f"HMAC_KEY = {HMAC_KEY}")
print("\nNext steps:")
print("  1. Put BOTH keys in every ESP32's config.h")
print("  2. Put API_KEY in the dashboard (it will prompt you, or edit index.html)")
print("  3. Keep secrets_config.py safe — never commit it")
print("  4. If a key leaks, re-run this and re-flash everything")
