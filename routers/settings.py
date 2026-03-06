from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from routers.auth import get_current_user
from services.auth_service import has_access
from services.settings_service import get_settings, update_settings
from services.audit_service import write_audit

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    notisEnabled: Optional[bool] = None
    autoLockEnabled: Optional[bool] = None
    motionEnabled: Optional[bool] = None
    faceRecogEnabled: Optional[bool] = None
    fingerprintEnabled: Optional[bool] = None
    bluetoothEnabled: Optional[bool] = None
    keypadEnabled: Optional[bool] = None
    cloudEnabled: Optional[bool] = None


@router.get("/{device_id}")
async def get_device_settings(device_id: str, current_user: dict = Depends(get_current_user)):
    """Get the merged settings for a device (defaults + device-level + user overrides)."""
    user_id = current_user["user_id"]

    if not has_access(user_id, device_id, "GET_STATUS"):
        raise HTTPException(status_code=403, detail="Access denied")

    return get_settings(device_id, user_id)


@router.put("/{device_id}")
async def update_device_settings(device_id: str, body: SettingsUpdate, current_user: dict = Depends(get_current_user)):
    """Update device-level settings. Requires UPDATE_SETTINGS permission (owner only)."""
    user_id = current_user["user_id"]

    if not has_access(user_id, device_id, "UPDATE_SETTINGS"):
        raise HTTPException(status_code=403, detail="Access denied")

    changes = body.model_dump(exclude_none=True)

    if not changes:
        raise HTTPException(status_code=400, detail="No settings provided")

    try:
        result = update_settings(device_id, changes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    write_audit(
        action="SETTINGS_UPDATED",
        actor_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"changed": list(changes.keys())},
    )

    return {"ok": True, "settings": result}


@router.put("/{device_id}/user")
async def update_user_settings(device_id: str, body: SettingsUpdate, current_user: dict = Depends(get_current_user)):
    """Update user-specific setting overrides for a device. Any authenticated user with access can set their own."""
    user_id = current_user["user_id"]

    if not has_access(user_id, device_id, "GET_STATUS"):
        raise HTTPException(status_code=403, detail="Access denied")

    changes = body.model_dump(exclude_none=True)

    if not changes:
        raise HTTPException(status_code=400, detail="No settings provided")

    try:
        result = update_settings(device_id, changes, user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"ok": True, "settings": result}
