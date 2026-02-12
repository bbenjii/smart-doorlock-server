from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from db import db

VALID_AUTH_METHODS = {"face", "fingerprint", "keypad", "bluetooth"}


def get_credentials(user_id: str) -> Dict[str, Any]:
    """
    Return the full authCredentials record for a user.
    Returns defaults (all inactive, no data) if no record exists.
    """
    doc = db.collection("authCredentials").document(user_id).get()

    if not doc.exists:
        return _empty_credentials(user_id)

    data = doc.to_dict() or {}
    data["userId"] = user_id
    return data


def enroll_method(
    user_id: str,
    method: str,
    credential_data: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Enroll or update a single auth method for a user.
    """
    if method not in VALID_AUTH_METHODS:
        return False, f"Invalid auth method: {method}. Valid: {', '.join(sorted(VALID_AUTH_METHODS))}"

    doc_ref = db.collection("authCredentials").document(user_id)
    now = datetime.utcnow()

    existing = doc_ref.get()

    if existing.exists:
        doc_ref.update({
            f"authMethods.{method}.isActive": True,
            f"authMethods.{method}.data": credential_data or {},
            f"authMethods.{method}.enrolledAt": now,
            "isActive": True,
            "updatedAt": now,
        })
    else:
        doc_ref.set({
            "userId": user_id,
            "authMethods": {
                method: {
                    "isActive": True,
                    "data": credential_data or {},
                    "enrolledAt": now,
                }
            },
            "isActive": True,
            "createdAt": now,
            "updatedAt": now,
        })

    return True, f"{method} enrolled"


def revoke_method(user_id: str, method: str) -> Tuple[bool, str]:
    """Deactivate a single auth method without deleting the credential data."""
    if method not in VALID_AUTH_METHODS:
        return False, f"Invalid auth method: {method}"

    doc_ref = db.collection("authCredentials").document(user_id)
    existing = doc_ref.get()

    if not existing.exists:
        return False, "No credentials found for user"

    methods = (existing.to_dict() or {}).get("authMethods", {})
    if method not in methods:
        return False, f"{method} not enrolled"

    now = datetime.utcnow()
    doc_ref.update({
        f"authMethods.{method}.isActive": False,
        "updatedAt": now,
    })

    # if all methods are now inactive, mark the whole record inactive
    updated = doc_ref.get().to_dict() or {}
    all_inactive = all(
        not m.get("isActive", False)
        for m in updated.get("authMethods", {}).values()
    )
    if all_inactive:
        doc_ref.update({"isActive": False})

    return True, f"{method} revoked"


def delete_method(user_id: str, method: str) -> Tuple[bool, str]:
    """Remove a credential method entirely (data + status)."""
    if method not in VALID_AUTH_METHODS:
        return False, f"Invalid auth method: {method}"

    doc_ref = db.collection("authCredentials").document(user_id)
    existing = doc_ref.get()

    if not existing.exists:
        return False, "No credentials found for user"

    methods = (existing.to_dict() or {}).get("authMethods", {})
    if method not in methods:
        return False, f"{method} not enrolled"

    from google.cloud.firestore_v1 import transforms
    now = datetime.utcnow()

    doc_ref.update({
        f"authMethods.{method}": transforms.DELETE_FIELD,
        "updatedAt": now,
    })

    # check if any methods remain
    updated = doc_ref.get().to_dict() or {}
    remaining = updated.get("authMethods", {})
    if not remaining:
        doc_ref.update({"isActive": False})

    return True, f"{method} deleted"


def get_device_credentials(device_id: str) -> List[Dict[str, Any]]:
    """
    Return all active credentials for users who have access to a device.
    Used by the ESP32 to sync its local credential store.
    """
    # get all users with active access to this device
    access_docs = (
        db.collection("accessControl")
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .stream()
    )

    results: List[Dict[str, Any]] = []

    for access_doc in access_docs:
        access = access_doc.to_dict() or {}
        uid = access.get("userId")
        allowed_methods = set(access.get("accessMethods", []))

        if not uid or not allowed_methods:
            continue

        cred_doc = db.collection("authCredentials").document(uid).get()
        if not cred_doc.exists:
            continue

        cred = cred_doc.to_dict() or {}
        if not cred.get("isActive", False):
            continue

        all_methods = cred.get("authMethods", {})

        # filter to only methods that are both enrolled+active AND allowed on this device
        filtered_methods = {}
        for method_name, method_data in all_methods.items():
            if method_name in allowed_methods and method_data.get("isActive", False):
                filtered_methods[method_name] = method_data

        if filtered_methods:
            results.append({
                "userId": uid,
                "authMethods": filtered_methods,
            })

    return results


def _empty_credentials(user_id: str) -> Dict[str, Any]:
    return {
        "userId": user_id,
        "authMethods": {},
        "isActive": False,
    }
