# server.py
import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Dict, Any, Set
from routers import auth, websockets, notifications, media
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from starlette.websockets import WebSocketState

from services.auth_service import has_access, claim_device
from services.event_service import ingest_event

from services.dev_state_service import (
    mark_device_online,
    mark_device_offline,
    persist_status_update,
    persist_command_result,
    get_last_seen,
)

from services.command_service import (
    create_command,
    deliver_command_with_retry,
    mark_command_acknowledged,
)

from typing import Optional
from services.event_service import query_events
from services.audit_service import write_audit
from ws.state import last_frame_bytes, frame_events, connected_devices, last_status
from services.cache_service import get_latest_frame

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(websockets.router)
app.include_router(media.router)


@app.get("/camera/{device_id}/snapshot")
async def get_snapshot(device_id: str):
    # try Redis cache first for low-latency retrieval
    try:
        frame, meta = get_latest_frame(device_id)
        if frame:
            return Response(content=frame, media_type="image/jpeg")
    except Exception:
        # fall back to in-memory cache
        pass

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
async def send_command(device_id: str, cmd: str, user_id: str = ""):
    cmd_upper = cmd.upper()

    if cmd_upper not in ("LOCK", "UNLOCK", "GET_STATUS"):
        return {"ok": False, "error": "Invalid command"}

    
    # if not has_access(user_id, device_id, cmd_upper):
    #     write_audit(
    #         action="COMMAND_DENIED",
    #         actor_user_id=user_id,
    #         device_id=device_id,
    #         status="DENIED",
    #         details={"command": cmd_upper},
    #     )
    #     raise HTTPException(status_code=403, detail="Access denied")
    # 
    write_audit(
        action="COMMAND_ISSUED",
        actor_user_id=user_id,
        device_id=device_id,
        status="SUCCESS",
        details={"command": cmd_upper},
    )

    ws = connected_devices.get(device_id)
    devices = connected_devices
    
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
