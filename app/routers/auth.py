from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.database import get_db
from app.routers.deps import get_current_user
from app.models.user import User
from app.services.auth import AuthService
from app.schemas.auth import (
    UserRegister, UserLogin, GoogleAuthRequest,
    TokenResponse, UserResponse, MessageResponse,
    ForgotPasswordRequest, ResetPasswordRequest,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
limiter = Limiter(key_func=get_remote_address)


@router.post("/register", response_model=UserResponse, status_code=201,
             summary="Register a new user",
             description="Creates a new account with email and password. "
                         "A verification token is generated (to be sent via email). "
                         "The user must verify their email before logging in. "
                         "Rate limited to 5 requests per minute.")
@limiter.limit("5/minute")
async def register(request: Request, data: UserRegister, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    return await service.register(data)


@router.post("/login", response_model=TokenResponse,
             summary="Login with email and password",
             description="Authenticates the user and returns a JWT access token. "
                         "The token must be included in the `Authorization: Bearer <token>` header "
                         "for all protected endpoints. Requires a verified email. "
                         "Rate limited to 10 requests per minute.")
@limiter.limit("10/minute")
async def login(request: Request, data: UserLogin, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    return await service.login(data.email, data.password)


@router.post("/google", response_model=TokenResponse,
             summary="Authenticate with Google OAuth",
             description="Authenticates using a Google OAuth credential token. "
                         "If the user doesn't exist, a new verified account is created automatically. "
                         "If the email already exists, the Google ID is linked to the existing account.")
@limiter.limit("10/minute")
async def google_auth(request: Request, data: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    return await service.google_auth(data.credential)


@router.post("/verify-email/{token}", response_model=MessageResponse,
             summary="Verify email address",
             description="Confirms the user's email address using the verification token "
                         "sent during registration. After verification, the user can log in.")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    await service.verify_email(token)
    return MessageResponse(message="Correo verificado exitosamente")


@router.post("/forgot-password", response_model=MessageResponse,
             summary="Request password reset",
             description="Sends a password reset token to the user's email. "
                         "For security, always returns success even if the email doesn't exist. "
                         "Rate limited to 3 requests per minute.")
@limiter.limit("3/minute")
async def forgot_password(request: Request, data: ForgotPasswordRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    await service.request_password_reset(data.email)
    return MessageResponse(message="Si el correo existe, se enviará un enlace de recuperación")


@router.post("/reset-password", response_model=MessageResponse,
             summary="Reset password with token",
             description="Sets a new password using the reset token received via email. "
                         "The token expires after 1 hour. "
                         "Rate limited to 5 requests per minute.")
@limiter.limit("5/minute")
async def reset_password(request: Request, data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):
    service = AuthService(db)
    await service.reset_password(data.token, data.new_password)
    return MessageResponse(message="Contraseña actualizada exitosamente")


@router.get("/me", response_model=UserResponse,
            summary="Get current user profile",
            description="Returns the authenticated user's profile information. "
                        "Requires a valid JWT token in the Authorization header.")
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user
