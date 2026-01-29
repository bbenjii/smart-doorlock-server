import hashlib
from typing import Any, List, Literal
from google.cloud.firestore_v1 import FieldFilter
from datetime import datetime
from db import db
from schemas import Filter
from utils import _hash_password

def get_user(filters: List[Filter]):
    query = db.collection("users")
    for f in filters:
        query = query.where(filter=FieldFilter(f.key, f.op, f.value))
    docs = query.limit(1).get()

    if not docs:
        return None

    user = docs[0].to_dict()
    
    return user

def create_user(user_data):
    # make sure the user is unique
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

    # verify important fields
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
