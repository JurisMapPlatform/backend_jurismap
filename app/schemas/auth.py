import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr = Field(examples=["estudiante@universidad.edu.pe"])
    password: str = Field(min_length=8, max_length=128, examples=["MiClave123!"])
    full_name: str = Field(min_length=2, max_length=255, examples=["Juan Pérez López"])


class UserLogin(BaseModel):
    email: EmailStr = Field(examples=["estudiante@universidad.edu.pe"])
    password: str = Field(examples=["MiClave123!"])


class GoogleAuthRequest(BaseModel):
    credential: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    is_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class MessageResponse(BaseModel):
    message: str
