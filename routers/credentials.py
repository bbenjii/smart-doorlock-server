from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from routers.auth import get_current_user
from services.auth_service import has_access
from services.credentials_service import (
    get_credentials,
    enroll_method,
    revoke_method,
    delete_method,
    get_device_credentials,
)
from services.audit_service import write_audit

router = APIRouter(prefix="/credentials", tags=["credentials"])


class EnrollRequest(BaseModel):
    method: str
    data: Optional[Dict[str, Any]] = None


# user-facing endpoints

@router.get("/me")
async def get_my_credentials(current_user: dict = Depends(get_current_user)):
    """Get the authenticated user's enrolled auth methods."""
    return get_credentials(current_user["user_id"])


@router.post("/me/enroll")
async def enroll_auth_method(body: EnrollRequest, current_user: dict = Depends(get_current_user)):
    """Enroll or update an auth method (face, fingerprint, keypad, bluetooth)."""
    user_id = current_user["user_id"]

    ok, msg = enroll_method(user_id, body.method, body.data)

    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    write_audit(
        action="CREDENTIAL_ENROLLED",
        actor_user_id=user_id,
        status="SUCCESS",
        details={"method": body.method},
    )

    return {"ok": True, "message": msg}


@router.post("/me/revoke")
async def revoke_auth_method(body: EnrollRequest, current_user: dict = Depends(get_current_user)):
    """Deactivate an auth method (keeps credential data for re-activation)."""
    user_id = current_user["user_id"]

    ok, msg = revoke_method(user_id, body.method)

    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    write_audit(
        action="CREDENTIAL_REVOKED",
        actor_user_id=user_id,
        status="SUCCESS",
        details={"method": body.method},
    )

    return {"ok": True, "message": msg}


@router.delete("/me/{method}")
async def delete_auth_method(method: str, current_user: dict = Depends(get_current_user)):
    """Permanently delete an auth method and its credential data."""
    user_id = current_user["user_id"]

    ok, msg = delete_method(user_id, method)

    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    write_audit(
        action="CREDENTIAL_DELETED",
        actor_user_id=user_id,
        status="SUCCESS",
        details={"method": method},
    )

    return {"ok": True, "message": msg}


# ------------- owner endpoints (manage other users) -------------

@router.get("/users/{user_id}")
async def get_user_credentials(user_id: str, device_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific user's credentials. Requires MANAGE_USERS on the device."""
    requester_id = current_user["user_id"]

    if requester_id != user_id:
        if not has_access(requester_id, device_id, "MANAGE_USERS"):
            raise HTTPException(status_code=403, detail="Access denied")

    return get_credentials(user_id)


# ------------- device-facing endpoint (ESP32 sync) -------------

@router.get("/device/{device_id}/sync")
async def sync_device_credentials(device_id: str, current_user: dict = Depends(get_current_user)):
    """
    Return all active credentials for a device.
    The ESP32 calls this to sync its local credential store.
    Requires owner-level access.
    """
    user_id = current_user["user_id"]

    if not has_access(user_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    credentials = get_device_credentials(device_id)

    write_audit(
        action="CREDENTIALS_SYNCED",
        actor_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"userCount": len(credentials)},
    )

    return {"ok": True, "deviceId": device_id, "credentials": credentials}
