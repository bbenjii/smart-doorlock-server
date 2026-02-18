from datetime import datetime
from typing import Optional, Dict, Any, List
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


def write_audit(
    action: str,
    actor_user_id: Optional[str],
    device_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    status: str = "SUCCESS",
    details: Optional[Dict[str, Any]] = None,
):
    #Records backend actions for traceability (who did what, when, result)
    db.collection("logs").add({
        "logType": "AUDIT",
        "action": action,
        "actorUserId": actor_user_id,
        "targetUserId": target_user_id,
        "deviceId": device_id,
        "status": status,
        "details": details or {},
        "timestamp": datetime.utcnow(),
    })


def query_audit_logs(
    device_id: str,
    limit: int = 50,
    cursor_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    bounded_limit = max(1, min(limit, 200))

    try:
        q = (
            db.collection("logs")
            .where(filter=FieldFilter("deviceId", "==", device_id))
            .order_by("timestamp", direction=Query.DESCENDING)
        )

        if cursor_ts:
            q = q.start_after({"timestamp": cursor_ts})

        docs = list(q.limit(bounded_limit).stream())
    except FailedPrecondition:
        # Missing composite index fallback: fetch unsorted and sort in memory.
        fallback_q = db.collection("logs").where(filter=FieldFilter("deviceId", "==", device_id))
        docs = list(fallback_q.limit(bounded_limit).stream())

    items: List[Dict[str, Any]] = []
    next_cursor_ts = None

    for d in docs:
        data = d.to_dict() or {}
        data["logId"] = d.id
        data = _serialize_doc(data)
        items.append(data)

    items.sort(key=lambda item: item.get("timestamp") or "", reverse=True)

    if items:
        next_cursor_ts = items[-1].get("timestamp")

    return {
        "items": items,
        "nextCursorTs": next_cursor_ts,
    }
