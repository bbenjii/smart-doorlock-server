from datetime import datetime
from typing import Optional, List, Dict, Any

from db import db
from google.cloud.firestore_v1 import FieldFilter, Query
from google.api_core.exceptions import FailedPrecondition


def _serialize_value(val: Any) -> Any:
    """Convert Firestore-specific types (e.g. DatetimeWithNanoseconds) to JSON-safe values."""
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def _serialize_doc(data: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively convert all datetime values in a Firestore document dict to ISO strings."""
    result = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = _serialize_doc(v)
        elif isinstance(v, list):
            result[k] = [_serialize_value(item) for item in v]
        else:
            result[k] = _serialize_value(v)
    return result

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

    try:
        q = q.order_by("timestamp", direction=Query.DESCENDING)

        if cursor_ts:
            # start after the last timestamp you received previously
            q = q.start_after({"timestamp": cursor_ts})

        q = q.limit(limit)
        docs = list(q.stream())
    except FailedPrecondition:
        # Missing composite index fallback: fetch unsorted and sort in memory.
        fallback_q = db.collection("events")
        if device_id:
            fallback_q = fallback_q.where(filter=FieldFilter("deviceId", "==", device_id))
        if user_id:
            fallback_q = fallback_q.where(filter=FieldFilter("userId", "==", user_id))
        if event_type:
            fallback_q = fallback_q.where(filter=FieldFilter("eventType", "==", event_type))
        if start:
            fallback_q = fallback_q.where(filter=FieldFilter("timestamp", ">=", start))
        if end:
            fallback_q = fallback_q.where(filter=FieldFilter("timestamp", "<=", end))
        docs = list(fallback_q.limit(limit).stream())
    items: List[Dict[str, Any]] = []
    next_cursor_ts = None

    for d in docs:
        data = d.to_dict()
        data["eventId"] = d.id
        data = _serialize_doc(data)
        items.append(data)

    items.sort(key=lambda item: item.get("timestamp") or "", reverse=True)

    if items:
        next_cursor_ts = items[-1].get("timestamp")

    return {
        "items": items,
        "nextCursorTs": next_cursor_ts,
    }
