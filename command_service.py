import asyncio
from datetime import datetime, timedelta
from users_controller import db

RETRY_INTERVAL_SECONDS = 3
MAX_RETRIES = 3
COMMAND_TIMEOUT_SECONDS = 15


def create_command(device_id: str, user_id: str, command: str) -> str:
    ref = db.collection("commands").document()
    ref.set({
        "deviceId": device_id,
        "userId": user_id,
        "command": command,
        "status": "PENDING",
        "retryCount": 0,
        "createdAt": datetime.utcnow(),
    })
    return ref.id


def mark_command_sent(command_id: str):
    db.collection("commands").document(command_id).update({
        "status": "SENT",
        "lastSentAt": datetime.utcnow(),
    })


def mark_command_acknowledged(command_id: str):
    db.collection("commands").document(command_id).update({
        "status": "ACKED",
        "completedAt": datetime.utcnow(),
    })


def mark_command_failed(command_id: str, reason: str):
    db.collection("commands").document(command_id).update({
        "status": "FAILED",
        "failureReason": reason,
        "completedAt": datetime.utcnow(),
    })


async def deliver_command_with_retry(
    command_id: str,
    device_id: str,
    command: str,
    connected_devices: dict,
):
    start_time = datetime.utcnow()

    while True:
        cmd_ref = db.collection("commands").document(command_id).get()
        cmd = cmd_ref.to_dict()

        if cmd["status"] == "ACKED":
            return

        if datetime.utcnow() - start_time > timedelta(seconds=COMMAND_TIMEOUT_SECONDS):
            mark_command_failed(command_id, "Timeout waiting for device")
            return

        if cmd["retryCount"] >= MAX_RETRIES:
            mark_command_failed(command_id, "Max retries exceeded")
            return

        ws = connected_devices.get(device_id)
        if ws:
            try:
                await ws.send_text(command)
                mark_command_sent(command_id)
                db.collection("commands").document(command_id).update({
                    "retryCount": cmd["retryCount"] + 1,
                })
            except Exception:
                pass

        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
