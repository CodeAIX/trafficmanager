import base64
import hashlib
import os
import secrets
from datetime import timedelta
from pathlib import Path

from argon2 import PasswordHasher
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings
from .database import utcnow

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except Exception:
        return False


def _master_key() -> bytes:
    if settings.app_master_key:
        raw = settings.app_master_key.encode()
        try:
            decoded = base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
        return hashlib.sha256(raw).digest()
    path = Path(settings.data_dir) / "master.key"
    if not path.exists():
        path.write_bytes(os.urandom(32))
        path.chmod(0o600)
    key = path.read_bytes()
    if len(key) != 32:
        raise RuntimeError("/data/master.key must be exactly 32 bytes")
    return key


def encrypt_token(token: str) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    return AESGCM(_master_key()).encrypt(nonce, token.encode(), b"3xui-node-token"), nonce


def decrypt_token(ciphertext: bytes, nonce: bytes) -> str:
    return AESGCM(_master_key()).decrypt(nonce, ciphertext, b"3xui-node-token").decode()


def new_session() -> tuple[str, str, str, object]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest(), secrets.token_urlsafe(24), utcnow() + timedelta(minutes=settings.session_timeout_minutes)
