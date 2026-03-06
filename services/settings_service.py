from datetime import datetime
from typing import Optional, Dict, Any

from db import db

# default settings applied when a device is first claimed or has no settings record
DEFAULTS = {
    "notisEnabled": True,
    "autoLockEnabled": True,
    "motionEnabled": True,
    "faceRecogEnabled": True,
    "fingerprintEnabled": True,
    "bluetoothEnabled": True,
    "keypadEnabled": True,
    "cloudEnabled": False,
}

ALLOWED_FIELDS = set(DEFAULTS.keys())

AUTH_METHOD_TO_SETTING_FIELD = {
    "face": "faceRecogEnabled",
    "fingerprint": "fingerprintEnabled",
    "keypad": "keypadEnabled",
    "bluetooth": "bluetoothEnabled",
}


def _settings_doc_id(device_id: str, user_id: Optional[str] = None) -> str:
    """
    Document id convention:
      - device-level settings  ->  deviceId
      - user-specific overrides ->  userId_deviceId
    """
    if user_id:
        return f"{user_id}_{device_id}"
    return device_id


def get_settings(device_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Return the merged settings for a device.
    """
    merged = dict(DEFAULTS)

    # device-level settings
    device_doc = db.collection("settings").document(_settings_doc_id(device_id)).get()
    if device_doc.exists:
        data = device_doc.to_dict() or {}
        for key in ALLOWED_FIELDS:
            if key in data:
                merged[key] = data[key]

    # user-specific overrides
    if user_id:
        user_doc = db.collection("settings").document(_settings_doc_id(device_id, user_id)).get()
        if user_doc.exists:
            data = user_doc.to_dict() or {}
            for key in ALLOWED_FIELDS:
                if key in data:
                    merged[key] = data[key]

    merged["deviceId"] = device_id
    if user_id:
        merged["userId"] = user_id

    return merged


def update_settings(
    device_id: str,
    changes: Dict[str, Any],
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist setting changes for a device (or a user+device pair).
    """
    filtered = {k: v for k, v in changes.items() if k in ALLOWED_FIELDS}

    if not filtered:
        raise ValueError("No valid settings fields provided")

    doc_id = _settings_doc_id(device_id, user_id)
    now = datetime.utcnow()

    payload = {
        **filtered,
        "deviceId": device_id,
        "updatedAt": now,
    }
    if user_id:
        payload["userId"] = user_id

    doc_ref = db.collection("settings").document(doc_id)
    existing = doc_ref.get()

    if existing.exists:
        doc_ref.update({**filtered, "updatedAt": now})
    else:
        payload["createdAt"] = now
        doc_ref.set(payload)

    return get_settings(device_id, user_id)


def is_auth_method_enabled(device_id: str, auth_method: str) -> bool:
    """
    Device-level auth method gate.
    Uses defaults when explicit device settings are absent.
    """
    setting_field = AUTH_METHOD_TO_SETTING_FIELD.get((auth_method or "").strip().lower())
    if not setting_field:
        return True

    settings = get_settings(device_id)
    return bool(settings.get(setting_field, True))
