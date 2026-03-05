import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from routers.auth import get_current_user
from services.auth_service import has_access
from services.command_service import create_command, deliver_command_with_retry
from services.credentials_service import (
    get_credentials,
    enroll_method,
    set_keypad_code,
    verify_device_keypad_code,
    get_device_keypad_pins_count,
    revoke_method,
    delete_method,
    get_device_credentials,
    start_fingerprint_enrollment,
    complete_fingerprint_enrollment,
    update_fingerprint_sync_status,
    delete_fingerprint,
    list_fingerprints,
)
from services.face_service import (
    start_face_enrollment,
    finalize_face_enrollment,
    revoke_face_credential,
    list_device_face_enrollments,
    get_face_enrollment_session,
)
from services.audit_service import write_audit
from ws.state import connected_devices, active_face_enrollment_session

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
    deviceId: str


class FingerprintSyncStatusRequest(BaseModel):
    status: str
    error: Optional[str] = None


class CompleteFingerprintEnrollRequest(BaseModel):
    enrollmentId: str
    success: bool
    sensorTemplateId: Optional[str] = None
    error: Optional[str] = None


class FaceEnrollStartRequest(BaseModel):
    userId: str
    deviceId: str


class FaceEnrollFinishRequest(BaseModel):
    sessionId: str


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
    """
    Start hardware fingerprint enrollment:
    1) Create pending fingerprint record.
    2) Send enroll command to lock device.
    """
    user_id = current_user["user_id"]
    device_id = body.deviceId

    if not has_access(user_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    if device_id not in connected_devices:
        raise HTTPException(status_code=400, detail="Device not connected. Connect lock and try again.")

    ok, msg, fp_id, enrollment_id = start_fingerprint_enrollment(user_id, device_id, body.nickname)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    # Format is intentionally simple for firmware parsing.
    command_text = f"ENROLL_FINGERPRINT:{enrollment_id}:{fp_id}"
    command_id = create_command(device_id, user_id, command_text)
    asyncio.create_task(
        deliver_command_with_retry(
            command_id,
            device_id,
            command_text,
            connected_devices,
        )
    )

    write_audit(
        action="FINGERPRINT_ENROLL_STARTED",
        actor_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"nickname": body.nickname, "fingerprintId": fp_id, "enrollmentId": enrollment_id},
    )
    return {
        "ok": True,
        "message": "Enrollment started. Scan fingerprint on lock sensor.",
        "fingerprintId": fp_id,
        "enrollmentId": enrollment_id,
        "commandId": command_id,
        "status": "pending",
    }


@router.post("/me/fingerprints/start-enroll")
async def start_my_fingerprint_enroll(body: AddFingerprintRequest, current_user: dict = Depends(get_current_user)):
    """
    Alias endpoint for clients using explicit enrollment naming.
    """
    return await add_my_fingerprint(body, current_user)


@router.post("/face/enroll/start")
async def start_face_enroll(body: FaceEnrollStartRequest, current_user: dict = Depends(get_current_user)):
    requester_id = current_user["user_id"]
    device_id = body.deviceId
    target_user_id = body.userId

    if requester_id != target_user_id and not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    if device_id not in connected_devices:
        raise HTTPException(status_code=400, detail="Device not connected. Connect lock and try again.")

    ok, msg, session_id = start_face_enrollment(
        user_id=target_user_id,
        device_id=device_id,
        initiated_by=requester_id,
    )
    if not ok or not session_id:
        raise HTTPException(status_code=400, detail=msg)

    active_face_enrollment_session[device_id] = session_id

    command_text = f"FACE_ENROLL_START:{session_id}"
    command_id = create_command(device_id, requester_id, command_text)
    asyncio.create_task(
        deliver_command_with_retry(
            command_id,
            device_id,
            command_text,
            connected_devices,
        )
    )

    write_audit(
        action="FACE_ENROLL_STARTED",
        actor_user_id=requester_id,
        target_user_id=target_user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"sessionId": session_id},
    )

    return {
        "ok": True,
        "message": msg,
        "sessionId": session_id,
        "commandId": command_id,
        "status": "in_progress",
    }


@router.post("/face/enroll/finish")
async def finish_face_enroll(body: FaceEnrollFinishRequest, current_user: dict = Depends(get_current_user)):
    requester_id = current_user["user_id"]
    session = get_face_enrollment_session(body.sessionId)
    if not session:
        raise HTTPException(status_code=404, detail="Enrollment session not found")

    device_id = session.get("deviceId")
    target_user_id = session.get("userId")
    if not device_id or not target_user_id:
        raise HTTPException(status_code=400, detail="Enrollment session is invalid")

    if requester_id != target_user_id and not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    ok, msg, payload = finalize_face_enrollment(body.sessionId)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    if active_face_enrollment_session.get(device_id) == body.sessionId:
        active_face_enrollment_session[device_id] = None

    write_audit(
        action="FACE_ENROLL_COMPLETED",
        actor_user_id=requester_id,
        target_user_id=target_user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"sessionId": body.sessionId},
    )

    return {"ok": True, "message": msg, "enrollment": payload}


@router.delete("/face/{user_id}")
async def revoke_face_for_user(
    user_id: str,
    device_id: str,
    current_user: dict = Depends(get_current_user),
):
    requester_id = current_user["user_id"]
    if requester_id != user_id and not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    ok, msg = revoke_face_credential(user_id, device_id)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    write_audit(
        action="FACE_CREDENTIAL_REVOKED",
        actor_user_id=requester_id,
        target_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
    )
    return {"ok": True, "message": msg}


@router.get("/face/{device_id}/enrolled")
async def get_face_enrolled_for_device(device_id: str, current_user: dict = Depends(get_current_user)):
    requester_id = current_user["user_id"]
    if not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    users = list_device_face_enrollments(device_id)
    return {"ok": True, "deviceId": device_id, "users": users}


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


@router.patch("/me/fingerprints/{fingerprint_id}/sync-status")
async def set_my_fingerprint_sync_status(
    fingerprint_id: str,
    body: FingerprintSyncStatusRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    ok, msg = update_fingerprint_sync_status(user_id, fingerprint_id, body.status, body.error)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    write_audit(
        action="FINGERPRINT_SYNC_STATUS_UPDATED",
        actor_user_id=user_id,
        status="SUCCESS",
        details={"fingerprintId": fingerprint_id, "status": body.status},
    )
    return {"ok": True, "message": msg}


@router.post("/device/{device_id}/fingerprints/complete-enroll")
async def complete_device_fingerprint_enroll(
    device_id: str,
    body: CompleteFingerprintEnrollRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Manual/owner endpoint to complete enrollment status when needed.
    """
    requester_id = current_user["user_id"]
    if not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    ok, msg, target_user_id, fingerprint_id = complete_fingerprint_enrollment(
        device_id=device_id,
        enrollment_id=body.enrollmentId,
        success=body.success,
        sensor_template_id=body.sensorTemplateId,
        error=body.error,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)

    write_audit(
        action="FINGERPRINT_ENROLL_COMPLETED",
        actor_user_id=requester_id,
        target_user_id=target_user_id,
        device_id=device_id,
        status="SUCCESS" if body.success else "FAILED",
        details={
            "fingerprintId": fingerprint_id,
            "enrollmentId": body.enrollmentId,
            "sensorTemplateId": body.sensorTemplateId,
            "error": body.error,
        },
    )
    return {"ok": True, "message": msg, "fingerprintId": fingerprint_id}


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
