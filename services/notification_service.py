from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from uuid import uuid4

from firebase_admin import messaging
from db import db

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

    msg = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=_stringify_data(data or {}),
        tokens=tokens,
    )

    resp = messaging.send_multicast(msg)
    return {"sent": resp.success_count, "failed": resp.failure_count}

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