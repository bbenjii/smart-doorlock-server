from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional
from db import db
from routers.auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


# token registration

class FCMTokenRequest(BaseModel):
    fcmToken: str
    deviceName: Optional[str] = None
    platform: Optional[str] = None

@router.post("/fcm-token")
async def register_fcm_token(body: FCMTokenRequest, current_user: dict = Depends(get_current_user)):
    """Register or update an FCM token for the authenticated user's mobile device."""
    user_id = current_user["user_id"]

    # Check if this token already exists for this user
    existing = (
        db.collection("userDevices")
        .where("userId", "==", user_id)
        .where("fcmToken", "==", body.fcmToken)
        .limit(1)
        .stream()
    )
    existing_doc = next(existing, None)

    now = datetime.utcnow()

    if existing_doc:
        # Update the existing record
        db.collection("userDevices").document(existing_doc.id).update({
            "deviceName": body.deviceName,
            "platform": body.platform,
            "updatedAt": now,
        })
        return {"ok": True, "message": "FCM token updated", "docId": existing_doc.id}

    # Create a new record
    doc_ref = db.collection("userDevices").add({
        "userId": user_id,
        "fcmToken": body.fcmToken,
        "deviceName": body.deviceName,
        "platform": body.platform,
        "createdAt": now,
        "updatedAt": now,
    })

    return {"ok": True, "message": "FCM token registered", "docId": doc_ref[1].id}

@router.delete("/fcm-token")
async def unregister_fcm_token(body: FCMTokenRequest, current_user: dict = Depends(get_current_user)):
    """Remove an FCM token (e.g. on logout). Only deletes tokens owned by the authenticated user."""
    user_id = current_user["user_id"]

    docs = (
        db.collection("userDevices")
        .where("userId", "==", user_id)
        .where("fcmToken", "==", body.fcmToken)
        .stream()
    )

    deleted = 0
    for doc in docs:
        db.collection("userDevices").document(doc.id).delete()
        deleted += 1

    if deleted == 0:
        raise HTTPException(status_code=404, detail="FCM token not found")

    return {"ok": True, "message": "FCM token removed"}


# notification preferences

class Prefs(BaseModel):
    enabledNotifications: List[str] = []

@router.post("/preferences/{device_id}")
async def update_notification_preferences(device_id: str, body: Prefs, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    prefs_doc_id = f"{user_id}_{device_id}"
    db.collection("userPreferences").document(prefs_doc_id).set({
        "userId": user_id,
        "deviceId": device_id,
        "enabledNotifications": body.enabledNotifications,
        "updatedAt": datetime.utcnow(),
    })
    return {"ok": True, "message": "Preferences updated"}

@router.get("/preferences/{device_id}")
async def get_notification_preferences(device_id: str, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    prefs_doc_id = f"{user_id}_{device_id}"
    doc = db.collection("userPreferences").document(prefs_doc_id).get()
    if not doc.exists:
        return {"enabledNotifications": ["FORCED_ENTRY", "FAILED_AUTH", "BATTERY_LOW", "DEVICE_OFFLINE", "DOORBELL_PRESSED", "WINDOW_SENSOR_TRIGGERED"]}
    return doc.to_dict()