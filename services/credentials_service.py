import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from db import db
from utils import hash_password
from utils import verify_password

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
    return _sanitize_credentials_for_client(data)


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

    # Keypad:
    # - if a hashed PIN already exists, this endpoint may re-activate keypad without resetting the PIN.
    # - if no hashed PIN exists yet, force use of set_keypad_code for secure enrollment.
    if method == "keypad":
        if not existing.exists:
            return False, "No keypad code found. Set a keypad PIN first."

        existing_data = existing.to_dict() or {}
        keypad_data = (
            ((existing_data.get("authMethods") or {}).get("keypad") or {}).get("data") or {}
        )
        if not keypad_data.get("hashedPin"):
            return False, "No keypad code found. Set a keypad PIN first."

        doc_ref.update({
            "authMethods.keypad.isActive": True,
            "authMethods.keypad.enrolledAt": now,
            "isActive": True,
            "updatedAt": now,
        })
        return True, "keypad enrolled"

    # Fingerprint: require at least one finger registered first.
    if method == "fingerprint":
        if not existing.exists:
            return False, "No fingerprints registered. Add a fingerprint first."
        existing_data = existing.to_dict() or {}
        fingers = (
            ((existing_data.get("authMethods") or {}).get("fingerprint") or {})
            .get("data", {})
            .get("fingers", {})
        ) or {}
        if not fingers:
            return False, "No fingerprints registered. Add a fingerprint first."
        doc_ref.update({
            "authMethods.fingerprint.isActive": True,
            "authMethods.fingerprint.enrolledAt": now,
            "isActive": True,
            "updatedAt": now,
        })
        return True, "fingerprint enrolled"

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


def set_keypad_code(user_id: str, keypad_code: str) -> Tuple[bool, str]:
    """
    Securely enroll/update keypad code using bcrypt hashing.
    The plaintext code is never stored.
    """
    if not keypad_code or not keypad_code.isdigit():
        return False, "Keypad code must contain digits only"
    if len(keypad_code) < 4 or len(keypad_code) > 8:
        return False, "Keypad code must be 4 to 8 digits"

    doc_ref = db.collection("authCredentials").document(user_id)
    now = datetime.utcnow()
    hashed_pin = hash_password(keypad_code)

    payload = {
        f"authMethods.keypad.isActive": True,
        f"authMethods.keypad.data": {
            "hashedPin": hashed_pin,
            "length": len(keypad_code),
            "updatedAt": now,
        },
        f"authMethods.keypad.enrolledAt": now,
        "isActive": True,
        "updatedAt": now,
    }

    existing = doc_ref.get()
    if existing.exists:
        doc_ref.update(payload)
    else:
        doc_ref.set({
            "userId": user_id,
            "authMethods": {
                "keypad": {
                    "isActive": True,
                    "data": {
                        "hashedPin": hashed_pin,
                        "length": len(keypad_code),
                        "updatedAt": now,
                    },
                    "enrolledAt": now,
                }
            },
            "isActive": True,
            "createdAt": now,
            "updatedAt": now,
        })

    return True, "Keypad code updated"


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


def get_device_keypad_pins_count(device_id: str) -> int:
    """
    Count active keypad credentials for users related to this device.
    Tries accessControl first; falls back to users.deviceId mapping.
    """
    user_ids: set[str] = set()

    access_docs = list(
        db.collection("accessControl")
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .stream()
    )
    for access_doc in access_docs:
        access = access_doc.to_dict() or {}
        uid = access.get("userId")
        if uid:
            user_ids.add(uid)

    if not user_ids:
        owner_docs = list(
            db.collection("users")
            .where("deviceId", "==", device_id)
            .stream()
        )
        for d in owner_docs:
            user_ids.add(d.id)

    count = 0
    for uid in user_ids:
        cred_doc = db.collection("authCredentials").document(uid).get()
        if not cred_doc.exists:
            continue
        cred = cred_doc.to_dict() or {}
        keypad = ((cred.get("authMethods") or {}).get("keypad") or {})
        keypad_data = keypad.get("data") or {}
        if keypad.get("isActive", False) and bool(keypad_data.get("hashedPin")):
            count += 1

    return count


def verify_device_keypad_code(
    device_id: str,
    code: str,
) -> Tuple[bool, Optional[str], str]:
    """
    Verify an entered keypad code against active keypad credentials
    for users that currently have keypad access to the device.
    Returns (is_valid, matched_user_id, message).
    """
    if not code or not code.isdigit():
        return False, None, "Invalid code format"

    access_docs = list(
        db.collection("accessControl")
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .stream()
    )

    candidate_user_ids: List[str] = []
    for access_doc in access_docs:
        access = access_doc.to_dict() or {}
        uid = access.get("userId")
        access_methods = access.get("accessMethods", None)
        # Backwards-compatible behavior:
        # - if accessMethods is missing/empty, still allow keypad verification for this user
        # - if accessMethods is present, require keypad membership
        if not uid:
            continue
        if access_methods is None:
            candidate_user_ids.append(uid)
            continue
        methods_set = set(access_methods)
        if not methods_set or "keypad" in methods_set:
            candidate_user_ids.append(uid)

    if not candidate_user_ids:
        # Owner bypass fallback:
        # If accessControl is not set up yet, allow verification against
        # owner-like users bound to this device via users.deviceId.
        owner_docs = list(
            db.collection("users")
            .where("deviceId", "==", device_id)
            .limit(1)
            .stream()
        )
        if owner_docs:
            candidate_user_ids.append(owner_docs[0].id)
        else:
            return False, None, "No keypad-enabled users for this device"

    for uid in candidate_user_ids:
        cred_doc = db.collection("authCredentials").document(uid).get()
        if not cred_doc.exists:
            continue

        cred = cred_doc.to_dict() or {}
        if not cred.get("isActive", False):
            continue

        keypad = (cred.get("authMethods", {}) or {}).get("keypad", {}) or {}
        if not keypad.get("isActive", False):
            continue

        keypad_data = keypad.get("data", {}) or {}
        hashed_pin = keypad_data.get("hashedPin")
        if not hashed_pin:
            continue

        if verify_password(code, hashed_pin):
            return True, uid, "Keypad code verified"

    return False, None, "Invalid keypad code"


def add_fingerprint(
    user_id: str,
    nickname: str,
    template_data: str = "",
) -> Tuple[bool, str, Optional[str]]:
    """Add a named fingerprint entry for a user. Returns (ok, message, fingerprint_id)."""
    nickname = nickname.strip()
    if not nickname:
        return False, "Nickname is required", None
    if len(nickname) > 50:
        return False, "Nickname too long (max 50 chars)", None

    doc_ref = db.collection("authCredentials").document(user_id)
    now = datetime.utcnow()
    fingerprint_id = f"fp_{uuid.uuid4().hex[:12]}"
    finger_entry = {
        "nickname": nickname,
        "templateData": template_data,
        "addedAt": now,
    }

    existing = doc_ref.get()
    if existing.exists:
        doc_ref.update({
            "authMethods.fingerprint.isActive": True,
            "authMethods.fingerprint.enrolledAt": now,
            f"authMethods.fingerprint.data.fingers.{fingerprint_id}": finger_entry,
            "isActive": True,
            "updatedAt": now,
        })
    else:
        doc_ref.set({
            "userId": user_id,
            "authMethods": {
                "fingerprint": {
                    "isActive": True,
                    "enrolledAt": now,
                    "data": {"fingers": {fingerprint_id: finger_entry}},
                }
            },
            "isActive": True,
            "createdAt": now,
            "updatedAt": now,
        })

    return True, "Fingerprint added", fingerprint_id


def delete_fingerprint(user_id: str, fingerprint_id: str) -> Tuple[bool, str]:
    """Delete a specific fingerprint entry by its ID."""
    from google.cloud.firestore_v1 import transforms

    doc_ref = db.collection("authCredentials").document(user_id)
    existing = doc_ref.get()

    if not existing.exists:
        return False, "No credentials found"

    data = existing.to_dict() or {}
    fingers = (
        ((data.get("authMethods") or {}).get("fingerprint") or {})
        .get("data", {})
        .get("fingers", {})
    ) or {}

    if fingerprint_id not in fingers:
        return False, "Fingerprint not found"

    now = datetime.utcnow()
    doc_ref.update({
        f"authMethods.fingerprint.data.fingers.{fingerprint_id}": transforms.DELETE_FIELD,
        "updatedAt": now,
    })

    # Deactivate fingerprint method if no fingers remain
    updated = doc_ref.get().to_dict() or {}
    remaining = (
        ((updated.get("authMethods") or {}).get("fingerprint") or {})
        .get("data", {})
        .get("fingers", {})
    ) or {}
    if not remaining:
        doc_ref.update({"authMethods.fingerprint.isActive": False, "updatedAt": now})
        updated2 = doc_ref.get().to_dict() or {}
        all_inactive = all(
            not m.get("isActive", False)
            for m in (updated2.get("authMethods") or {}).values()
        )
        if all_inactive:
            doc_ref.update({"isActive": False})

    return True, "Fingerprint deleted"


def list_fingerprints(user_id: str) -> List[Dict[str, Any]]:
    """Return all fingerprint entries (no template data) for a user."""
    doc = db.collection("authCredentials").document(user_id).get()
    if not doc.exists:
        return []

    data = doc.to_dict() or {}
    fingers = (
        ((data.get("authMethods") or {}).get("fingerprint") or {})
        .get("data", {})
        .get("fingers", {})
    ) or {}

    result = []
    for fp_id, fp_data in fingers.items():
        added_at = fp_data.get("addedAt")
        result.append({
            "id": fp_id,
            "nickname": fp_data.get("nickname", ""),
            "addedAt": added_at.isoformat() if hasattr(added_at, "isoformat") else (str(added_at) if added_at else None),
        })
    result.sort(key=lambda x: x.get("addedAt") or "")
    return result


def _empty_credentials(user_id: str) -> Dict[str, Any]:
    return {
        "userId": user_id,
        "authMethods": {},
        "isActive": False,
    }


def _sanitize_credentials_for_client(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove sensitive material (e.g. keypad hash) from user-facing API responses.
    """
    safe = {**record}
    methods = safe.get("authMethods", {}) or {}
    sanitized_methods: Dict[str, Any] = {}

    for method_name, method_data in methods.items():
        entry = dict(method_data or {})
        if method_name == "keypad":
            raw_data = entry.get("data") or {}
            entry["data"] = {
                "hasCode": bool(raw_data.get("hashedPin")),
                "length": raw_data.get("length"),
            }
        elif method_name == "fingerprint":
            raw_data = entry.get("data") or {}
            fingers_raw = raw_data.get("fingers") or {}
            fingers_list = []
            for fp_id, fp_data in fingers_raw.items():
                added_at = fp_data.get("addedAt")
                fingers_list.append({
                    "id": fp_id,
                    "nickname": fp_data.get("nickname", ""),
                    "addedAt": added_at.isoformat() if hasattr(added_at, "isoformat") else (str(added_at) if added_at else None),
                })
            fingers_list.sort(key=lambda x: x.get("addedAt") or "")
            entry["data"] = {
                "count": len(fingers_list),
                "fingers": fingers_list,
            }
        sanitized_methods[method_name] = entry

    safe["authMethods"] = sanitized_methods
    return safe
