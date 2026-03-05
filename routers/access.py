import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from google.cloud.firestore_v1 import FieldFilter
from pydantic import BaseModel, Field

from db import db
from routers.auth import get_current_user
from services.audit_service import write_audit
from services.auth_service import has_access
from services.command_service import create_command, deliver_command_with_retry
from ws.state import connected_devices

router = APIRouter(prefix="/devices", tags=["access"])

VALID_ACCESS_LEVELS = {"owner", "guest"}
VALID_ACCESS_METHODS = {"face", "fingerprint", "keypad", "bluetooth"}


class GrantAccessRequest(BaseModel):
    email: str
    accessLevel: str = Field(default="guest")
    accessMethods: List[str] = Field(default_factory=lambda: ["face", "fingerprint", "keypad", "bluetooth"])


class UpdateAccessRequest(BaseModel):
    accessLevel: Optional[str] = None
    accessMethods: Optional[List[str]] = None


def _get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    docs = list(
        db.collection("users")
        .where(filter=FieldFilter("email", "==", email.strip().lower()))
        .limit(1)
        .stream()
    )
    if not docs:
        return None
    user = docs[0].to_dict() or {}
    user["id"] = docs[0].id
    return user


def _normalize_methods(methods: List[str]) -> List[str]:
    normalized = []
    for method in methods:
        m = (method or "").strip().lower()
        if m in VALID_ACCESS_METHODS and m not in normalized:
            normalized.append(m)
    return normalized


def _active_access_doc(device_id: str, user_id: str):
    docs = list(
        db.collection("accessControl")
        .where(filter=FieldFilter("deviceId", "==", device_id))
        .where(filter=FieldFilter("userId", "==", user_id))
        .where(filter=FieldFilter("enabled", "==", True))
        .limit(1)
        .stream()
    )
    return docs[0] if docs else None


def _owner_count(device_id: str) -> int:
    docs = list(
        db.collection("accessControl")
        .where(filter=FieldFilter("deviceId", "==", device_id))
        .where(filter=FieldFilter("enabled", "==", True))
        .where(filter=FieldFilter("accessLevel", "==", "owner"))
        .stream()
    )
    return len(docs)


def _list_finger_templates_for_device(user_id: str, device_id: str) -> List[str]:
    cred_doc = db.collection("authCredentials").document(user_id).get()
    if not cred_doc.exists:
        return []

    cred = cred_doc.to_dict() or {}
    fingers = (
        ((cred.get("authMethods") or {}).get("fingerprint") or {})
        .get("data", {})
        .get("fingers", {})
    ) or {}

    templates: List[str] = []
    for _finger_id, fp in fingers.items():
        fp_device = fp.get("deviceId")
        template_id = fp.get("sensorTemplateId")
        if not template_id:
            continue
        if fp_device and fp_device != device_id:
            continue
        if template_id not in templates:
            templates.append(template_id)
    return templates


@router.get("/me")
async def get_my_devices(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]

    access_docs = list(
        db.collection("accessControl")
        .where(filter=FieldFilter("userId", "==", user_id))
        .where(filter=FieldFilter("enabled", "==", True))
        .stream()
    )

    items: List[Dict[str, Any]] = []
    for doc in access_docs:
        access = doc.to_dict() or {}
        device_id = access.get("deviceId")
        if not device_id:
            continue

        device_doc = db.collection("devices").document(device_id).get()
        device_meta = device_doc.to_dict() if device_doc.exists else {}

        items.append(
            {
                "deviceId": device_id,
                "accessLevel": access.get("accessLevel", "guest"),
                "accessMethods": access.get("accessMethods", []),
                "enabled": access.get("enabled", True),
                "ownerId": device_meta.get("ownerId"),
            }
        )

    if not items:
        # Backward-compatible fallback for legacy users.deviceId mapping
        user_doc = db.collection("users").document(user_id).get()
        if user_doc.exists:
            user_data = user_doc.to_dict() or {}
            device_id = user_data.get("deviceId")
            if device_id:
                items.append(
                    {
                        "deviceId": device_id,
                        "accessLevel": "owner",
                        "accessMethods": ["face", "fingerprint", "keypad", "bluetooth"],
                        "enabled": True,
                        "ownerId": user_id,
                    }
                )

    return {"ok": True, "devices": items}


@router.get("/{device_id}/access")
async def list_device_access(device_id: str, current_user: dict = Depends(get_current_user)):
    requester_id = current_user["user_id"]

    can_manage = has_access(requester_id, device_id, "MANAGE_USERS")
    can_view = has_access(requester_id, device_id, "GET_STATUS")

    if not can_manage and not can_view:
        raise HTTPException(status_code=403, detail="Access denied")

    query = (
        db.collection("accessControl")
        .where(filter=FieldFilter("deviceId", "==", device_id))
        .where(filter=FieldFilter("enabled", "==", True))
    )

    docs = list(query.stream())
    users: List[Dict[str, Any]] = []

    for doc in docs:
        entry = doc.to_dict() or {}
        target_user_id = entry.get("userId")
        if not target_user_id:
            continue

        if not can_manage and target_user_id != requester_id:
            continue

        user_doc = db.collection("users").document(target_user_id).get()
        user_data = user_doc.to_dict() if user_doc.exists else {}

        users.append(
            {
                "userId": target_user_id,
                "email": user_data.get("email"),
                "firstName": user_data.get("firstName"),
                "lastName": user_data.get("lastName"),
                "accessLevel": entry.get("accessLevel", "guest"),
                "accessMethods": entry.get("accessMethods", []),
                "enabled": entry.get("enabled", True),
                "invitedBy": entry.get("invitedBy"),
                "createdAt": entry.get("createdAt").isoformat() if hasattr(entry.get("createdAt"), "isoformat") else None,
                "updatedAt": entry.get("updatedAt").isoformat() if hasattr(entry.get("updatedAt"), "isoformat") else None,
            }
        )

    users.sort(key=lambda x: ((x.get("accessLevel") != "owner"), x.get("email") or ""))
    return {"ok": True, "deviceId": device_id, "users": users}


@router.post("/{device_id}/access")
async def grant_device_access(
    device_id: str,
    body: GrantAccessRequest,
    current_user: dict = Depends(get_current_user),
):
    requester_id = current_user["user_id"]

    if not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    access_level = (body.accessLevel or "guest").strip().lower()
    if access_level not in VALID_ACCESS_LEVELS:
        raise HTTPException(status_code=400, detail="Invalid accessLevel")

    methods = _normalize_methods(body.accessMethods)
    if not methods:
        raise HTTPException(status_code=400, detail="At least one valid access method is required")

    target_user = _get_user_by_email(body.email)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not registered. They must create an account first.")

    target_user_id = target_user["id"]
    existing = _active_access_doc(device_id, target_user_id)
    if existing:
        raise HTTPException(status_code=409, detail="This user already has access to this device.")

    now = datetime.utcnow()
    db.collection("accessControl").add(
        {
            "deviceId": device_id,
            "userId": target_user_id,
            "accessLevel": access_level,
            "accessMethods": methods,
            "enabled": True,
            "validFrom": None,
            "validUntil": None,
            "invitedBy": requester_id,
            "createdAt": now,
            "updatedAt": now,
        }
    )

    write_audit(
        action="ACCESS_GRANTED",
        actor_user_id=requester_id,
        target_user_id=target_user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"accessLevel": access_level, "accessMethods": methods},
    )

    return {"ok": True, "message": "Access granted", "userId": target_user_id}


@router.patch("/{device_id}/access/{user_id}")
async def update_device_access(
    device_id: str,
    user_id: str,
    body: UpdateAccessRequest,
    current_user: dict = Depends(get_current_user),
):
    requester_id = current_user["user_id"]

    if not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    doc = _active_access_doc(device_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Access entry not found")

    current = doc.to_dict() or {}
    current_level = current.get("accessLevel", "guest")

    updates: Dict[str, Any] = {}

    if body.accessLevel is not None:
        next_level = body.accessLevel.strip().lower()
        if next_level not in VALID_ACCESS_LEVELS:
            raise HTTPException(status_code=400, detail="Invalid accessLevel")
        if current_level == "owner" and next_level != "owner" and _owner_count(device_id) <= 1:
            raise HTTPException(status_code=400, detail="Cannot downgrade the only owner.")
        updates["accessLevel"] = next_level

    if body.accessMethods is not None:
        methods = _normalize_methods(body.accessMethods)
        if not methods:
            raise HTTPException(status_code=400, detail="At least one valid access method is required")
        updates["accessMethods"] = methods

    if not updates:
        raise HTTPException(status_code=400, detail="No changes provided")

    updates["updatedAt"] = datetime.utcnow()
    doc.reference.update(updates)

    write_audit(
        action="ACCESS_UPDATED",
        actor_user_id=requester_id,
        target_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details=updates,
    )

    return {"ok": True, "message": "Access updated"}


@router.delete("/{device_id}/access/{user_id}")
async def revoke_device_access(
    device_id: str,
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    requester_id = current_user["user_id"]

    if not has_access(requester_id, device_id, "MANAGE_USERS"):
        raise HTTPException(status_code=403, detail="Access denied")

    doc = _active_access_doc(device_id, user_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Access entry not found")

    current = doc.to_dict() or {}
    is_owner = current.get("accessLevel") == "owner"

    if is_owner and _owner_count(device_id) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the only owner.")

    if user_id == requester_id and is_owner and _owner_count(device_id) <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove yourself as the only owner.")

    doc.reference.update({"enabled": False, "updatedAt": datetime.utcnow()})

    templates = _list_finger_templates_for_device(user_id, device_id)
    for template_id in templates:
        if device_id not in connected_devices:
            continue
        command_text = f"DELETE_FINGERPRINT:{template_id}"
        command_id = create_command(device_id, requester_id, command_text)
        asyncio.create_task(
            deliver_command_with_retry(command_id, device_id, command_text, connected_devices)
        )

    write_audit(
        action="ACCESS_REVOKED",
        actor_user_id=requester_id,
        target_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"fingerprintTemplatesRemoved": len(templates)},
    )

    return {"ok": True, "message": "Access revoked"}
