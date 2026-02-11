from fastapi import APIRouter, Depends
from datetime import datetime
from pydantic import BaseModel
from typing import List
from db import db
from routers.auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

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
        return {"enabledNotifications": ["FORCED_ENTRY", "FAILED_AUTH", "BATTERY_LOW", "DEVICE_OFFLINE"]}
    return doc.to_dict()