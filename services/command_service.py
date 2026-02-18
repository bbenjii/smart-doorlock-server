import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any

from google.cloud import firestore
from db import db

RETRY_INTERVAL_SECONDS = 3
MAX_RETRIES = 3
COMMAND_TIMEOUT_SECONDS = 15
DEVICE_COMMAND_LOCK_TTL_SECONDS = COMMAND_TIMEOUT_SECONDS + 5


class CommandInFlightError(Exception):
    def __init__(self, command_id: Optional[str] = None, expires_at: Optional[datetime] = None):
        self.command_id = command_id
        self.expires_at = expires_at
        super().__init__("Command already in flight for this device")


def _normalize_naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def create_command(device_id: str, user_id: str, command: str) -> str:
    lock_ref = db.collection("deviceCommandLocks").document(device_id)
    cmd_ref = db.collection("commands").document()
    transaction = db.transaction()

    @firestore.transactional
    def _create_with_lock(txn) -> Tuple[str, Optional[Dict[str, Any]]]:
        now = datetime.utcnow()
        lock_snap = lock_ref.get(transaction=txn)
        if lock_snap.exists:
            lock_data = lock_snap.to_dict() or {}
            if lock_data.get("status") == "IN_FLIGHT":
                expires_at = _normalize_naive_utc(lock_data.get("expiresAt"))
                if expires_at is None or expires_at > now:
                    return "", {
                        "commandId": lock_data.get("commandId"),
                        "expiresAt": expires_at,
                    }

        txn.set(
            cmd_ref,
            {
                "deviceId": device_id,
                "userId": user_id,
                "command": command,
                "status": "PENDING",
                "retryCount": 0,
                "createdAt": now,
            },
        )
        txn.set(
            lock_ref,
            {
                "deviceId": device_id,
                "status": "IN_FLIGHT",
                "commandId": cmd_ref.id,
                "createdAt": now,
                "updatedAt": now,
                "expiresAt": now + timedelta(seconds=DEVICE_COMMAND_LOCK_TTL_SECONDS),
            },
            merge=True,
        )
        return cmd_ref.id, None

    command_id, lock_conflict = _create_with_lock(transaction)
    if lock_conflict is not None:
        raise CommandInFlightError(
            command_id=lock_conflict.get("commandId"),
            expires_at=lock_conflict.get("expiresAt"),
        )
    return command_id


def release_command_lock(device_id: str, command_id: Optional[str], final_status: str):
    lock_ref = db.collection("deviceCommandLocks").document(device_id)
    transaction = db.transaction()

    @firestore.transactional
    def _release(txn):
        now = datetime.utcnow()
        lock_snap = lock_ref.get(transaction=txn)
        if not lock_snap.exists:
            return

        lock_data = lock_snap.to_dict() or {}
        locked_command_id = lock_data.get("commandId")
        if command_id and locked_command_id and locked_command_id != command_id:
            return

        txn.set(
            lock_ref,
            {
                "status": "IDLE",
                "commandId": None,
                "updatedAt": now,
                "releasedAt": now,
                "lastCompletedCommandId": command_id or locked_command_id,
                "finalStatus": final_status,
                "expiresAt": None,
            },
            merge=True,
        )

    _release(transaction)


def mark_command_sent(command_id: str):
    db.collection("commands").document(command_id).update({
        "status": "SENT",
        "lastSentAt": datetime.utcnow(),
    })


def mark_command_acknowledged(command_id: str, device_id: Optional[str] = None):
    db.collection("commands").document(command_id).update({
        "status": "ACKED",
        "completedAt": datetime.utcnow(),
    })
    if device_id:
        release_command_lock(device_id=device_id, command_id=command_id, final_status="ACKED")


def mark_command_failed(command_id: str, reason: str, device_id: Optional[str] = None):
    db.collection("commands").document(command_id).update({
        "status": "FAILED",
        "failureReason": reason,
        "completedAt": datetime.utcnow(),
    })
    if device_id:
        release_command_lock(device_id=device_id, command_id=command_id, final_status="FAILED")


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
            mark_command_failed(command_id, "Timeout waiting for device", device_id=device_id)
            return

        if cmd["retryCount"] >= MAX_RETRIES:
            mark_command_failed(command_id, "Max retries exceeded", device_id=device_id)
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
