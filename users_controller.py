import hashlib

import firebase_admin
from firebase_admin import firestore
from firebase_admin import credentials
from google.cloud.firestore_v1 import FieldFilter
from datetime import datetime
cred = credentials.Certificate("smart-doorlock-project-firebase-adminsdk-fbsvc-035584259e.json")
app = firebase_admin.initialize_app(cred)

# Application Default credentials are automatically created.
db = firestore.client()

def _hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def authenticate_user(email: str, password: str):
    password = _hash_password(password)
    docs = (
        db.collection("users")
        .where(filter=FieldFilter("email", "==", email))
        .where(filter=FieldFilter("password", "==", password))
        .stream()
    )

    users = [doc.to_dict() for doc in docs]
    if not users:
        return 401, {"error": "User not found"}

    user = users[0]
    return 200, {
        "message": "success",
        "user": {
            "email": user.get("email"),
            "firstName": user.get("firstName"),
            "lastName": user.get("lastName"),
            "deviceId": user.get("deviceId"),
        }}

def create_user(user_data):
    # make sure user is unique
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
    