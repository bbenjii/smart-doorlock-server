# server.py
import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, Set

import uvicorn
from fastapi import FastAPI, Body, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.responses import Response, StreamingResponse
from starlette.websockets import WebSocketState

from users_controller import authenticate_user, create_user
from auth_service import has_access, claim_device
from event_service import ingest_event

from dev_state_service import (
    mark_device_online,
    mark_device_offline,
    persist_status_update,
    persist_command_result,
    get_last_seen,
)

from command_service import (
    create_command,
    deliver_command_with_retry,
    mark_command_acknowledged,
)

from fastapi import Query
from typing import Optional
from event_service import query_events
from audit_service import write_audit

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# deviceId -> WebSocket (ESP connections)
connected_devices: Dict[str, WebSocket] = {}

# deviceId -> set of WebSockets (mobile clients)
device_subscribers: Dict[str, Set[WebSocket]] = defaultdict(set)

# deviceId -> last status payload
last_status: Dict[str, Dict[str, Any]] = {}

last_frame_bytes: Dict[str, bytes] = {}
last_frame_meta: Dict[str, Any] = {}
frame_events: Dict[str, asyncio.Event] = {}

# ------------- Utils -------------

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



class Credentials(BaseModel):
    username: str
    password: str
# -------------- USER AUTHENTICATION -----------------
@app.post("/auth/login")
async def login(credentials: dict):
    status_code, result = authenticate_user(email=credentials.get("email"), password=credentials.get("password"))
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)
    return result

@app.post("/auth/signup")
async def signup(user_data: dict):
    status_code, result = create_user(user_data)
    
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)
    
    return result


# ------------- WebSocket: ESP32 devices -------------

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

            if (datetime.utcnow() - last_seen).seconds > 10:
                print(f"[{device_id}] Device considered offline (watchdog)")
                break

    except asyncio.CancelledError:
        pass
    
@app.websocket("/ws/device")
async def device_ws(websocket: WebSocket):
    await websocket.accept()
    print("ESP connected, waiting for hello...")

    device_id = None
    watchdog = None
    try:
        # First message must still be the JSON "hello"
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
            if not (websocket.client_state == WebSocketState.CONNECTED and websocket.application_state == WebSocketState.CONNECTED):
                print("WebSocket is already closed.")
                
            message = await websocket.receive()
            
            # Client closed
            if message["type"] == "websocket.disconnect":
                break

            text = message.get("text")
            binary = message.get("bytes")

            if text is not None:
                # handle JSON messages exactly like before
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
                    ingest_event(
                        device_id=device_id,
                        event_type=data.get("eventType"),
                        user_id=data.get("userId"),
                        auth_method=data.get("authMethod"),
                    )
                else:
                    print(f"Unknown/unused message from {device_id}: {text}")

            elif binary is not None:
                # binary frame from ESP32 camera
                # print(f"[{device_id}] received frame, size={len(binary)} bytes")

                last_frame_bytes[device_id] = binary
                last_frame_meta[device_id] = {
                    "timestamp": asyncio.get_event_loop().time(),
                }

                # notify any waiting stream
                if device_id not in frame_events:
                    frame_events[device_id] = asyncio.Event()
                frame_events[device_id].set()
                frame_events[device_id].clear()

    except WebSocketDisconnect:
        print(f"Device disconnected: {device_id}")
    except Exception as e:
        print(f"Device WS error for {device_id}: {e}")
    finally:
        if device_id and connected_devices.get(device_id) is websocket:
            del connected_devices[device_id]

        if device_id:
            mark_device_offline(device_id)

        watchdog.cancel()
        print(f"Device unregistered: {device_id}")

        

# ------------- WebSocket: Mobile clients -------------

@app.websocket("/ws/client")
async def client_ws(websocket: WebSocket):
    await websocket.accept()
    device_id = None
    try:
        # First message should be a subscribe:
        # {"type":"subscribe","deviceId":"smartlock_xxx"}
        sub_msg = await websocket.receive_text()
        try:
            sub_json = json.loads(sub_msg)
        except json.JSONDecodeError:
            await websocket.close()
            return

        if sub_json.get("type") != "subscribe":
            await websocket.close()
            return

        device_id = sub_json.get("deviceId")
        if not device_id:
            await websocket.close()
            return

        device_subscribers[device_id].add(websocket)
        print(f"Client subscribed to {device_id}, total subscribers: {len(device_subscribers[device_id])}")

        # Immediately send last status if available
        if device_id in last_status:
            await websocket.send_json(last_status[device_id])

        # Keep the connection alive; we don't expect many messages from client
        while True:
            # You can handle pings or client messages here if needed
            msg = await websocket.receive_text()
            print(f"[client:{device_id}] -> {msg}")

           
    except WebSocketDisconnect:
        print(f"Client disconnected from {device_id}")
    except Exception as e:
        print(f"Client WS error ({device_id}): {e}")
    finally:
        print(f"________-Client unsubscribed from {device_id}")
        if device_id and websocket in device_subscribers.get(device_id, set()):
            device_subscribers[device_id].discard(websocket)


@app.get("/camera/{device_id}/snapshot")
async def get_snapshot(device_id: str):
    frame = last_frame_bytes.get(device_id)
    if not frame:
        raise HTTPException(status_code=404, detail="No frame for this device")
    return Response(content=frame, media_type="image/jpeg")

BOUNDARY = "frameboundary123456"

@app.get("/camera/{device_id}/stream")
async def mjpeg_stream(device_id: str):
    if device_id not in frame_events:
        frame_events[device_id] = asyncio.Event()

    async def frame_generator():
        while True:
            # wait for next frame from ESP
            await frame_events[device_id].wait()
            frame = last_frame_bytes.get(device_id)
            if not frame:
                continue

            header = (
                f"--{BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(frame)}\r\n\r\n"
            ).encode("latin1")

            yield header
            yield frame
            yield b"\r\n"

    return StreamingResponse(
        frame_generator(),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
    )



# ------------- HTTP: Send command from mobile -> ESP -------------

@app.post("/send-command/{device_id}/{cmd}")
async def send_command(device_id: str, cmd: str, user_id: str):
    cmd_upper = cmd.upper()

    if cmd_upper not in ("LOCK", "UNLOCK", "GET_STATUS"):
        return {"ok": False, "error": "Invalid command"}

    
    if not has_access(user_id, device_id, cmd_upper):
        write_audit(
            action="COMMAND_DENIED",
            actor_user_id=user_id,
            device_id=device_id,
            status="DENIED",
            details={"command": cmd_upper},
        )
        raise HTTPException(status_code=403, detail="Access denied")
    
    write_audit(
        action="COMMAND_ISSUED",
        actor_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"command": cmd_upper},
    )

    ws = connected_devices.get(device_id)
    if not ws:
        return {"ok": False, "error": "Device not connected"}

    command_id = create_command(device_id, user_id, cmd_upper)

    asyncio.create_task(
    deliver_command_with_retry(
        command_id,
        device_id,
        cmd_upper,
        connected_devices,
        )
    )   

    return {"ok": True, "commandId": command_id}


# ------------- HTTP: Claim device ---------------------

@app.post("/devices/{device_id}/claim")
async def claim(device_id: str, body: dict):
    user_id = body.get("userId")
    pairing_code = body.get("pairingCode")

    if not user_id or not pairing_code:
        raise HTTPException(status_code=400, detail="Missing userId or pairingCode")

    ok, msg = claim_device(user_id, device_id, pairing_code)

    if not ok:
        write_audit(
            action="CLAIM_DEVICE",
            actor_user_id=user_id,
            device_id=device_id,
            status="FAILED",
            details={"reason": msg},
        )
        raise HTTPException(status_code=400, detail=msg)
    write_audit(
        action="CLAIM_DEVICE",
        actor_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
    )

    return {"ok": True, "message": msg}

# ------------- HTTP: Get last known status -------------

@app.get("/status/{device_id}")
async def get_status(device_id: str):
    data = last_status.get(device_id)
    print(connected_devices)
    if not data:
        return {"deviceId": device_id, "status": None, "online": device_id in connected_devices}
    return {**data, "online": device_id in connected_devices}

# ------------- HTTP: Query events -------------

@app.get("/devices/{device_id}/events")
async def get_device_events(
    device_id: str,
    requester_id: str,  
    user_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor_ts: Optional[str] = Query(default=None),
):

    if not has_access(requester_id, device_id, "GET_STATUS"):
        raise HTTPException(status_code=403, detail="Access denied")

    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    cursor_dt = datetime.fromisoformat(cursor_ts) if cursor_ts else None

    return query_events(
        device_id=device_id,
        user_id=user_id,
        event_type=event_type,
        start=start_dt,
        end=end_dt,
        limit=limit,
        cursor_ts=cursor_dt,
    )

# ------------- HTTP: Query events by user -------------
@app.get("/users/{user_id}/events")
async def get_user_events(
    user_id: str,              # target user
    requester_id: str,         # who is querying
    device_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor_ts: Optional[str] = Query(default=None),
):
    # Users can see their own events
    if requester_id != user_id:
        # Otherwise must be owner of the device
        if not device_id or not has_access(requester_id, device_id, "MANAGE_USERS"):
            raise HTTPException(status_code=403, detail="Access denied")

    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None
    cursor_dt = datetime.fromisoformat(cursor_ts) if cursor_ts else None

    return query_events(
        device_id=device_id,
        user_id=user_id,
        event_type=event_type,
        start=start_dt,
        end=end_dt,
        limit=limit,
        cursor_ts=cursor_dt,
    )


if __name__ == "__main__":
    uvicorn.run(app, host='0.0.0.0', port=8000)