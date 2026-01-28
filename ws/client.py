import json
from fastapi import WebSocket, WebSocketDisconnect

from ws.state import device_subscribers, last_status


async def handle_client_connection(websocket: WebSocket):
    device_id = None
    try:
        # First message should be a subscribe:
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
