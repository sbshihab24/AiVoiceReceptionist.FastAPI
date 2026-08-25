from pydantic import BaseModel, EmailStr
from typing import Optional
import datetime

class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    name: Optional[str] = None
    password: str

class UserResponse(UserBase):
    id: int
    name: Optional[str] = None
    is_active: bool
    created_at: datetime.datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    name: Optional[str] = None

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class UserRegisterResponse(Token):
    user: UserResponse

class TokenData(BaseModel):
    email: Optional[str] = None

class OTPRequest(BaseModel):
    email: EmailStr

class ChangePasswordWithOTP(BaseModel):
    email: EmailStr
    otp: str
    new_password: str
