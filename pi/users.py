"""
openHome Users & Preferences
────────────────────────────
Manages household members and their preferences.
Preferences feed into the AI's reasoning (via RAG) so it personalizes behavior.

A user:
  {
    "user_id", "name", "role",        # role: admin | member | guest
    "rfid_uid",                        # link to their RFID card
    "preferences": {
        "preferred_temp_f", "wake_time", "sleep_time",
        "notify_severity_min",         # only notify me about >= this severity
        "favorite_light_color",
    },
    "created"
  }
"""

from datetime import datetime
import storage


def create_user(name: str, role: str = "member", rfid_uid: str = None,
                preferences: dict = None) -> dict:
    user_id = f"user_{name.lower().replace(' ', '_')}_{int(datetime.now().timestamp())}"
    user = {
        "user_id":     user_id,
        "name":        name,
        "role":        role,
        "rfid_uid":    rfid_uid,
        "preferences": preferences or _default_prefs(),
        "created":     datetime.now().isoformat()
    }
    storage.set_item("users", user_id, user)
    print(f"[USERS] Created {role}: {name} ({user_id})")
    return user


def _default_prefs() -> dict:
    return {
        "preferred_temp_f":     72,
        "wake_time":            "07:00",
        "sleep_time":           "23:00",
        "notify_severity_min":  "medium",
        "favorite_light_color": "#FFD740",
    }


def get_user(user_id: str) -> dict:
    return storage.get_item("users", user_id)


def list_users() -> list[dict]:
    return list(storage.get("users").values())


def update_user(user_id: str, patch: dict) -> dict:
    return storage.update_item("users", user_id, patch)


def update_preferences(user_id: str, prefs: dict) -> dict:
    user = storage.get_item("users", user_id)
    if not user:
        return None
    user["preferences"] = {**user.get("preferences", {}), **prefs}
    storage.set_item("users", user_id, user)
    return user


def delete_user(user_id: str) -> bool:
    return storage.delete_item("users", user_id)


def find_by_rfid(uid: str) -> dict:
    """Look up which user owns an RFID card."""
    for user in storage.get("users").values():
        if user.get("rfid_uid") == uid:
            return user
    return None
