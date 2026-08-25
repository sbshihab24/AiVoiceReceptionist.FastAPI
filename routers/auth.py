import random
import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from database import get_db
from models.auth_models import User
from schemas.auth_schemas import UserCreate, UserResponse, Token, UserRegisterResponse, OTPRequest, ChangePasswordWithOTP, RefreshTokenRequest
from utils.auth_utils import get_password_hash, verify_password, create_access_token, decode_access_token, create_refresh_token
from services.email_service import send_otp_email

router = APIRouter(
    prefix="/api/auth",
    tags=["Authentication"]
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """Dependency to retrieve the authenticated current user."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception
    email: str = payload.get("sub")
    if email is None:
        raise credentials_exception
    
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user


@router.post("/register", response_model=UserRegisterResponse, status_code=status.HTTP_201_CREATED)
def register_user(user_in: UserCreate, db: Session = Depends(get_db)):
    """Register a new user in the system and return user data along with token."""
    existing_user = db.query(User).filter(User.email == user_in.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    hashed_password = get_password_hash(user_in.password)
    db_user = User(
        email=user_in.email,
        name=user_in.name,
        hashed_password=hashed_password
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    access_token = create_access_token(data={"sub": db_user.email})
    refresh_token = create_refresh_token(data={"sub": db_user.email})
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "name": db_user.name,
        "user": db_user
    }


@router.post("/login", response_model=Token)
def login_user(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Login to the system using email/username and password."""
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )

    access_token = create_access_token(data={"sub": user.email})
    refresh_token = create_refresh_token(data={"sub": user.email})
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "name": user.name
    }


# @router.post("/login-json", response_model=Token)
def login_user_json(user_in: UserCreate, db: Session = Depends(get_db)):
    """Login with JSON request body (email & password) instead of form data."""
    user = db.query(User).filter(User.email == user_in.email).first()
    if not user or not verify_password(user_in.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )

    access_token = create_access_token(data={"sub": user.email})
    refresh_token = create_refresh_token(data={"sub": user.email})
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "name": user.name or "Unknown User"
    }


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Fetch profile details of the logged in user."""
    return current_user


@router.post("/send-otp")
async def send_otp(otp_in: OTPRequest, db: Session = Depends(get_db)):
    """Generate and send an OTP for password reset."""
    user = db.query(User).filter(User.email == otp_in.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not registered"
        )
    
    otp = str(random.randint(100000, 999999))
    user.otp = otp
    user.otp_expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
    db.commit()
    db.refresh(user)
    
    # Send OTP via Email
    try:
        await send_otp_email(user.email, otp)
    except Exception as e:
        print(f"Error sending OTP email: {e}")
        # Optionally raise error or continue
    
    return {"message": "OTP generated and sent successfully to email"}


@router.post("/reset-password-with-otp")
def reset_password_with_otp(change_in: ChangePasswordWithOTP, db: Session = Depends(get_db)):
    """Verify OTP and reset the user's password."""
    user = db.query(User).filter(User.email == change_in.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not registered"
        )
        
    if not user.otp or user.otp != change_in.otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP"
        )
        
    if not user.otp_expires_at or user.otp_expires_at < datetime.datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP has expired"
        )
        
    user.hashed_password = get_password_hash(change_in.new_password)
    user.otp = None
    user.otp_expires_at = None
    db.commit()
    db.refresh(user)
    
    return {"message": "Password changed successfully!"}


@router.post("/refresh", response_model=Token)
def refresh_access_token(refresh_in: RefreshTokenRequest, db: Session = Depends(get_db)):
    """Generate a new access token using a valid refresh token."""
    payload = decode_access_token(refresh_in.refresh_token)
    if payload is None or not payload.get("refresh"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
    
    email = payload.get("sub")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
        
    access_token = create_access_token(data={"sub": user.email})
    # We also rotate the refresh token
    new_refresh_token = create_refresh_token(data={"sub": user.email})
    
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
        "name": user.name
    }
