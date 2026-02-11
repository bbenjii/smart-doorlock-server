import os
import json
from typing import Optional, Dict, Any, Tuple

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_r = redis.from_url(REDIS_URL)


def set_latest_frame(device_id: str, data: bytes, meta: Optional[Dict[str, Any]] = None, ttl: int = 60):
    """Store latest frame bytes and optional metadata in Redis with TTL (seconds)."""
    if not device_id or data is None:
        raise ValueError("device_id and data are required")

    key = f"latest_frame:{device_id}"
    meta_key = f"latest_frame_meta:{device_id}"

    # store raw bytes
    _r.set(key, data, ex=ttl)

    # store meta as JSON fields in a hash
    if meta:
        mapping = {}
        for k, v in meta.items():
            try:
                mapping[k] = json.dumps(v)
            except Exception:
                mapping[k] = json.dumps(str(v))
        if mapping:
            _r.hset(meta_key, mapping=mapping)
            _r.expire(meta_key, ttl)


def get_latest_frame(device_id: str) -> Tuple[Optional[bytes], Dict[str, Any]]:
    """Return (bytes, meta) from Redis. Meta values are JSON-decoded where possible"""
    key = f"latest_frame:{device_id}"
    meta_key = f"latest_frame_meta:{device_id}"

    data = _r.get(key)
    meta_raw = _r.hgetall(meta_key) or {}
    meta: Dict[str, Any] = {}
    for k, v in meta_raw.items():
        try:
            # redis returns bytes for keys/values
            k_str = k.decode() if isinstance(k, bytes) else str(k)
            v_str = v.decode() if isinstance(v, bytes) else str(v)
            meta[k_str] = json.loads(v_str)
        except Exception:
            meta[k_str] = v_str

    return data, meta
