from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from db import db
from routers.auth import get_current_user
import os

router = APIRouter(prefix="/media", tags=["media"])

@router.get("/{media_id}")
async def get_media_meta(media_id: str, source: str = "auto", current_user: dict = Depends(get_current_user)):

    doc = db.collection("media").document(media_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Media not found")

    data = doc.to_dict() or {}

    storage_path = data.get("storagePath")
    cloud_url = data.get("cloudUrl")

    if source == "local":
        if storage_path and os.path.exists(storage_path):
            return {
                "ok": True,
                "media": data,
                "downloadUrl": f"/media/{media_id}/download",
                "source": "local",
            }
        raise HTTPException(status_code=404, detail="Local file not available")

    if source == "cloud":
        if cloud_url:
            return {
                "ok": True,
                "media": data,
                "downloadUrl": cloud_url,
                "source": "cloud",
            }
        raise HTTPException(status_code=404, detail="Cloud file not available")

    if storage_path and os.path.exists(storage_path):
        return {
            "ok": True,
            "media": data,
            "downloadUrl": f"/media/{media_id}/download",
            "source": "local",
        }

    if cloud_url:
        return {
            "ok": True,
            "media": data,
            "downloadUrl": cloud_url,
            "source": "cloud",
        }

    raise HTTPException(status_code=404, detail="Media file not available")
