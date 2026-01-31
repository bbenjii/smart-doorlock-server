from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class Credentials(BaseModel):
    username: str
    password: str

# REGISTRATION

class RegisterRequest(BaseModel):

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, description="Password (8+ chars, uppercase, lowercase, digit, special)")
    password_confirm: str = Field(..., description="Password confirmation (must match password)")
    first_name: str = Field(..., min_length=1, description="User's first name")
    last_name: str = Field(..., min_length=1, description="User's last name")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "john.doe@example.com",
                "password": "SecurePass123!",
                "password_confirm": "SecurePass123!",
                "first_name": "John",
                "last_name": "Doe"
            }
        }

class RegisterResponse(BaseModel):

    message: str
    user: dict = Field(..., description="User information")


# LOGIN

class LoginRequest(BaseModel):

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., description="User password")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "john.doe@example.com",
                "password": "SecurePass123!"
            }
        }


class TokenResponse(BaseModel):

    message: str
    access_token: str = Field(..., description="JWT access token (valid 15 minutes)")
    refresh_token: str = Field(..., description="JWT refresh token (valid 7 days)")
    token_type: str = Field(default="bearer")
    user: dict = Field(..., description="User information")


class RefreshTokenRequest(BaseModel):

    refresh_token: str = Field(..., description="Valid refresh token")

    class Config:
        json_schema_extra = {
            "example": {
                "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            }
        }

class RefreshTokenResponse(BaseModel):

    message: str
    access_token: str = Field(..., description="New JWT access token")
    token_type: str = Field(default="bearer")

class LogoutRequest(BaseModel):

    access_token: str = Field(..., description="Access token to revoke")

    class Config:
        json_schema_extra = {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
            }
        }

class LogoutResponse(BaseModel):

    message: str
