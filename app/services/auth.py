import hashlib
import secrets


def generate_key() -> str:
    """
    Generates a gateway API key with 32 bytes (256 bits) of randomness.
    The gw_ prefix makes keys identifiable in logs and .env files.
    """
    return "gw_" + secrets.token_urlsafe(32)


def hash_key(token: str) -> str:
    """
    SHA-256 hex digest of a token — this is what gets stored in the DB.

    SHA-256 is correct here (not bcrypt) because the token itself is
    high-entropy random data. bcrypt is needed for low-entropy secrets
    like user passwords, where a slow hash defeats dictionary attacks.
    A 256-bit random string can't be brute-forced even with a fast hash.
    """
    return hashlib.sha256(token.encode()).hexdigest()
