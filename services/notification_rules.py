from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

from services.dev_state_service import get_last_seen
from services.notification_service import recent_notification_exists

RULE_COOLDOWNS = {
    "BATTERY_LOW": timedelta(hours=6),
    "DEVICE_OFFLINE": timedelta(minutes=10),
}

CRITICAL_EVENTS = {
    "FORCED_ENTRY",
    "FAILED_AUTH",
}

def should_notify(
    device_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[str]]:
    """Returns (should_notify, required_access_level)"""

    if event_type in CRITICAL_EVENTS:
        return True, "owner"

    if event_type == "BATTERY_LOW":
        level = payload.get("battery") if payload else None
        if level is not None and level > 20:
            return False, None
        cooldown = RULE_COOLDOWNS.get(event_type)
        since = datetime.utcnow() - cooldown if cooldown else None
        should_send = not recent_notification_exists(device_id, event_type, since)
        return should_send, "owner"

    if event_type == "DEVICE_OFFLINE":
        last_seen = get_last_seen(device_id)
        if not last_seen:
            return False, None
        if datetime.utcnow() - last_seen < timedelta(seconds=10):
            return False, None
        cooldown = RULE_COOLDOWNS.get(event_type)
        since = datetime.utcnow() - cooldown if cooldown else None
        should_send = not recent_notification_exists(device_id, event_type, since)
        return should_send, "owner"

    return False, None