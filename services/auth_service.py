from datetime import datetime, timedelta
from typing import Optional
from db import db

ROLE_PERMISSIONS = {
    "owner": {
        "LOCK",
        "UNLOCK",
        "GET_STATUS",
        "UPDATE_SETTINGS",
        "MANAGE_USERS",
        "CLAIM_DEVICE",
    },
    "guest": {
        "LOCK",
        "UNLOCK",
        "GET_STATUS",
    },
}


def has_access(
    user_id: str,
    device_id: str,
    action: str,
    auth_method: Optional[str] = None,
) -> bool:
    """
    Central authorization check.
    """

    docs = (
        db.collection("accessControl")
        .where("userId", "==", user_id)
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .limit(1)
        .stream()
    )

    record = next(docs, None)
    if not record:
        return False

    access = record.to_dict()
    now = datetime.utcnow()

    # Time validity
    if access.get("validFrom") and now < access["validFrom"]:
        return False
    if access.get("validUntil") and now > access["validUntil"]:
        return False

    role = access.get("accessLevel")
    if role not in ROLE_PERMISSIONS:
        return False

    if action not in ROLE_PERMISSIONS[role]:
        return False

    if auth_method:
        allowed = access.get("accessMethods", [])
        if auth_method not in allowed:
            return False

    return True

PAIRING_EXPIRY_MINUTES = 5


def store_pairing_code(device_id: str, pairing_code: str):
    """
    Called when ESP32 enters pairing mode.
    """
    db.collection("devicePairing").document(device_id).set({
        "pairingCode": pairing_code,
        "expiresAt": datetime.utcnow() + timedelta(minutes=PAIRING_EXPIRY_MINUTES),
    })


def claim_device(user_id: str, device_id: str, pairing_code: str):
    """
    Secure device claiming.
    """

    pairing_ref = db.collection("devicePairing").document(device_id).get()
    if not pairing_ref.exists:
        return False, "Pairing not active"

    pairing = pairing_ref.to_dict()

    if pairing["pairingCode"] != pairing_code:
        return False, "Invalid pairing code"

    if datetime.utcnow() > pairing["expiresAt"]:
        return False, "Pairing code expired"

    device_ref = db.collection("devices").document(device_id).get()
    if device_ref.exists and device_ref.to_dict().get("ownerId"):
        return False, "Device already claimed"

    # Assign owner
    db.collection("devices").document(device_id).set({
        "ownerId": user_id,
        "createdAt": datetime.utcnow(),
    }, merge=True)

    # Grant owner access
    db.collection("accessControl").add({
        "userId": user_id,
        "deviceId": device_id,
        "accessLevel": "owner",
        "accessMethods": ["face", "fingerprint", "keypad", "bluetooth"],
        "enabled": True,
        "createdAt": datetime.utcnow(),
    })

    # Cleanup pairing entry
    db.collection("devicePairing").document(device_id).delete()

    return True, "Device claimed"
