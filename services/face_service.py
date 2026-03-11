import math
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from insightface.app import FaceAnalysis

from db import db
from services.settings_service import is_auth_method_enabled


EMBEDDING_DIM = 512
_FACE_APP: Optional[FaceAnalysis] = None


def _utcnow() -> datetime:
    return datetime.utcnow()


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _face_threshold() -> float:
    return _env_float("FACE_MATCH_THRESHOLD", 0.55)


def _enroll_min_frames() -> int:
    return max(1, _env_int("FACE_ENROLL_MIN_FRAMES", 5))


def _max_frame_bytes() -> int:
    return max(1024, _env_int("FACE_FRAME_MAX_SIZE_KB", 50) * 1024)


def _model_version() -> str:
    # Keep external override for compatibility, but prefer the more specific insightface model env var
    return os.getenv("FACE_MODEL_VERSION", "buffalo_sc")


def _insightface_model_name() -> str:
    return os.getenv("FACE_INSIGHTFACE_MODEL", "buffalo_sc")


def _insightface_det_size() -> Tuple[int, int]:
    size = max(80, _env_int("FACE_DET_SIZE", 160))
    return (size, size)


def _liveness_enabled() -> bool:
    return os.getenv("FACE_LIVENESS_ENABLED", "true").strip().lower() != "false"


def _vector_norm(vec: List[float]) -> float:
    return math.sqrt(sum(v * v for v in vec))


def _normalize(vec: List[float]) -> List[float]:
    norm = _vector_norm(vec)
    if norm <= 0:
        return vec
    return [v / norm for v in vec]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = _vector_norm(a)
    nb = _vector_norm(b)
    if na <= 0 or nb <= 0:
        return -1.0
    return dot / (na * nb)


def _load_face_app() -> FaceAnalysis:
    global _FACE_APP

    if _FACE_APP is not None:
        return _FACE_APP

    app = FaceAnalysis(name=_insightface_model_name(), providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=_insightface_det_size())
    _FACE_APP = app
    return _FACE_APP


def _decode_bgr(frame_bytes: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(frame_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img


def annotate_faces(frame_bytes: bytes) -> Tuple[bytes, Dict[str, Any]]:
    if not frame_bytes:
        return frame_bytes, {"faceCount": 0, "faces": []}

    img = _decode_bgr(frame_bytes)
    if img is None:
        return frame_bytes, {"faceCount": 0, "faces": []}

    try:
        faces = _load_face_app().get(img)
    except Exception:
        return frame_bytes, {"faceCount": 0, "faces": []}

    boxes: List[Dict[str, Any]] = []
    for face in faces:
        bbox = getattr(face, "bbox", None)
        if bbox is None or len(bbox) != 4:
            continue

        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(img.shape[1], x2)
        y2 = min(img.shape[0], y2)
        if x2 <= x1 or y2 <= y1:
            continue

        score = round(float(getattr(face, "det_score", 0.0) or 0.0), 4)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            img,
            f"{score:.2f}",
            (x1, max(18, y1 - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
        boxes.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "score": score})

    if not boxes:
        return frame_bytes, {"faceCount": 0, "faces": []}

    ok, encoded = cv2.imencode(".jpg", img)
    if not ok:
        return frame_bytes, {"faceCount": len(boxes), "faces": boxes}

    return encoded.tobytes(), {"faceCount": len(boxes), "faces": boxes}


def _pick_primary_face(faces: List[Any]) -> Optional[Any]:
    if not faces:
        return None

    def face_score(face: Any) -> float:
        det = float(getattr(face, "det_score", 0.0) or 0.0)
        bbox = getattr(face, "bbox", None)
        if bbox is None or len(bbox) != 4:
            return det
        w = max(0.0, float(bbox[2]) - float(bbox[0]))
        h = max(0.0, float(bbox[3]) - float(bbox[1]))
        area = w * h
        return det + (area / 100000.0)

    return max(faces, key=face_score)


def _face_quality(face: Any, img: np.ndarray) -> float:
    det_score = float(getattr(face, "det_score", 0.0) or 0.0)

    bbox = getattr(face, "bbox", None)
    if bbox is None or len(bbox) != 4:
        return round(min(1.0, max(0.0, det_score)), 4)

    x1, y1, x2, y2 = [int(v) for v in bbox]
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(img.shape[1], x2)
    y2 = min(img.shape[0], y2)

    if x2 <= x1 or y2 <= y1:
        return round(min(1.0, max(0.0, det_score)), 4)

    crop = img[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharpness_score = min(1.0, sharpness / 350.0)

    return round((0.65 * min(1.0, max(0.0, det_score))) + (0.35 * sharpness_score), 4)


def _extract_face_features(frame_bytes: bytes) -> Tuple[bool, Optional[List[float]], float, str]:
    if not frame_bytes:
        return False, None, 0.0, "empty_frame"

    if len(frame_bytes) > _max_frame_bytes():
        return False, None, 0.0, "frame_too_large"

    img = _decode_bgr(frame_bytes)
    if img is None:
        return False, None, 0.0, "decode_failed"

    try:
        app = _load_face_app()
        faces = app.get(img)
    except Exception as exc:
        return False, None, 0.0, f"face_inference_failed:{exc}"

    if not faces:
        return False, None, 0.0, "no_face_detected"

    face = _pick_primary_face(faces)
    if face is None:
        return False, None, 0.0, "no_face_detected"

    embedding = getattr(face, "normed_embedding", None)
    if embedding is None:
        embedding = getattr(face, "embedding", None)

    if embedding is None:
        return False, None, 0.0, "missing_embedding"

    vec = np.asarray(embedding, dtype=np.float32).reshape(-1)
    vec_list = _normalize([float(v) for v in vec.tolist()])
    if not vec_list:
        return False, None, 0.0, "empty_embedding"

    quality = _face_quality(face, img)
    if quality < 0.08:
        return False, None, quality, "low_quality"

    return True, vec_list, quality, "ok"


def _passes_liveness(frames: List[bytes]) -> bool:
    if not _liveness_enabled():
        return True
    if len(frames) < 2:
        return False

    embeddings: List[List[float]] = []
    for frame in frames:
        ok, emb, _q, _reason = _extract_face_features(frame)
        if ok and emb is not None:
            embeddings.append(emb)

    if len(embeddings) < 2:
        return False

    similarities = [
        _cosine_similarity(embeddings[idx - 1], embeddings[idx])
        for idx in range(1, len(embeddings))
    ]
    avg_sim = sum(similarities) / float(len(similarities))

    # Very high near-perfect similarity across all frames is unrealistically perfect
    return avg_sim < 0.9995


def start_face_enrollment(user_id: str, device_id: str, initiated_by: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    if not user_id:
        return False, "userId is required", None
    if not device_id:
        return False, "deviceId is required", None
    if not is_auth_method_enabled(device_id, "face"):
        return False, "Face unlock is disabled for this device", None

    try:
        _load_face_app()
    except Exception as exc:
        return False, f"Face model unavailable: {exc}", None

    now = _utcnow()
    session_id = f"fenr_{uuid.uuid4().hex[:16]}"

    db.collection("faceEnrollments").document(session_id).set(
        {
            "sessionId": session_id,
            "userId": user_id,
            "deviceId": device_id,
            "status": "in_progress",
            "acceptedFrames": 0,
            "rejectedFrames": 0,
            "qualitySum": 0.0,
            "embeddingSum": [0.0] * EMBEDDING_DIM,
            "embeddingDim": EMBEDDING_DIM,
            "minRequired": _enroll_min_frames(),
            "initiatedBy": initiated_by,
            "createdAt": now,
            "updatedAt": now,
        }
    )

    return True, "Face enrollment started", session_id


def add_face_enrollment_frame(session_id: str, frame_bytes: bytes) -> Dict[str, Any]:
    if not session_id:
        return {"ok": False, "message": "Missing sessionId", "accepted": False}

    session_ref = db.collection("faceEnrollments").document(session_id)
    session_doc = session_ref.get()
    if not session_doc.exists:
        return {"ok": False, "message": "Enrollment session not found", "accepted": False}

    session = session_doc.to_dict() or {}
    if session.get("status") != "in_progress":
        return {"ok": False, "message": "Enrollment session is not active", "accepted": False}

    ok, embedding, quality, reason = _extract_face_features(frame_bytes)
    now = _utcnow()

    accepted_frames = int(session.get("acceptedFrames", 0))
    rejected_frames = int(session.get("rejectedFrames", 0))
    quality_sum = float(session.get("qualitySum", 0.0))
    embedding_sum = list(session.get("embeddingSum", [0.0] * EMBEDDING_DIM))
    if len(embedding_sum) != EMBEDDING_DIM:
        embedding_sum = [0.0] * EMBEDDING_DIM

    if ok and embedding is not None:
        accepted_frames += 1
        quality_sum += quality
        embedding_sum = [a + b for a, b in zip(embedding_sum, embedding)]
        accepted = True
        message = "Frame accepted"
    else:
        rejected_frames += 1
        accepted = False
        message = f"Frame rejected: {reason}"

    session_ref.update(
        {
            "acceptedFrames": accepted_frames,
            "rejectedFrames": rejected_frames,
            "qualitySum": quality_sum,
            "embeddingSum": embedding_sum,
            "updatedAt": now,
        }
    )

    min_required = int(session.get("minRequired", _enroll_min_frames()))
    return {
        "ok": True,
        "message": message,
        "accepted": accepted,
        "quality": quality,
        "acceptedFrames": accepted_frames,
        "rejectedFrames": rejected_frames,
        "minRequired": min_required,
        "ready": accepted_frames >= min_required,
    }


def finalize_face_enrollment(session_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if not session_id:
        return False, "Missing sessionId", None

    session_ref = db.collection("faceEnrollments").document(session_id)
    session_doc = session_ref.get()
    if not session_doc.exists:
        return False, "Enrollment session not found", None

    session = session_doc.to_dict() or {}
    if session.get("status") != "in_progress":
        return False, "Enrollment session already finished", None

    accepted_frames = int(session.get("acceptedFrames", 0))
    min_required = int(session.get("minRequired", _enroll_min_frames()))
    if accepted_frames < min_required:
        return False, f"Need at least {min_required} accepted frames", None

    user_id = session.get("userId")
    device_id = session.get("deviceId")
    if not user_id or not device_id:
        return False, "Enrollment session is invalid", None

    embedding_sum = list(session.get("embeddingSum", [0.0] * EMBEDDING_DIM))
    if len(embedding_sum) != EMBEDDING_DIM:
        return False, "Enrollment data invalid", None

    embedding = [v / float(accepted_frames) for v in embedding_sum]
    embedding = _normalize(embedding)

    quality_score = round(float(session.get("qualitySum", 0.0)) / float(accepted_frames), 4)
    now = _utcnow()

    cred_ref = db.collection("authCredentials").document(user_id)
    cred_doc = cred_ref.get()

    face_payload = {
        "isActive": True,
        "enabled": True,
        "enrolledAt": now,
        "embedding": embedding,
        "embeddingVersion": _model_version(),
        "qualityScore": quality_score,
        "deviceId": device_id,
        "lastUsedAt": None,
        "revokedAt": None,
    }

    if cred_doc.exists:
        cred_ref.update(
            {
                "authMethods.face": face_payload,
                "isActive": True,
                "updatedAt": now,
            }
        )
    else:
        cred_ref.set(
            {
                "userId": user_id,
                "authMethods": {"face": face_payload},
                "isActive": True,
                "createdAt": now,
                "updatedAt": now,
            }
        )

    session_ref.update(
        {
            "status": "completed",
            "qualityScore": quality_score,
            "completedAt": now,
            "updatedAt": now,
        }
    )

    return (
        True,
        "Face enrollment completed",
        {
            "sessionId": session_id,
            "userId": user_id,
            "deviceId": device_id,
            "qualityScore": quality_score,
            "acceptedFrames": accepted_frames,
        },
    )


def revoke_face_credential(user_id: str, device_id: str) -> Tuple[bool, str]:
    if not user_id:
        return False, "userId is required"

    cred_ref = db.collection("authCredentials").document(user_id)
    cred_doc = cred_ref.get()
    if not cred_doc.exists:
        return False, "No credentials found for user"

    data = cred_doc.to_dict() or {}
    face = ((data.get("authMethods") or {}).get("face") or {})
    if not face:
        return False, "No face credential found"

    enrolled_device_id = face.get("deviceId")
    if device_id and enrolled_device_id and enrolled_device_id != device_id:
        return False, "Face credential belongs to another device"

    now = _utcnow()
    cred_ref.update(
        {
            "authMethods.face.isActive": False,
            "authMethods.face.enabled": False,
            "authMethods.face.revokedAt": now,
            "updatedAt": now,
        }
    )
    return True, "Face credential revoked"


def _candidate_user_ids_for_device(device_id: str) -> List[str]:
    access_docs = list(
        db.collection("accessControl")
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .stream()
    )

    user_ids: List[str] = []
    for access_doc in access_docs:
        access = access_doc.to_dict() or {}
        user_id = access.get("userId")
        if not user_id:
            continue

        methods = access.get("accessMethods", None)
        if methods is None:
            user_ids.append(user_id)
            continue

        method_set = set(methods)
        if not method_set or "face" in method_set:
            user_ids.append(user_id)

    if user_ids:
        return user_ids

    owner_docs = list(
        db.collection("users")
        .where("deviceId", "==", device_id)
        .limit(1)
        .stream()
    )
    if owner_docs:
        return [owner_docs[0].id]
    return []


def _get_active_face_entry(cred: Dict[str, Any], device_id: str) -> Optional[Dict[str, Any]]:
    if not cred.get("isActive", False):
        return None

    face = ((cred.get("authMethods") or {}).get("face") or {})
    if not face:
        return None

    is_active = bool(face.get("isActive", False))
    enabled = face.get("enabled", True)
    if not is_active or enabled is False:
        return None

    face_device_id = face.get("deviceId")
    if face_device_id and face_device_id != device_id:
        return None

    embedding = face.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        return None

    try:
        embedding = [float(v) for v in embedding]
    except Exception:
        return None

    return {
        "embedding": embedding,
        "embeddingVersion": face.get("embeddingVersion"),
        "qualityScore": face.get("qualityScore"),
    }


def verify_face(device_id: str, frames: List[bytes]) -> Dict[str, Any]:
    started = time.perf_counter()

    if not is_auth_method_enabled(device_id, "face"):
        return {
            "matched": False,
            "matchedUserId": None,
            "score": 0.0,
            "threshold": _face_threshold(),
            "livenessPassed": False,
            "action": "DENY",
            "reason": "method_disabled",
            "processingMs": int((time.perf_counter() - started) * 1000),
            "frameCount": len([f for f in frames if f]),
        }

    valid_frames = [f for f in frames if f]
    if not valid_frames:
        return {
            "matched": False,
            "matchedUserId": None,
            "score": 0.0,
            "threshold": _face_threshold(),
            "livenessPassed": False,
            "action": "DENY",
            "reason": "no_frames",
            "processingMs": int((time.perf_counter() - started) * 1000),
            "frameCount": 0,
        }

    embeddings: List[Tuple[List[float], float]] = []
    for frame in valid_frames:
        ok, embedding, quality, _reason = _extract_face_features(frame)
        if ok and embedding is not None:
            embeddings.append((embedding, quality))

    if not embeddings:
        return {
            "matched": False,
            "matchedUserId": None,
            "score": 0.0,
            "threshold": _face_threshold(),
            "livenessPassed": False,
            "action": "DENY",
            "reason": "no_face_detected",
            "processingMs": int((time.perf_counter() - started) * 1000),
            "frameCount": len(valid_frames),
        }

    best_embedding, _best_quality = max(embeddings, key=lambda item: item[1])

    candidate_user_ids = _candidate_user_ids_for_device(device_id)
    if not candidate_user_ids:
        return {
            "matched": False,
            "matchedUserId": None,
            "score": 0.0,
            "threshold": _face_threshold(),
            "livenessPassed": False,
            "action": "DENY",
            "reason": "no_candidates",
            "processingMs": int((time.perf_counter() - started) * 1000),
            "frameCount": len(valid_frames),
        }

    best_user_id: Optional[str] = None
    best_score = -1.0

    for user_id in candidate_user_ids:
        cred_doc = db.collection("authCredentials").document(user_id).get()
        if not cred_doc.exists:
            continue

        cred = cred_doc.to_dict() or {}
        face_entry = _get_active_face_entry(cred, device_id)
        if not face_entry:
            continue

        score = _cosine_similarity(best_embedding, face_entry["embedding"])
        if score > best_score:
            best_score = score
            best_user_id = user_id

    threshold = _face_threshold()
    liveness_passed = _passes_liveness(valid_frames)
    matched = bool(best_user_id) and best_score >= threshold and liveness_passed

    reason = "match" if matched else "below_threshold"
    if not liveness_passed:
        reason = "liveness_fail"
    elif best_user_id is None:
        reason = "no_candidates"

    if matched and best_user_id:
        now = _utcnow()
        db.collection("authCredentials").document(best_user_id).update(
            {
                "authMethods.face.lastUsedAt": now,
                "updatedAt": now,
            }
        )

    return {
        "matched": matched,
        "matchedUserId": best_user_id if matched else None,
        "score": round(max(best_score, 0.0), 5),
        "threshold": threshold,
        "livenessPassed": liveness_passed,
        "action": "UNLOCK" if matched else "DENY",
        "reason": reason,
        "processingMs": int((time.perf_counter() - started) * 1000),
        "frameCount": len(valid_frames),
    }


def log_face_auth_attempt(
    *,
    device_id: str,
    matched_user_id: Optional[str],
    score: float,
    threshold: float,
    liveness_passed: bool,
    result: str,
    reason: str,
    processing_ms: int,
    frame_count: int,
) -> None:
    db.collection("faceAuthAttempts").add(
        {
            "deviceId": device_id,
            "matchedUserId": matched_user_id,
            "score": score,
            "threshold": threshold,
            "livenessPassed": liveness_passed,
            "result": result,
            "reason": reason,
            "processingMs": processing_ms,
            "frameCount": frame_count,
            "createdAt": _utcnow(),
        }
    )


def list_device_face_enrollments(device_id: str) -> List[Dict[str, Any]]:
    users = _candidate_user_ids_for_device(device_id)
    result: List[Dict[str, Any]] = []
    for user_id in users:
        cred_doc = db.collection("authCredentials").document(user_id).get()
        if not cred_doc.exists:
            continue

        cred = cred_doc.to_dict() or {}
        face = ((cred.get("authMethods") or {}).get("face") or {})
        if not face:
            continue

        enrolled_device_id = face.get("deviceId")
        if enrolled_device_id and enrolled_device_id != device_id:
            continue

        enrolled_at = face.get("enrolledAt")
        last_used_at = face.get("lastUsedAt")
        revoked_at = face.get("revokedAt")

        result.append(
            {
                "userId": user_id,
                "isActive": bool(face.get("isActive", False)),
                "enabled": bool(face.get("enabled", True)),
                "embeddingVersion": face.get("embeddingVersion"),
                "qualityScore": face.get("qualityScore"),
                "enrolledAt": enrolled_at.isoformat() if hasattr(enrolled_at, "isoformat") else str(enrolled_at) if enrolled_at else None,
                "lastUsedAt": last_used_at.isoformat() if hasattr(last_used_at, "isoformat") else None,
                "revokedAt": revoked_at.isoformat() if hasattr(revoked_at, "isoformat") else None,
            }
        )

    result.sort(key=lambda item: item.get("enrolledAt") or "")
    return result


def get_face_enrollment_session(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    doc = db.collection("faceEnrollments").document(session_id).get()
    if not doc.exists:
        return None
    return doc.to_dict() or {}
