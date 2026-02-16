from datetime import datetime
from typing import Optional, List, Dict, Any

from db import db
from google.cloud.firestore_v1 import FieldFilter

ALLOWED_EVENTS = {
    "LOCKED",
    "UNLOCKED",
    "FAILED_AUTH",
    "MOTION_DETECTED",
    "FORCED_ENTRY",
    "BATTERY_LOW",
    "DEVICE_OFFLINE",
    "DEVICE_ONLINE",
    "DOORBELL_PRESSED",
    "WINDOW_SENSOR_TRIGGERED",
}

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def ingest_event(
    device_id: str,
    event_type: str,
    user_id: Optional[str] = None,
    auth_method: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    #Stores validated structured device events in Firestore.

    if not device_id or not isinstance(device_id, str):
        return False
    if event_type not in ALLOWED_EVENTS:
        return False

    doc = {
        "deviceId": device_id,
        "eventType": event_type,
        "userId": user_id,
        "authMethod": auth_method,
        "metadata": metadata or {},
        "timestamp": datetime.utcnow(),
    }

    # Persist event and return its id so callers can link media
    doc_ref = db.collection("events").document()
    doc_ref.set(doc)
    return doc_ref.id


def query_events(
    device_id: Optional[str] = None,
    user_id: Optional[str] = None,
    event_type: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = DEFAULT_LIMIT,
    cursor_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    BE-015: Event querying and filtering
    Firestore-friendly filters + pagination by timestamp.

    Pagination strategy:
      - order_by(timestamp desc)
      - optionally start_after(cursor_ts)
    """
    limit = max(1, min(limit, MAX_LIMIT))

    q = db.collection("events")

    if device_id:
        q = q.where(filter=FieldFilter("deviceId", "==", device_id))
    if user_id:
        q = q.where(filter=FieldFilter("userId", "==", user_id))
    if event_type:
        q = q.where(filter=FieldFilter("eventType", "==", event_type))
    if start:
        q = q.where(filter=FieldFilter("timestamp", ">=", start))
    if end:
        q = q.where(filter=FieldFilter("timestamp", "<=", end))

    q = q.order_by("timestamp", direction="DESCENDING")

    if cursor_ts:
        # start after the last timestamp you received previously
        q = q.start_after({"timestamp": cursor_ts})

    q = q.limit(limit)

    docs = list(q.stream())
    items: List[Dict[str, Any]] = []
    next_cursor_ts = None

    for d in docs:
        data = d.to_dict()
        data["eventId"] = d.id
        items.append(data)

    if items:
        next_cursor_ts = items[-1].get("timestamp")

    return {
        "items": items,
        "nextCursorTs": next_cursor_ts,
    }
