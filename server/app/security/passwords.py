"""Password hashing and verification utilities.

Owns PBKDF2-HMAC-SHA256 password hashing with salt generation and legacy hash verification.
Must NOT manage HTTP session cookies or issue JWT tokens.
"""
import hashlib
import secrets

PBKDF2_ITERATIONS = 600_000
LEGACY_ITERATIONS = 100_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hashes a password with PBKDF2-HMAC-SHA256, 600,000 iterations, and random 16-byte salt."""
    salt_hex = secrets.token_hex(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_hex}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a plain password against stored hash (new format or legacy step 1 format)."""
    if not stored_hash or not password:
        return False

    # New standard format: pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
    if stored_hash.startswith("pbkdf2_sha256$"):
        parts = stored_hash.split("$")
        if len(parts) != 4:
            return False
        try:
            iterations = int(parts[1])
            salt_hex = parts[2]
            expected_hex = parts[3]
            calc_dk = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                iterations,
            )
            return secrets.compare_digest(calc_dk.hex(), expected_hex)
        except Exception:
            return False

    # Legacy step 1 format: <salt_hex>$<hash_hex> (100,000 iterations)
    if "$" in stored_hash:
        parts = stored_hash.split("$")
        if len(parts) == 2:
            salt_hex, expected_hex = parts[0], parts[1]
            try:
                # Legacy step 1 hashed with salt.encode("utf-8")
                calc_dk = hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    salt_hex.encode("utf-8"),
                    LEGACY_ITERATIONS,
                )
                if secrets.compare_digest(calc_dk.hex(), expected_hex):
                    return True
                # Fallback if bytes.fromhex was used
                calc_dk_bytes = hashlib.pbkdf2_hmac(
                    "sha256",
                    password.encode("utf-8"),
                    bytes.fromhex(salt_hex),
                    LEGACY_ITERATIONS,
                )
                return secrets.compare_digest(calc_dk_bytes.hex(), expected_hex)
            except Exception:
                return False

    return False


def needs_rehash(stored_hash: str) -> bool:
    """Returns True if the hash was computed with an older algorithm or lower iteration count."""
    if not stored_hash or not stored_hash.startswith("pbkdf2_sha256$"):
        return True
    parts = stored_hash.split("$")
    if len(parts) != 4:
        return True
    try:
        return int(parts[1]) < PBKDF2_ITERATIONS
    except ValueError:
        return True
