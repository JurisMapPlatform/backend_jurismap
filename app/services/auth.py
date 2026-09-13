import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from jose import jwt, JWTError
from passlib.context import CryptContext
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import UserRegister, TokenResponse

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HU-02: el enlace de verificación vence a las 24 horas. El vencimiento viaja dentro del propio
# token ("<uuid>.<timestamp>"), así no hace falta una columna nueva en la base de datos.
VERIFICATION_TTL = timedelta(hours=24)


def new_verification_token(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    expires = int((now + VERIFICATION_TTL).timestamp())
    return f"{uuid.uuid4()}.{expires}"


def verification_token_expired(token: str, created_at: datetime | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    _, sep, expires = token.rpartition(".")
    if sep and expires.isdigit():
        return now.timestamp() > int(expires)
    # Tokens emitidos antes de este cambio (uuid sin vencimiento): vencen 24 h después del registro.
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now > created_at + VERIFICATION_TTL


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    async def register(self, data: UserRegister) -> User:
        existing = await self.repo.get_by_email(data.email)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este correo ya está registrado. Inicia sesión o recupera tu contraseña.")

        user = User(
            email=data.email,
            hashed_password=pwd_context.hash(data.password),
            full_name=data.full_name,
            verification_token=new_verification_token(),
        )
        user = await self.repo.create(user)

        from app.services.email import send_verification_email
        await send_verification_email(user.email, user.full_name, user.verification_token)
        return user

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.repo.get_by_email(email)
        if not user or not user.hashed_password or not pwd_context.verify(password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Correo o contraseña incorrectos. Revisa tus datos o usa «¿Olvidaste tu contraseña?».")
        if settings.require_email_verification and not user.is_verified:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Debes verificar tu correo antes de iniciar sesión. Revisa tu bandeja de entrada o reenvía el correo de validación.")
        return self._create_token(user)

    async def google_auth(self, credential: str) -> TokenResponse:
        try:
            idinfo = id_token.verify_oauth2_token(credential, google_requests.Request(), settings.google_client_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No se pudo validar tu cuenta de Google. Inténtalo de nuevo.")

        # Solo se confía en correos que Google verificó: si no, alguien podría vincular una cuenta de
        # Google con un correo ajeno sin verificar y entrar a la cuenta JurisMap de esa persona.
        if not idinfo.get("email_verified"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Tu cuenta de Google no tiene el correo verificado. Verifícalo en Google o regístrate con correo y contraseña.")

        google_id = idinfo["sub"]
        email = idinfo["email"]
        name = idinfo.get("name", email.split("@")[0])

        user = await self.repo.get_by_google_id(google_id)
        if not user:
            user = await self.repo.get_by_email(email)
            if user:
                user.google_id = google_id
                user.is_verified = True
                await self.repo.update(user)
            else:
                user = User(email=email, full_name=name, google_id=google_id, is_verified=True)
                user = await self.repo.create(user)

        return self._create_token(user)

    async def verify_email(self, token: str) -> bool:
        user = await self.repo.get_by_verification_token(token)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail="El enlace de verificación no es válido o ya fue usado.")
        if verification_token_expired(token, user.created_at):
            raise HTTPException(status_code=status.HTTP_410_GONE,
                                detail="El enlace de verificación expiró. Inicia sesión para solicitar uno nuevo.")
        user.is_verified = True
        user.verification_token = None
        await self.repo.update(user)
        return True

    async def resend_verification(self, email: str) -> None:
        # HU-02: reenvía el correo de validación con un nuevo enlace. Por seguridad no revela
        # si el correo existe; ignora cuentas ya verificadas o de Google (sin contraseña).
        user = await self.repo.get_by_email(email)
        if not user or user.is_verified or not user.hashed_password:
            return
        user.verification_token = new_verification_token()
        await self.repo.update(user)

        from app.services.email import send_verification_email
        await send_verification_email(user.email, user.full_name, user.verification_token)

    async def request_password_reset(self, email: str) -> None:
        user = await self.repo.get_by_email(email)
        if not user:
            return
        user.reset_token = str(uuid.uuid4())
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await self.repo.update(user)

        from app.services.email import send_password_reset_email
        await send_password_reset_email(user.email, user.full_name, user.reset_token)

    async def reset_password(self, token: str, new_password: str) -> None:
        user = await self.repo.get_by_reset_token(token)
        if not user or not user.reset_token_expires or user.reset_token_expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El enlace para restablecer la contraseña no es válido o expiró. Solicita uno nuevo desde «¿Olvidaste tu contraseña?».")
        user.hashed_password = pwd_context.hash(new_password)
        user.reset_token = None
        user.reset_token_expires = None
        await self.repo.update(user)

    def _create_token(self, user: User) -> TokenResponse:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
        payload = {"sub": str(user.id), "exp": expire}
        token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
        return TokenResponse(access_token=token)

    @staticmethod
    async def get_current_user(token: str, db: AsyncSession) -> User:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            user_id = payload.get("sub")
            if user_id is None:
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
        except JWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")

        repo = UserRepository(db)
        user = await repo.get_by_id(uuid.UUID(user_id))
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
        return user
