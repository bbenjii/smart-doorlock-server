import hashlib
import jwt
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from db import db
from schemas import Filter
from services.users_service import get_user
from utils import _hash_password, verify_password

# JWT Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7

# In-memory token blacklist for logout; Format: {token: expiry_datetime}

_token_blacklist: Dict[str, datetime] = {}

ROLE_PERMISSIONS = {
    "owner": {
        "LOCK",
        "UNLOCK",
        "GET_STATUS",
        "UPDATE_SETTINGS",
        "MANAGE_USERS",
        "CLAIM_DEVICE",
    },
    "guest": {
        "LOCK",
        "UNLOCK",
        "GET_STATUS",
    },
}

# JWT TOKEN MANAGEMENT

def create_access_token(user_id: str, email: str) -> str:
    """
    Create a JWT access token.
    
    Args:
        user_id: User ID from database
        email: User email
        
    Returns:
        Encoded JWT token
    """
    payload = {
        "user_id": user_id,
        "email": email,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, email: str) -> str:
    """
    Create a JWT refresh token (longer expiry).
    
    Args:
        user_id: User ID from database
        email: User email
        
    Returns:
        Encoded JWT token
    """
    payload = {
        "user_id": user_id,
        "email": email,
        "type": "refresh",
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verify and decode a JWT token.
    
    Args:
        token: JWT token string
        
    Returns:
        Tuple of (is_valid: bool, payload: dict or None)
    """
    # Check if token is blacklisted
    if token in _token_blacklist:
        return False, None
    
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return True, payload
    except jwt.ExpiredSignatureError:
        return False, None
    except jwt.InvalidTokenError:
        return False, None


def refresh_access_token(refresh_token: str) -> tuple[bool, Optional[str]]:
    """
    Issue a new access token using a refresh token.
    
    Args:
        refresh_token: Valid refresh token
        
    Returns:
        Tuple of (success: bool, new_access_token: str or None)
    """
    valid, payload = verify_token(refresh_token)
    
    if not valid or payload.get("type") != "refresh":
        return False, None
    
    new_token = create_access_token(payload["user_id"], payload["email"])
    return True, new_token


def revoke_token(token: str) -> None:
    """
    Add token to blacklist (for logout).
    
    Args:
        token: Token to revoke
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        exp_time = datetime.fromtimestamp(payload["exp"])
        _token_blacklist[token] = exp_time
    except:
        pass

def authenticate_user(email: str, password: str):
    """
    Authenticate user with email and password.
    Returns JWT tokens if successful.
    
    Supports both bcrypt (new) and SHA-256 (legacy) password hashes for backwards compatibility.
    If user has SHA-256 hash, it will be automatically upgraded to bcrypt on next login.
    
    Args:
        email: User email
        password: Plain text password
        
    Returns:
        Tuple of (status_code, response_dict)
    """
    from utils import hash_password
    
    filters = [Filter(key="email", value=email, op="==")]
    user = get_user(filters)

    if not user:
        return 401, {"error": "Invalid email or password"}

    stored_password_hash = user.get("password", "")
    user_id = user.get("id", email)
    password_valid = False
    is_legacy_hash = False
    
    # Try bcrypt verification first (new standard)
    if stored_password_hash and "$2b$" in stored_password_hash:
        password_valid = verify_password(password, stored_password_hash)
    # Fall back to SHA-256 verification (legacy support)
    elif stored_password_hash:
        legacy_hash = _hash_password(password)
        password_valid = (legacy_hash == stored_password_hash)
        is_legacy_hash = True
    
    if not password_valid:
        return 401, {"error": "Invalid email or password"}

    # Upgrade legacy SHA-256 passwords to bcrypt
    if is_legacy_hash:
        try:
            new_hash = hash_password(password)
            db.collection("users").document(user_id).update({
                "password": new_hash
            })
        except Exception as e:
            # Log but don't fail - authentication already succeeded
            print(f"Failed to upgrade password hash for {email}: {str(e)}")
    
    # Generate tokens
    access_token = create_access_token(user_id, email)
    refresh_token = create_refresh_token(user_id, email)

    return 200, {
        "message": "success",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": user.get("email"),
            "firstName": user.get("firstName"),
            "lastName": user.get("lastName"),
            "deviceId": user.get("deviceId"),
        },
    }


def has_access(
    user_id: str,
    device_id: str,
    action: str,
    auth_method: Optional[str] = None,
) -> bool:
    """
    Central authorization check.
    """

    docs = (
        db.collection("accessControl")
        .where("userId", "==", user_id)
        .where("deviceId", "==", device_id)
        .where("enabled", "==", True)
        .limit(1)
        .stream()
    )

    record = next(docs, None)
    if not record:
        return False

    access = record.to_dict()
    now = datetime.utcnow()

    # Time validity
    if access.get("validFrom") and now < access["validFrom"]:
        return False
    if access.get("validUntil") and now > access["validUntil"]:
        return False

    role = access.get("accessLevel")
    if role not in ROLE_PERMISSIONS:
        return False

    if action not in ROLE_PERMISSIONS[role]:
        return False

    if auth_method:
        allowed = access.get("accessMethods", [])
        if auth_method not in allowed:
            return False

    return True

PAIRING_EXPIRY_MINUTES = 5


def store_pairing_code(device_id: str, pairing_code: str):
    """
    Called when ESP32 enters pairing mode.
    """
    db.collection("devicePairing").document(device_id).set({
        "pairingCode": pairing_code,
        "expiresAt": datetime.utcnow() + timedelta(minutes=PAIRING_EXPIRY_MINUTES),
    })


def claim_device(user_id: str, device_id: str, pairing_code: str):
    """
    Secure device claiming.
    """

    pairing_ref = db.collection("devicePairing").document(device_id).get()
    if not pairing_ref.exists:
        return False, "Pairing not active"

    pairing = pairing_ref.to_dict()

    if pairing["pairingCode"] != pairing_code:
        return False, "Invalid pairing code"

    if datetime.utcnow() > pairing["expiresAt"]:
        return False, "Pairing code expired"

    device_ref = db.collection("devices").document(device_id).get()
    if device_ref.exists and device_ref.to_dict().get("ownerId"):
        return False, "Device already claimed"

    # Assign owner
    db.collection("devices").document(device_id).set({
        "ownerId": user_id,
        "createdAt": datetime.utcnow(),
    }, merge=True)

    # Grant owner access
    db.collection("accessControl").add({
        "userId": user_id,
        "deviceId": device_id,
        "accessLevel": "owner",
        "accessMethods": ["face", "fingerprint", "keypad", "bluetooth"],
        "enabled": True,
        "createdAt": datetime.utcnow(),
    })

    # Cleanup pairing entry
    db.collection("devicePairing").document(device_id).delete()

    return True, "Device claimed"
