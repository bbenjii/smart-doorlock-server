"""
Seed one entry per event/log type into Firestore for testing.
Usage: 
2 events and 1 log only (for realtime testing):
python seed_test_data.py --realtime
Full dataset with many events/logs:
python seed_test_data.py --full

"""
import argparse
import os
import sys
from datetime import datetime, timedelta

os.environ["FIREBASE_SERVICE_ACCOUNT_PATH"] = (
    "/Users/abou/Desktop/Others/smart-doorlock-project-firebase-adminsdk-fbsvc-18e6852cc4.json"
)

from db import db  

DEVICE_ID = "smartlock_5C567740C86C"
USER_ID = "user_test_events"

now = datetime.utcnow()


def ts(minutes_ago: int = 0, hours_ago: int = 0, days_ago: int = 0) -> datetime:
    return now - timedelta(minutes=minutes_ago, hours=hours_ago, days=days_ago)


# ─── Events ────────────────────────────────────────────────────────
events = [
    {
        "deviceId": DEVICE_ID,
        "eventType": "LOCKED",
        "userId": USER_ID,
        "authMethod": "KEYPAD",
        "metadata": {},
        "timestamp": ts(minutes_ago=10),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "UNLOCKED",
        "userId": USER_ID,
        "authMethod": "FACE_ID",
        "metadata": {},
        "timestamp": ts(minutes_ago=45),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "FAILED_AUTH",
        "userId": None,
        "authMethod": "KEYPAD",
        "metadata": {"attempts": 3},
        "timestamp": ts(hours_ago=1),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "MOTION_DETECTED",
        "userId": None,
        "authMethod": None,
        "metadata": {"zone": "front_door"},
        "timestamp": ts(hours_ago=2),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "FORCED_ENTRY",
        "userId": None,
        "authMethod": None,
        "metadata": {},
        "timestamp": ts(hours_ago=3),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "BATTERY_LOW",
        "userId": None,
        "authMethod": None,
        "metadata": {"level": 12},
        "timestamp": ts(hours_ago=6),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "DEVICE_OFFLINE",
        "userId": None,
        "authMethod": None,
        "metadata": {},
        "timestamp": ts(hours_ago=23),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "DEVICE_ONLINE",
        "userId": None,
        "authMethod": None,
        "metadata": {},
        "timestamp": ts(hours_ago=22),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "DOORBELL_PRESSED",
        "userId": None,
        "authMethod": None,
        "metadata": {},
        "timestamp": ts(minutes_ago=5),
    },
    {
        "deviceId": DEVICE_ID,
        "eventType": "WINDOW_SENSOR_TRIGGERED",
        "userId": None,
        "authMethod": None,
        "metadata": {"sensor": "living_room_window"},
        "timestamp": ts(hours_ago=4),
    },
]

# ─── Audit logs ────────────────────────────────────────────────────
logs = [
    {
        "logType": "AUDIT",
        "action": "COMMAND_ISSUED",
        "actorUserId": USER_ID,
        "targetUserId": None,
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {"command": "LOCK"},
        "timestamp": ts(minutes_ago=8),
    },
    {
        "logType": "AUDIT",
        "action": "COMMAND_DENIED",
        "actorUserId": "user_guest_01",
        "targetUserId": None,
        "deviceId": DEVICE_ID,
        "status": "DENIED",
        "details": {"command": "UNLOCK"},
        "timestamp": ts(minutes_ago=30),
    },
    {
        "logType": "AUDIT",
        "action": "CLAIM_DEVICE",
        "actorUserId": USER_ID,
        "targetUserId": None,
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {},
        "timestamp": ts(days_ago=7),
    },
    {
        "logType": "AUDIT",
        "action": "SETTINGS_UPDATED",
        "actorUserId": USER_ID,
        "targetUserId": None,
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {"field": "auto_lock_delay"},
        "timestamp": ts(days_ago=2),
    },
    {
        "logType": "AUDIT",
        "action": "USER_ADDED",
        "actorUserId": USER_ID,
        "targetUserId": "user_guest_01",
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {},
        "timestamp": ts(days_ago=4),
    },
    {
        "logType": "AUDIT",
        "action": "USER_REMOVED",
        "actorUserId": USER_ID,
        "targetUserId": "user_guest_02",
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {},
        "timestamp": ts(days_ago=5),
    },
    {
        "logType": "AUDIT",
        "action": "ROLE_CHANGED",
        "actorUserId": USER_ID,
        "targetUserId": "user_guest_01",
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {"newRole": "VIEWER"},
        "timestamp": ts(days_ago=3),
    },
]


def seed_full():
    print(f"Seeding device: {DEVICE_ID}\n")

    print("Writing events...")
    for e in events:
        ref = db.collection("events").document()
        ref.set(e)
        print(f"  ✓ {e['eventType']}")

    print("\nWriting audit logs...")
    for log in logs:
        ref = db.collection("logs").document()
        ref.set(log)
        print(f"  ✓ {log['action']}")

    print(f"\nDone — {len(events)} events + {len(logs)} logs written.")


def seed_realtime():
    current = datetime.utcnow()

    realtime_events = [
        {
            "deviceId": DEVICE_ID,
            "eventType": "DOORBELL_PRESSED",
            "userId": None,
            "authMethod": None,
            "metadata": {"source": "manual_realtime_seed", "label": "event_1"},
            "timestamp": current,
        },
        {
            "deviceId": DEVICE_ID,
            "eventType": "MOTION_DETECTED",
            "userId": None,
            "authMethod": None,
            "metadata": {"source": "manual_realtime_seed", "label": "event_2"},
            "timestamp": current + timedelta(seconds=1),
        },
    ]

    realtime_log = {
        "logType": "AUDIT",
        "action": "COMMAND_ISSUED",
        "actorUserId": USER_ID,
        "targetUserId": None,
        "deviceId": DEVICE_ID,
        "status": "SUCCESS",
        "details": {"source": "manual_realtime_seed", "command": "UNLOCK"},
        "timestamp": current + timedelta(seconds=2),
    }

    print(f"Seeding realtime test data for device: {DEVICE_ID}\n")

    for event in realtime_events:
        ref = db.collection("events").document()
        ref.set(event)
        print(f"  ✓ event {event['eventType']} ({ref.id})")

    log_ref = db.collection("logs").document()
    log_ref.set(realtime_log)
    print(f"  ✓ log {realtime_log['action']} ({log_ref.id})")

    print("\nDone — 2 events + 1 log written.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--realtime",
        action="store_true",
        help="Write exactly 2 events and 1 log for realtime testing.",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Write the full demo dataset (many events/logs).",
    )
    args = parser.parse_args()

    # Default to realtime to avoid accidentally spamming full test data.
    if args.full:
        seed_full()
    else:
        seed_realtime()
