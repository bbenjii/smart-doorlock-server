from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from routers.auth import get_current_user
from services.auth_service import has_access
from services.credentials_service import (
    get_credentials,
    enroll_method,
    set_keypad_code,
    verify_device_keypad_code,
    get_device_keypad_pins_count,
    revoke_method,
    delete_method,
    get_device_credentials,
    add_fingerprint,
    delete_fingerprint,
    list_fingerprints,
)
from services.audit_service import write_audit

router = APIRouter(prefix="/credentials", tags=["credentials"])


class EnrollRequest(BaseModel):
    method: str
    data: Optional[Dict[str, Any]] = None


class KeypadCodeRequest(BaseModel):
    code: str
    confirmCode: str


class KeypadVerifyRequest(BaseModel):
    code: str


class AddFingerprintRequest(BaseModel):
    nickname: str
    templateData: Optional[str] = ""


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


@router.post("/me/keypad-code")
async def set_my_keypad_code(body: KeypadCodeRequest, current_user: dict = Depends(get_current_user)):
    """
    Set/update keypad code securely (bcrypt hash only, no plaintext persisted).
    """
    user_id = current_user["user_id"]

    if body.code != body.confirmCode:
        raise HTTPException(status_code=400, detail="Codes do not match")

    ok, msg = set_keypad_code(user_id, body.code)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    write_audit(
        action="KEYPAD_CODE_UPDATED",
        actor_user_id=user_id,
        status="SUCCESS",
    )

    return {"ok": True, "message": msg}


@router.get("/me/fingerprints")
async def list_my_fingerprints(current_user: dict = Depends(get_current_user)):
    """List all registered fingerprints (nickname + id, no template data)."""
    return {"ok": True, "fingerprints": list_fingerprints(current_user["user_id"])}


@router.post("/me/fingerprints")
async def add_my_fingerprint(body: AddFingerprintRequest, current_user: dict = Depends(get_current_user)):
    """Register a new fingerprint with a nickname."""
    user_id = current_user["user_id"]
    ok, msg, fp_id = add_fingerprint(user_id, body.nickname, body.templateData or "")
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    write_audit(
        action="FINGERPRINT_ADDED",
        actor_user_id=user_id,
        status="SUCCESS",
        details={"nickname": body.nickname},
    )
    return {"ok": True, "message": msg, "fingerprintId": fp_id}


@router.delete("/me/fingerprints/{fingerprint_id}")
async def delete_my_fingerprint(fingerprint_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a specific fingerprint by ID."""
    user_id = current_user["user_id"]
    ok, msg = delete_fingerprint(user_id, fingerprint_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    write_audit(
        action="FINGERPRINT_DELETED",
        actor_user_id=user_id,
        status="SUCCESS",
        details={"fingerprintId": fingerprint_id},
    )
    return {"ok": True, "message": msg}


@router.post("/device/{device_id}/verify-keypad")
async def verify_keypad_for_device(
    device_id: str,
    body: KeypadVerifyRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Verify keypad code against users who have keypad access on this device.
    Requires owner-level access to avoid exposing verification publicly.
    """
    requester_id = current_user["user_id"]

    if not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    is_valid, matched_user_id, msg = verify_device_keypad_code(device_id, body.code)

    write_audit(
        action="KEYPAD_VERIFY_ATTEMPT",
        actor_user_id=requester_id,
        device_id=device_id,
        target_user_id=matched_user_id,
        status="SUCCESS" if is_valid else "FAILED",
    )

    return {
        "ok": is_valid,
        "matchedUserId": matched_user_id,
        "message": msg,
    }


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


@router.get("/device/{device_id}/keypad-count")
async def get_device_keypad_count(device_id: str, current_user: dict = Depends(get_current_user)):
    """
    Return how many keypad PINs are configured for this device.
    """
    user_id = current_user["user_id"]
    if not has_access(user_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    count = get_device_keypad_pins_count(device_id)
    return {"ok": True, "deviceId": device_id, "keypadPinsCount": count}
