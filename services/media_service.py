from datetime import datetime
from typing import Optional, Dict, Any
import os
from uuid import uuid4
import io

from db import db

try:
    from PIL import Image
except Exception:
    Image = None

MEDIA_BASE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "media")

_MIME_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "video/mp4": "mp4",
}


def _ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def _guess_mime_and_ext(data: bytes) -> (str, str):
    if len(data) >= 4 and data[0:2] == b"\xff\xd8":
        return "image/jpeg", "jpg"
    if len(data) >= 8 and data[0:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"

    if b"ftyp" in data[4:12]:
        return "video/mp4", "mp4"
    return "application/octet-stream", "bin"


def _extract_image_size(data: bytes) -> Optional[Dict[str, int]]:
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
            return {"width": w, "height": h}
    except Exception:
        return None


def store_media_bytes(
    device_id: str,
    event_id: Optional[str],
    data: bytes,
    media_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Persist media blob to local storage and write a metadata record to Firestore.

    The stored metadata includes resolution (if available), size in bytes,
    and any contextual information from the linked event (eventType, userId, metadata).
    """
    if not device_id or not data:
        raise ValueError("device_id and data are required")

    media_id = str(uuid4())

    if not media_type:
        media_type, ext = _guess_mime_and_ext(data)
    else:
        ext = _MIME_TO_EXT.get(media_type, "bin")

    device_dir = os.path.join(MEDIA_BASE, device_id)
    _ensure_dir(device_dir)

    filename = f"{media_id}.{ext}"
    storage_path = os.path.join(device_dir, filename)

    # write file
    with open(storage_path, "wb") as f:
        f.write(data)

    now = datetime.utcnow()
    size_bytes = len(data)

    resolution = _extract_image_size(data)

    # attach event context if available
    event_type = None
    event_user = None
    event_metadata = None
    if event_id:
        try:
            ev_doc = db.collection("events").document(event_id).get()
            if ev_doc.exists:
                ev = ev_doc.to_dict() or {}
                event_type = ev.get("eventType")
                event_user = ev.get("userId")
                event_metadata = ev.get("metadata")
        except Exception:
            # continue without event enrichment
            pass

    doc = {
        "mediaId": media_id,
        "deviceId": device_id,
        "eventId": event_id,
        "mediaType": media_type,
        "storagePath": storage_path,
        "timestamp": now,
        "sizeBytes": size_bytes,
        "resolution": resolution,
        "eventType": event_type,
        "eventUserId": event_user,
        "eventMetadata": event_metadata,
    }

    # write metadata to Firestore
    db.collection("media").document(media_id).set(doc)

    return {"ok": True, "mediaId": media_id, "storagePath": storage_path, "timestamp": now, "sizeBytes": size_bytes, "resolution": resolution, "eventType": event_type, "eventUserId": event_user}
