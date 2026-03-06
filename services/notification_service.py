from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from uuid import uuid4

from firebase_admin import messaging
import requests
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1 import FieldFilter, Query
from db import db
from services.settings_service import get_settings

DEFAULT_ENABLED_NOTIFICATIONS = {
    "FORCED_ENTRY",
    "FAILED_AUTH",
    "BATTERY_LOW",
    "DEVICE_OFFLINE",
    "DOORBELL_PRESSED",
    "WINDOW_SENSOR_TRIGGERED",
}

def create_notification(
    device_id: str,
    user_ids: List[str],
    notif_type: str,
    message: str,
    data: Optional[Dict[str, Any]] = None,
    *,
    send_push: bool = True,
) -> Dict[str, Any]:

    if not device_id:
        raise ValueError("device_id is required")
    if not user_ids:
        raise ValueError("user_ids must not be empty")
    if not notif_type:
        raise ValueError("notif_type is required")
    if not message:
        raise ValueError("message is required")

    clean_user_ids = _dedupe_preserve_order([u for u in user_ids if u])
    payload_data = data or {}
    now = datetime.utcnow()

    # Write notifications (one per user)
    created_ids: List[str] = []
    for user_id in clean_user_ids:
        nid = str(uuid4())
        doc = {
            "notificationId": nid,
            "deviceId": device_id,
            "userId": user_id,
            "type": notif_type,
            "message": message,
            "data": payload_data,
            "read": False,
            "timestamp": now,
        }
        db.collection("notifications").add(doc)
        created_ids.append(nid)

    # Push delivery
    push_result = {"sent": 0, "failed": 0}
    if send_push:
        push_result = send_push_notifications(
            user_ids=clean_user_ids,
            title="Smart Lock Alert",
            body=message,
            data={
                "deviceId": device_id,
                "type": notif_type,
                **payload_data,
            },
        )

    return {
        "ok": True,
        "deviceId": device_id,
        "type": notif_type,
        "message": message,
        "userCount": len(clean_user_ids),
        "notificationIds": created_ids,
        "push": push_result,
    }


def send_push_notifications(
    user_ids: List[str],
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, int]:
    if not user_ids:
        return {"sent": 0, "failed": 0}

    tokens = _get_fcm_tokens(user_ids)
    if not tokens:
        return {"sent": 0, "failed": 0}

    expo_tokens = [t for t in tokens if _is_expo_push_token(t)]
    fcm_tokens = [t for t in tokens if not _is_expo_push_token(t)]

    sent = 0
    failed = 0

    if fcm_tokens:
        fcm_result = _send_fcm_multicast(
            tokens=fcm_tokens,
            title=title,
            body=body,
            data=data or {},
        )
        sent += fcm_result["sent"]
        failed += fcm_result["failed"]

    if expo_tokens:
        expo_result = _send_expo_push_batch(
            tokens=expo_tokens,
            title=title,
            body=body,
            data=data or {},
        )
        sent += expo_result["sent"]
        failed += expo_result["failed"]

    return {"sent": sent, "failed": failed}


def _is_expo_push_token(token: str) -> bool:
    return token.startswith("ExponentPushToken[") or token.startswith("ExpoPushToken[")


def _send_fcm_multicast(tokens: List[str], title: str, body: str, data: Dict[str, Any]) -> Dict[str, int]:
    msg = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=_stringify_data(data),
        tokens=tokens,
    )
    resp = messaging.send_multicast(msg)
    return {"sent": resp.success_count, "failed": resp.failure_count}


def _send_expo_push_batch(tokens: List[str], title: str, body: str, data: Dict[str, Any]) -> Dict[str, int]:
    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "data": data,
        }
        for token in tokens
    ]

    try:
        res = requests.post(
            "https://exp.host/--/api/v2/push/send",
            json=messages,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        if not res.ok:
            return {"sent": 0, "failed": len(tokens)}

        payload = res.json() if res.content else {}
        results = payload.get("data", [])
        if isinstance(results, dict):
            results = [results]

        sent = 0
        failed = 0
        for item in results:
            if (item or {}).get("status") == "ok":
                sent += 1
            else:
                failed += 1

        # Fallback if response does not contain per-message statuses
        if sent + failed == 0:
            return {"sent": len(tokens), "failed": 0}

        # If Expo returned fewer results than tokens, count the missing as failed
        if sent + failed < len(tokens):
            failed += len(tokens) - (sent + failed)

        return {"sent": sent, "failed": failed}
    except Exception:
        return {"sent": 0, "failed": len(tokens)}

def _get_fcm_tokens(user_ids: List[str]) -> List[str]:
    tokens: List[str] = []
    seen: set[str] = set()

    for uid in user_ids:
        if not uid:
            continue

        docs = db.collection("userDevices").where("userId", "==", uid).stream()
        for d in docs:
            token = (d.to_dict() or {}).get("fcmToken")
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)

    return tokens

def _stringify_data(data: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        out[str(k)] = str(v)
    return out


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _serialize_value(val: Any) -> Any:
    if isinstance(val, datetime):
        return val.isoformat()
    return val


def _serialize_doc(data: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for k, v in (data or {}).items():
        if isinstance(v, dict):
            result[k] = _serialize_doc(v)
        elif isinstance(v, list):
            result[k] = [_serialize_value(item) for item in v]
        else:
            result[k] = _serialize_value(v)
    return result


def query_notifications(device_id: str, user_id: str, limit: int = 50) -> Dict[str, Any]:
    bounded_limit = max(1, min(limit, 200))
    try:
        q = (
            db.collection("notifications")
            .where(filter=FieldFilter("deviceId", "==", device_id))
            .where(filter=FieldFilter("userId", "==", user_id))
            .order_by("timestamp", direction=Query.DESCENDING)
            .limit(bounded_limit)
        )
        docs = list(q.stream())
    except FailedPrecondition:
        # Missing composite index fallback: fetch smaller unsorted set and sort in memory.
        docs = list(
            db.collection("notifications")
            .where(filter=FieldFilter("deviceId", "==", device_id))
            .where(filter=FieldFilter("userId", "==", user_id))
            .limit(bounded_limit)
            .stream()
        )

    items: List[Dict[str, Any]] = []
    for d in docs:
        row = _serialize_doc(d.to_dict() or {})
        row["id"] = d.id
        items.append(row)

    items.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return {"items": items}

def get_notification_recipients(device_id: str) -> List[str]:
    docs = (
        db.collection("accessControl")
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .stream()
    )

    user_ids = []
    for d in docs:
        uid = d.to_dict().get("userId")
        if uid:
            user_ids.append(uid)

    return list(set(user_ids))  

def build_notification_message(event_type: str, payload: dict) -> str:
    if event_type == "FORCED_ENTRY":
        return "Forced entry detected on your device"

    if event_type == "FAILED_AUTH":
        return "Multiple failed authentication attempts detected"

    if event_type == "BATTERY_LOW":
        level = payload.get("battery")
        return f"Battery low ({level}%)" if level is not None else "Battery low"

    if event_type == "DEVICE_OFFLINE":
        return "Device is offline"

    if event_type == "DOORBELL_PRESSED":
        return "Someone is at your door"

    if event_type == "WINDOW_SENSOR_TRIGGERED":
        location = payload.get("location", "Unknown")
        return f"Window sensor triggered – {location}"

    return f"Event detected: {event_type}"

def recent_notification_exists(device_id: str, event_type: str, since: datetime) -> bool:
    docs = (
        db.collection("notifications")
        .where("deviceId", "==", device_id)
        .where("type", "==", event_type)
        .where("timestamp", ">=", since)
        .limit(1)
        .stream()
    )
    return next(docs, None) is not None

def get_notification_recipients_by_access(device_id: str, access_level: Optional[str] = None) -> List[str]:
    query = (
        db.collection("accessControl")
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
    )
    
    if access_level:
        query = query.where("accessLevel", "in", [access_level])
    
    docs = query.stream()
    user_ids = []
    for d in docs:
        uid = d.to_dict().get("userId")
        if uid:
            user_ids.append(uid)
    
    return list(set(user_ids))

def should_user_receive_notification(user_id: str, device_id: str, event_type: str) -> bool:
    # Master user/device toggle from settings (merged defaults + device + user override)
    # If this is disabled, no notification should pass regardless of event preferences.
    try:
        settings = get_settings(device_id, user_id=user_id)
    except Exception:
        settings = {"notisEnabled": True}

    if not bool(settings.get("notisEnabled", True)):
        return False

    doc = db.collection("userPreferences").document(f"{user_id}_{device_id}").get()
    if not doc.exists:
        return event_type in DEFAULT_ENABLED_NOTIFICATIONS

    prefs = doc.to_dict() or {}
    enabled_events = prefs.get("enabledNotifications", [])
    return event_type in enabled_events
