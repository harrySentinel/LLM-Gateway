"""
Symmetric encryption for provider API keys stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256). The encryption key is read from
ENCRYPTION_KEY env var. In development, a deterministic fallback key is
derived from a fixed string so the app works without configuration.

IMPORTANT: set ENCRYPTION_KEY to a proper Fernet key in production.
Generate one with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Then: fly secrets set ENCRYPTION_KEY="<output>"
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key:
        # Dev fallback — deterministic, NOT secure for production
        raw = hashlib.sha256(b"llm-gateway-dev-encryption-key-v1").digest()
        key = base64.urlsafe_b64encode(raw).decode()
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
