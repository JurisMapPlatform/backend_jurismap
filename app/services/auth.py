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


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)

    async def register(self, data: UserRegister) -> User:
        existing = await self.repo.get_by_email(data.email)
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El correo ya está registrado")

        user = User(
            email=data.email,
            hashed_password=pwd_context.hash(data.password),
            full_name=data.full_name,
            verification_token=str(uuid.uuid4()),
        )
        return await self.repo.create(user)

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.repo.get_by_email(email)
        if not user or not user.hashed_password or not pwd_context.verify(password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciales incorrectas")
        # TODO: reactivar cuando se implemente el servicio de email
        # if not user.is_verified:
        #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Correo no verificado")
        return self._create_token(user)

    async def google_auth(self, credential: str) -> TokenResponse:
        try:
            idinfo = id_token.verify_oauth2_token(credential, google_requests.Request(), settings.google_client_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token de Google inválido")

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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token inválido")
        user.is_verified = True
        user.verification_token = None
        await self.repo.update(user)
        return True

    async def request_password_reset(self, email: str) -> None:
        user = await self.repo.get_by_email(email)
        if not user:
            return
        user.reset_token = str(uuid.uuid4())
        user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
        await self.repo.update(user)

    async def reset_password(self, token: str, new_password: str) -> None:
        user = await self.repo.get_by_reset_token(token)
        if not user or not user.reset_token_expires or user.reset_token_expires < datetime.now(timezone.utc):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token inválido o expirado")
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
