import asyncio
import json
from datetime import datetime
from pydoc import text
from typing import Dict, Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from services.audit_service import write_audit
from services.command_service import mark_command_acknowledged
from services.dev_state_service import (
    mark_device_online,
    mark_device_offline,
    persist_status_update,
    persist_command_result,
    get_last_seen,
)
from services.event_service import ingest_event
from services.media_service import store_media_bytes

from ws.state import (
    connected_devices,
    device_subscribers,
    last_status,
    last_frame_bytes,
    last_frame_meta,
    frame_events,
    pending_media_event,
)
from services.cache_service import set_latest_frame

from services.notification_service import create_notification, get_notification_recipients, build_notification_message, \
    get_notification_recipients_by_access, should_user_receive_notification
from services.notification_rules import should_notify 
from services.credentials_service import verify_device_keypad_code
from services.credentials_service import complete_fingerprint_enrollment


async def broadcast_status(device_id: str, status_payload: Dict[str, Any]):
    """Send status to all subscribed clients for this device."""
    subscribers = device_subscribers.get(device_id, set())
    dead = []
    for ws in subscribers:
        try:
            await ws.send_json(status_payload)
        except WebSocketDisconnect:
            dead.append(ws)
        except Exception:
            dead.append(ws)
    for ws in dead:
        subscribers.discard(ws)


async def connection_watchdog(websocket: WebSocket, device_id: str):
    try:
        while True:
            await asyncio.sleep(5)

            ws = connected_devices.get(device_id)
            if ws:
                await ws.send_text("GET_STATUS")

            last_seen = get_last_seen(device_id)
            if not last_seen:
                continue

            if (datetime.now() - last_seen).seconds > 10:
                print(f"[{device_id}] Device considered offline (watchdog)")
                break

    except asyncio.CancelledError:
        pass


async def handle_device_connection(websocket: WebSocket):
    device_id = None
    watchdog = None
    try:
        # The first message must still be the JSON "hello"
        hello_text = await websocket.receive_text()
        try:
            hello_json = json.loads(hello_text)
        except json.JSONDecodeError:
            print("Invalid hello JSON:", hello_text)
            await websocket.close()
            return

        if hello_json.get("type") != "hello":
            print("First message not 'hello'", hello_json)
            await websocket.close()
            return

        device_id = hello_json.get("deviceId")
        if not device_id:
            print("No deviceId in hello")
            await websocket.close()
            return

        connected_devices[device_id] = websocket
        print(f"Device registered: {device_id}")
        mark_device_online(device_id)

        if device_id in last_status:
            await broadcast_status(device_id, last_status[device_id])

        watchdog = asyncio.create_task(connection_watchdog(websocket, device_id))

        # Main loop: now handle both text and binary messages
        while True:
            if not (
                websocket.client_state == WebSocketState.CONNECTED
                and websocket.application_state == WebSocketState.CONNECTED
            ):
                print("WebSocket is already closed.")

            message = await websocket.receive()

            # Client closed
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            binary = message.get("bytes")
            
            if text is not None:
                await handle_device_text_message(device_id, text, websocket)
            elif binary is not None:
                await handle_device_binary_message(device_id, binary)

    except WebSocketDisconnect:
        print(f"Device disconnected: {device_id}")
    except Exception as e:
        print(f"Device WS error for {device_id}: {e}")
    finally:
        if device_id and connected_devices.get(device_id) is websocket:
            del connected_devices[device_id]

        if device_id:
            mark_device_offline(device_id)

        if watchdog:
            watchdog.cancel()
        print(f"Device unregistered: {device_id}")


async def handle_device_text_message(device_id: str, text: str, websocket: WebSocket):
    print(f"[{device_id}] (text) -> {text}")
    try:
        data = json.loads(text)
        msg_type = data.get("type")
    except json.JSONDecodeError:
        msg_type = None
        data = None

    if msg_type == "status":
        persist_status_update(
            device_id=device_id,
            status=data.get("status"),
            battery_level=data.get("battery"),
            current_user=data.get("currentUser"),
        )

        data["online"] = True
        last_status[device_id] = data

        await broadcast_status(device_id, data)

    elif msg_type == "command_finished":
        print(f"Command finished from {device_id}: {data}")
        command_id = data.get("commandId")
        if command_id:
            mark_command_acknowledged(command_id)

        new_status = data.get("new_status")
        if isinstance(new_status, str):
            persist_command_result(device_id, new_status)

        now = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
        data["timestamp"] = now
        data["online"] = True
        if isinstance(new_status, str):
            status_payload = {
                "type": "status",
                "deviceId": device_id,
                "status": new_status,
            }
            last_status[device_id] = status_payload
            await broadcast_status(device_id, status_payload)

    elif msg_type == "event":
        event_type = data.get("eventType")
        if not event_type:
            return
        actor_user_id = data.get("userId")

        # persist the event and keep the returned event id so that
        # a following binary frame can be linked to it
        event_id = ingest_event(
            device_id=device_id,
            event_type=event_type,
            user_id=actor_user_id,
            auth_method=data.get("authMethod"),
            metadata=data.get("metadata"),
        )

        # track that the next incoming binary from this device is media for this event
        pending_media_event[device_id] = event_id

        # push the new event to all subscribed mobile clients in real-time
        if event_id:
            await broadcast_status(device_id, {
                "type": "event",
                "eventId": event_id,
                "eventType": event_type,
                "userId": actor_user_id,
                "authMethod": data.get("authMethod"),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })

        should_send, required_access_level = should_notify(
            device_id=device_id,
            event_type=event_type,
            payload=data,
        )

        if should_send:
            recipients = [
                uid for uid in get_notification_recipients_by_access(device_id, required_access_level)
                if uid != actor_user_id
            ]
            recipients = [
                uid for uid in recipients
                if should_user_receive_notification(uid, device_id, event_type)
            ]
            if recipients:
                create_notification(
                    device_id=device_id,
                    user_ids=recipients,
                    notif_type=event_type,
                    message=build_notification_message(event_type, data),
                    data=data,
                )
                write_audit(
                action="NOTIFICATION_SENT",
                actor_user_id=None,
                device_id=device_id,
                status="SUCCESS",
                details={
                "type": event_type,
                "userCount": len(recipients),
                    },
                )
        else:
            print(f"Notification not enabled for event type {event_type} on device {device_id}")

    elif msg_type == "keypad_verify":
        code = data.get("code")
        is_valid, matched_user_id, msg = verify_device_keypad_code(device_id, code)

        await websocket.send_json({
            "type": "keypad_verify_result",
            "deviceId": device_id,
            "ok": is_valid,
            "matchedUserId": matched_user_id,
            "message": msg,
        })

        write_audit(
            action="KEYPAD_VERIFY_ATTEMPT",
            actor_user_id=None,
            device_id=device_id,
            target_user_id=matched_user_id,
            status="SUCCESS" if is_valid else "FAILED",
            details={"source": "ws_device"},
        )
    elif msg_type == "fingerprint_enroll_result":
        enrollment_id = data.get("enrollmentId")
        success = bool(data.get("success"))
        sensor_template_id = data.get("sensorTemplateId")
        error = data.get("error")

        ok, msg, target_user_id, fingerprint_id = complete_fingerprint_enrollment(
            device_id=device_id,
            enrollment_id=enrollment_id,
            success=success,
            sensor_template_id=sensor_template_id,
            error=error,
        )

        if ok:
            write_audit(
                action="FINGERPRINT_ENROLL_COMPLETED",
                actor_user_id=None,
                target_user_id=target_user_id,
                device_id=device_id,
                status="SUCCESS" if success else "FAILED",
                details={
                    "source": "ws_device",
                    "enrollmentId": enrollment_id,
                    "fingerprintId": fingerprint_id,
                    "sensorTemplateId": sensor_template_id,
                    "error": error,
                },
            )
            await broadcast_status(device_id, {
                "type": "fingerprint_enroll_status",
                "deviceId": device_id,
                "enrollmentId": enrollment_id,
                "fingerprintId": fingerprint_id,
                "success": success,
                "sensorTemplateId": sensor_template_id,
                "error": error,
            })
        else:
            write_audit(
                action="FINGERPRINT_ENROLL_COMPLETED",
                actor_user_id=None,
                device_id=device_id,
                status="FAILED",
                details={
                    "source": "ws_device",
                    "enrollmentId": enrollment_id,
                    "error": msg,
                },
            )

 

async def handle_device_binary_message(device_id: str, binary: bytes):
    # keep an in-memory last frame for streaming
    last_frame_bytes[device_id] = binary
    last_frame_meta[device_id] = {
        "timestamp": asyncio.get_event_loop().time()
    }
    # store latest frame in Redis cache for fast retrieval by mobile clients
    try:
        set_latest_frame(device_id, binary, meta={"timestamp": last_frame_meta[device_id]["timestamp"]}, ttl=60)
    except Exception as e:
        print(f"Failed to write latest frame to Redis for {device_id}: {e}")

    # If there's a pending event waiting for media, persist it
    event_id = pending_media_event.get(device_id)
    if event_id:
        try:
            res = store_media_bytes(device_id=device_id, event_id=event_id, data=binary)
            write_audit(
                action="MEDIA_STORED",
                actor_user_id=None,
                device_id=device_id,
                status="SUCCESS",
                details={"mediaId": res.get("mediaId"), "eventId": event_id},
            )
        except Exception as e:
            write_audit(
                action="MEDIA_STORED",
                actor_user_id=None,
                device_id=device_id,
                status="FAILED",
                details={"error": str(e), "eventId": event_id},
            )
        finally:
            # clear pending marker after first fragment stored
            pending_media_event[device_id] = None

    # notify any waiting stream
    if device_id not in frame_events:
        frame_events[device_id] = asyncio.Event()

    frame_events[device_id].set()
    frame_events[device_id].clear()
