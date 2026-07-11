"""
openHome Security Layer
────────────────────────
Two layers of protection:

1. API KEY (shared secret)
   Every request must carry header  X-OpenHome-Key: <key>
   Blocks anyone on your WiFi who doesn't have the key.
   Used by: dashboard, manual curl, always-on actuators.

2. HMAC SIGNATURE (anti-spoofing for sensors)
   Battery sensors sign their JSON payload with a shared HMAC key.
   Header:  X-OpenHome-Sig: <hex hmac-sha256 of raw body>
   Prevents an attacker from forging fake sensor events
   ("smoke clear", "all doors closed", fake panic) even if they
   somehow learned the API key, because the signature is over the
   exact body and keyed with a separate secret.

Keys live in secrets.py (gitignored). Generate with:  python3 gen_keys.py

Both checks can be toggled in settings for debugging, but default ON.
"""

import hmac
import hashlib
from fastapi import Request
from fastapi.responses import JSONResponse

try:
    from secrets_config import API_KEY, HMAC_KEY
except ImportError:
    # Fail closed: a hardcoded fallback key is public (it's in this file) and
    # would let anyone who read the repo control the hub. Refuse to boot instead.
    print("[SECURITY] ⚠ secrets_config.py not found — run 'python3 gen_keys.py'")
    raise SystemExit(1)

# Endpoints that don't require auth (none, really — even root is protected)
# Kept minimal. The dashboard sends the key on every call.
PUBLIC_PATHS = set()

# Endpoints that additionally require a valid HMAC signature (sensor data).
# These are the spoofable ones — fake events could trigger/suppress alarms.
HMAC_REQUIRED_PATHS = {"/sensor", "/register", "/snapshot"}


def constant_time_eq(a: str, b: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(a.encode(), b.encode())


def check_api_key(request: Request) -> bool:
    """True if the request carries the correct API key."""
    provided = request.headers.get("X-OpenHome-Key", "")
    return constant_time_eq(provided, API_KEY)


def compute_hmac(raw_body: bytes) -> str:
    """HMAC-SHA256 of the raw request body, hex-encoded."""
    return hmac.new(HMAC_KEY.encode(), raw_body, hashlib.sha256).hexdigest()


def check_hmac(request: Request, raw_body: bytes) -> bool:
    """True if the X-OpenHome-Sig header matches the body signature."""
    provided = request.headers.get("X-OpenHome-Sig", "")
    expected = compute_hmac(raw_body)
    return constant_time_eq(provided, expected)


def needs_hmac(path: str) -> bool:
    return any(path.startswith(p) for p in HMAC_REQUIRED_PATHS)


async def security_middleware(request: Request, call_next):
    """
    FastAPI middleware. Runs on every request.
    Order: API key first (cheap), then HMAC for sensor paths.
    """
    import storage
    path = request.url.path

    # Allow static snapshot files served by StaticFiles mount to pass
    # (they're images, already gated by the key on the API that creates them)
    if path.startswith("/snapshots/") and request.method == "GET":
        # still require the key for viewing snapshots
        if not check_api_key(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    # Master toggle (default on). Lets you disable during local debugging.
    auth_enabled = storage.get_setting("auth_enabled", True)
    if not auth_enabled:
        return await call_next(request)

    if path in PUBLIC_PATHS:
        return await call_next(request)

    # 1. API key on everything
    if not check_api_key(request):
        return JSONResponse({"error": "unauthorized: missing or bad API key"},
                            status_code=401)

    # 2. HMAC on sensor/register/snapshot (the spoofable, safety-critical paths)
    if needs_hmac(path) and request.method == "POST":
        body = await request.body()
        if not check_hmac(request, body):
            return JSONResponse({"error": "unauthorized: bad signature"},
                                status_code=401)
        # Re-inject body so downstream handlers can read it again
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        request._receive = receive

    return await call_next(request)
