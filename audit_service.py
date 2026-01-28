from datetime import datetime
from typing import Optional, Dict, Any
from db import db

def write_audit(
    action: str,
    actor_user_id: Optional[str],
    device_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    status: str = "SUCCESS",
    details: Optional[Dict[str, Any]] = None,
):
    #Records backend actions for traceability (who did what, when, result)
    db.collection("logs").add({
        "logType": "AUDIT",
        "action": action,
        "actorUserId": actor_user_id,
        "targetUserId": target_user_id,
        "deviceId": device_id,
        "status": status,
        "details": details or {},
        "timestamp": datetime.utcnow(),
    })
