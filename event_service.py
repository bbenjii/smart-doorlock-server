from datetime import datetime
from typing import Optional
from users_controller import db

ALLOWED_EVENTS = {
    "LOCKED",
    "UNLOCKED",
    "FAILED_AUTH",
    "MOTION_DETECTED",
    "FORCED_ENTRY",
    "BATTERY_LOW",
}


def ingest_event(
    device_id: str,
    event_type: str,
    user_id: Optional[str] = None,
    auth_method: Optional[str] = None,
):
    if event_type not in ALLOWED_EVENTS:
        return False

    db.collection("events").add({
        "deviceId": device_id,
        "userId": user_id,
        "eventType": event_type,
        "authMethod": auth_method,
        "timestamp": datetime.utcnow(),
    })

    return True
