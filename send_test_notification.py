#!/usr/bin/env python3
"""
Send one test notification through the existing backend notification pipeline.

Example:
  python3 send_test_notification.py \
    --device-id smartlock_123 \
    --user-id user_abc \
    --type FORCED_ENTRY \
    --message "Test notification from script"
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from services.notification_service import create_notification


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send one test notification.")
    parser.add_argument("--device-id", required=True, help="Target device ID.")
    parser.add_argument(
        "--user-id",
        action="append",
        required=True,
        dest="user_ids",
        help="Target user ID. Repeat --user-id to send to multiple users.",
    )
    parser.add_argument(
        "--type",
        default="FORCED_ENTRY",
        help="Notification type (e.g. FORCED_ENTRY, DOORBELL_PRESSED).",
    )
    parser.add_argument(
        "--message",
        default="Test notification",
        help="Notification message shown to users.",
    )
    parser.add_argument(
        "--data",
        default="{}",
        help='Optional JSON string payload, e.g. \'{"source":"manual_test"}\'',
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Store notification records only, skip FCM push delivery.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        payload_data: Dict[str, Any] = json.loads(args.data)
        if not isinstance(payload_data, dict):
            raise ValueError("data must be a JSON object")
    except Exception as exc:
        raise SystemExit(f"Invalid --data JSON: {exc}") from exc

    user_ids: List[str] = [uid.strip() for uid in args.user_ids if uid and uid.strip()]
    if not user_ids:
        raise SystemExit("At least one valid --user-id is required.")

    result = create_notification(
        device_id=args.device_id.strip(),
        user_ids=user_ids,
        notif_type=args.type.strip(),
        message=args.message.strip(),
        data=payload_data,
        send_push=not args.no_push,
    )

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
