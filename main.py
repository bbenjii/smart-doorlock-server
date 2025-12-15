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
            await asyncio.sleep(5)  # check every 5 seconds
            # await send_command(device_id, "GET_STATUS")
            ws = connected_devices.get(device_id)
            if ws:
                await ws.send_text("GET_STATUS")
        
            now = datetime.now()
            
            last_timestamp = last_status[device_id].get("timestamp")
            last_timestamp = datetime.strptime(last_timestamp, "%d/%m/%Y, %H:%M:%S")
            difference = now - last_timestamp
            if difference.seconds > 10:
                print(f"[{device_id}] WebSocket is no longer connected (watchdog).")
                if device_id and connected_devices.get(device_id) is websocket:
                    del connected_devices[device_id]
                break
    except asyncio.CancelledError:
        # normal when we cancel the task on exit
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
                    now = datetime.now().strftime("%d/%m/%Y, %H:%M:%S")
                    data["timestamp"] = now
                    data["online"] = True
                    last_status[device_id] = data
                    
                    await broadcast_status(device_id, data)

                elif msg_type == "command_finished":
                    print(f"Command finished from {device_id}: {data}")
                    new_status = data.get("new_status")
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
async def send_command(device_id: str, cmd: str):
    ws = connected_devices.get(device_id)
    if not ws:
        return {"ok": False, "error": "Device not connected"}

    cmd_upper = cmd.upper()
    if cmd_upper not in ("LOCK", "UNLOCK", "GET_STATUS"):
        return {"ok": False, "error": "Invalid command"}

    await ws.send_text(cmd_upper if cmd_upper != "GET_STATUS" else "get_status")
    return {"ok": True, "command": cmd_upper}


# ------------- HTTP: Get last known status -------------

@app.get("/status/{device_id}")
async def get_status(device_id: str):
    data = last_status.get(device_id)
    print(connected_devices)
    if not data:
        return {"deviceId": device_id, "status": None, "online": device_id in connected_devices}
    return {**data, "online": device_id in connected_devices}


if __name__ == "__main__":
    uvicorn.run(app, host='0.0.0.0', port=8000)