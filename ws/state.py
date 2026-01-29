from typing import Dict, Any, Set
from collections import defaultdict
import asyncio
from fastapi import WebSocket

# deviceId -> WebSocket (ESP connections)
connected_devices: Dict[str, WebSocket] = {}

# deviceId -> set of WebSockets (mobile clients)
device_subscribers: Dict[str, Set[WebSocket]] = defaultdict(set)

# deviceId -> last status payload
last_status: Dict[str, Dict[str, Any]] = {}

last_frame_bytes: Dict[str, bytes] = {}
last_frame_meta: Dict[str, Any] = {}
frame_events: Dict[str, asyncio.Event] = {}
