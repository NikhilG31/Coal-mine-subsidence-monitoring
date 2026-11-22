"""Cryptographic key generation and verification service for hardware gateways.

Owns creating 256-bit gateway tokens, fast SHA-256 hashing, and constant-time verification.
Must NOT store keys in plaintext, print keys to logs, or handle HTTP routing.
"""
import hashlib
import hmac
import secrets


def generate_key() -> str:
    """Generates a cryptographically random gateway key token."""
    return "gwk_" + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    """Computes SHA-256 hexadecimal digest of a gateway key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def verify_key(key: str, hashed: str) -> bool:
    """Performs constant-time comparison of a gateway key against its stored hash."""
    if not key or not hashed:
        return False
    return hmac.compare_digest(hash_key(key), hashed)
