import os
import hashlib
import hmac
import datetime
import jwt
from typing import Union

# Secret key for JWT encoding and decoding
SECRET_KEY = os.getenv("SECRET_KEY", "7d4db3b9e4d081f29aa45037fbca27d05cf4cb099eb0aefd94d31d7ec788c005")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30 # Shorter expiry for better security
REFRESH_TOKEN_EXPIRE_DAYS = 7     # Refresh token lasts 7 days

# PBKDF2 configuration
SALT_LENGTH = 32
HASH_ITERATIONS = 100000


def get_password_hash(password: str) -> str:
    """Hash a password using PBKDF2 with a secure salt."""
    salt = os.urandom(SALT_LENGTH)
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        HASH_ITERATIONS
    )
    # Store both the salt and the hash in hex format
    return f"{salt.hex()}${pwd_hash.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hashed version."""
    try:
        salt_hex, hash_hex = hashed_password.split("$")
        salt = bytes.fromhex(salt_hex)
        expected_hash = bytes.fromhex(hash_hex)
    except ValueError:
        return False

    computed_hash = hashlib.pbkdf2_hmac(
        'sha256',
        plain_password.encode('utf-8'),
        salt,
        HASH_ITERATIONS
    )
    return hmac.compare_digest(computed_hash, expected_hash)


def create_access_token(data: dict, expires_delta: Union[datetime.timedelta, None] = None) -> str:
    """Generate a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> Union[dict, None]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

def create_refresh_token(data: dict) -> str:
    """Generate a JWT refresh token with longer expiry."""
    to_encode = data.copy()
    expire = datetime.datetime.utcnow() + datetime.timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "refresh": True})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
