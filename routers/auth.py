from fastapi import APIRouter, Depends, HTTPException
from services.users_service import authenticate_user, create_user
from pydantic import BaseModel

router = APIRouter(prefix="/auth", tags=["users"])


class Credentials(BaseModel):
    username: str
    password: str


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
