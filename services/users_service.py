import hashlib
import re
from typing import Any, List, Literal, Tuple, Optional
from google.cloud.firestore_v1 import FieldFilter
from datetime import datetime
from db import db
from schemas import Filter
from utils import _hash_password, hash_password, validate_password_strength

def validate_email(email: str) -> Tuple[bool, str]:
    """
    Args:
        email: Email to validate
        
    Returns:
        Tuple of (is_valid: bool, message: str)
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if re.match(pattern, email):
        return True, "Valid email"
    return False, "Invalid email format"

from utils import _hash_password
from utils import _hash_password

def get_user(filters: List[Filter]):
    """Get user by filters (email, id, etc)"""
    query = db.collection("users")
    for f in filters:
        query = query.where(filter=FieldFilter(f.key, f.op, f.value))
    docs = query.limit(1).get()

    if not docs:
        return None

    user = docs[0].to_dict()
    user["id"] = docs[0].id  # Include document ID
    
    return user


def create_user(user_data):
    """
    Use register_user() for new user registrations.

    """
    email = user_data.get("email")
    docs = (
        db.collection("users")
        .where(filter=FieldFilter("email", "==", email))
        .stream()
    )
    users = [doc.to_dict() for doc in docs]

    if users:
        return 400, {"error": "User already exists"}

    plain_password = user_data.get("password")
    user_data["password"] = _hash_password(plain_password)

    now = datetime.now()
    user_data["created_at"] = now

    if user_data.get("firstName") in [None, ""] or user_data.get("lastName") in [None, ""]:
        return 400, {"error": "Missing first name or last name"}

    update_time, user = db.collection("users").add(user_data)
    return 200, {
        "message": "success",
        "user": {
            "email": user_data.get("email"),
            "firstName": user_data.get("firstName"),
            "lastName": user_data.get("lastName"),
            "deviceId": user_data.get("deviceId"),
        }}


# NEW SECURE REGISTRATION

def register_user(email: str, password: str, password_confirm: str, 
                  first_name: str, last_name: str) -> Tuple[int, dict]:
    """
    
    Args:
        email: User email
        password: Plain text password
        password_confirm: Password confirmation (must match)
        first_name: User's first name
        last_name: User's last name
        
    Returns:
        Tuple of (status_code, response_dict)
    """
    email_valid, email_msg = validate_email(email)
    if not email_valid:
        return 400, {"error": f"Invalid email: {email_msg}"}
    
    existing_user = get_user([Filter(key="email", value=email, op="==")])
    if existing_user:
        return 400, {"error": "Email already registered"}
    
    if password != password_confirm:
        return 400, {"error": "Passwords do not match"}
    
    is_strong, strength_msg = validate_password_strength(password)
    if not is_strong:
        return 400, {"error": f"Password too weak: {strength_msg}"}
    
    first_name = first_name.strip() if first_name else ""
    last_name = last_name.strip() if last_name else ""
    
    if not first_name or not last_name:
        return 400, {"error": "First name and last name are required"}
    
    hashed_password = hash_password(password)
    
    user_data = {
        "email": email,
        "password": hashed_password,
        "firstName": first_name,
        "lastName": last_name,
        "created_at": datetime.utcnow(),
        "verified": False,  
    }
    
    try:
        doc_ref = db.collection("users").add(user_data)
        user_id = doc_ref[1].id
        
        return 201, {
            "message": "User registered successfully",
            "user": {
                "id": user_id,
                "email": email,
                "firstName": first_name,
                "lastName": last_name,
            },
        }
    except Exception as e:
        return 500, {"error": f"Failed to create user: {str(e)}"}

