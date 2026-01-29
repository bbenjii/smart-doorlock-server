from fastapi import APIRouter, Depends, HTTPException
from services.auth_service import authenticate_user
from services.users_service import create_user
from schemas import Credentials

router = APIRouter(prefix="/auth", tags=["users"])

# -------------- USER AUTHENTICATION -----------------
@router.post("/login")
async def login(credentials: dict):
    status_code, result = authenticate_user(email=credentials.get("email"), password=credentials.get("password"))
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)
    return result

@router.post("/signup")
async def signup(user_data: dict):
    status_code, result = create_user(user_data)

    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)

    return result
