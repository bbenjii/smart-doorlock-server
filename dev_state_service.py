from datetime import datetime
from typing import Optional
from db import db

def update_device_state(device_id: str, **fields):
    """
    Persist canonical device state in Firestore.

    This is the ONLY function that writes to deviceState.
    """
    db.collection("deviceState").document(device_id).set(
        {
            **fields,
            "lastUpdatedAt": datetime.utcnow(),
        },
        merge=True,
    )


# ------------------ Online / Offline ------------------

def mark_device_online(device_id: str):
    """
    Mark device as online and update last seen timestamp.
    """
    update_device_state(
        device_id=device_id,
        online=True,
        lastSeenAt=datetime.utcnow(),
    )


def mark_device_offline(device_id: str):
    """
    Mark device as offline.
    """
    update_device_state(
        device_id=device_id,
        online=False,
        lastSeenAt=datetime.utcnow(),
    )


# ------------------ Runtime State ---------------------

def persist_status_update(
    device_id: str,
    status: Optional[str] = None,
    battery_level: Optional[int] = None,
    current_user: Optional[str] = None,
):
    """
    Persist device runtime state sent by ESP32.
    """
    fields = {
        "online": True,
        "lastSeenAt": datetime.utcnow(),
    }

    if status is not None:
        fields["status"] = status

    if battery_level is not None:
        fields["batteryLevel"] = battery_level

    if current_user is not None:
        fields["currentUser"] = current_user

    update_device_state(device_id, **fields)


# ------------------ Command Results -------------------

def persist_command_result(device_id: str, new_status: str):
    """
    Persist state change after a command execution.
    """
    update_device_state(
        device_id=device_id,
        status=new_status,
        online=True,
    )

# ------------------ last seen retrieval -------------------
def get_last_seen(device_id: str):
    doc = db.collection("deviceState").document(device_id).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("lastSeenAt")
