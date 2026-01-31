from fastapi import APIRouter, Depends, HTTPException, status, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from services.auth_service import (
    authenticate_user,
    verify_token,
    refresh_access_token,
    revoke_token,
)
from services.users_service import create_user, register_user
from schemas.credentials import (
    Credentials,
    RegisterRequest,
    RegisterResponse,
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    LogoutRequest,
    LogoutResponse,
)

router = APIRouter(prefix="/auth", tags=["users"])

# Rate limiter instance for auth endpoints
limiter = Limiter(key_func=get_remote_address)


# TOKEN AUTHENTICATION

# EXTRACT AND VERIFY TOKEN FROM AUTHORIZATION HEADER
# Expected format: "Bearer <token>
def get_current_user(authorization: str = None):

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        scheme, token = authorization.split(" ")
        if scheme.lower() != "bearer":
            raise ValueError("Invalid authentication scheme")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )

    is_valid, payload = verify_token(token)
    if not is_valid or not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload

# Use POST /auth/login with LoginRequest schema instead
@router.post("/login", deprecated=True)
async def login(credentials: dict):

    status_code, result = authenticate_user(
        email=credentials.get("email"), password=credentials.get("password")
    )
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)
    return result

# Use POST /auth/register with RegisterRequest schema instead
@router.post("/signup", deprecated=True)
async def signup(user_data: dict):

    status_code, result = create_user(user_data)
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)
    return result


# SECURE AUTHENTICATION ENDPOINTS

@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(request: Request, reg_request: RegisterRequest) -> RegisterResponse:

    status_code, result = register_user(
        email=reg_request.email,
        password=reg_request.password,
        password_confirm=reg_request.password_confirm,
        first_name=reg_request.first_name,
        last_name=reg_request.last_name,
    )

    if status_code != 201:
        raise HTTPException(status_code=status_code, detail=result)

    return result


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("10/minute")
async def login_secure(request: Request, login_request: LoginRequest) -> TokenResponse:
    """
    Authenticate user with email and password.
    
    Rate limited:** 10 requests per minute per IP address
    
    Returns JWT access token (15 min validity) and refresh token (7 days).
    Include access token in subsequent requests: Authorization: Bearer <token>
    """
    status_code, result = authenticate_user(
        email=login_request.email, password=login_request.password
    )

    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=result)

    return result


@router.post(
    "/refresh",
    response_model=RefreshTokenResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/minute")
async def refresh_token(request: Request, refresh_request: RefreshTokenRequest) -> RefreshTokenResponse:
    """
    Issue a new access token using a valid refresh token.
    
    Rate limited:** 30 requests per minute per IP address

    """
    success, new_token = refresh_access_token(refresh_request.refresh_token)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    return {
        "message": "Access token refreshed successfully",
        "access_token": new_token,
        "token_type": "bearer",
    }


@router.post(
    "/logout",
    response_model=LogoutResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("60/minute")
async def logout(request: Request, logout_request: LogoutRequest) -> LogoutResponse:
    """
    Logout user by revoking their access token.
    
    Rate limited:** 60 requests per minute per IP address

    """
    revoke_token(logout_request.access_token)

    return {"message": "Logged out successfully"}
