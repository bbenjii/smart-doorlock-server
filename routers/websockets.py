from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect


from ws.manager import handle_device_connection, handle_client_connection

router = APIRouter(prefix="/ws", tags=["device websocket"])


# ------------- WebSocket: ESP32 devices -------------
@router.websocket("/device")
async def device_ws(websocket: WebSocket):
    await websocket.accept()
    await handle_device_connection(websocket)


# ------------- WebSocket: Mobile clients -------------
@router.websocket("/client")
async def client_ws(websocket: WebSocket):
    await websocket.accept()
    await handle_client_connection(websocket)
