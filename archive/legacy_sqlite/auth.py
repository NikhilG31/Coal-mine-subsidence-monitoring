"""Authentication, authorization, and tenant isolation for Mine Subsidence Monitoring.

Uses PBKDF2-HMAC-SHA256 with cryptographically random salt for zero-dependency,
battle-tested password security without external compiled binaries.
"""
import hashlib
import os
import secrets
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

SALT_BYTES = 16
HASH_ITERATIONS = 100_000


def hash_password(password: str) -> Tuple[str, str]:
    """Generates salt and hashes password with PBKDF2-HMAC-SHA256."""
    salt = secrets.token_hex(SALT_BYTES)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ITERATIONS
    ).hex()
    return pwd_hash, salt


def verify_password(password: str, pwd_hash: str, salt: str) -> bool:
    """Verifies password against stored PBKDF2 hash and salt."""
    test_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ITERATIONS
    ).hex()
    return secrets.compare_digest(pwd_hash, test_hash)


def init_auth_tables(db: sqlite3.Connection):
    """Initializes user, session, and gateway permission tables."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator', -- 'admin' or 'operator'
        created_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS user_sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS user_gateways (
        user_id INTEGER NOT NULL,
        gateway_mac_address TEXT NOT NULL,
        PRIMARY KEY (user_id, gateway_mac_address),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_token ON user_sessions(token);
    CREATE INDEX IF NOT EXISTS idx_user_gw ON user_gateways(user_id, gateway_mac_address);
    """)

    # Seed default admin and operator if users table is empty
    cur = db.execute("SELECT COUNT(*) as count FROM users")
    if cur.fetchone()["count"] == 0:
        admin_hash, admin_salt = hash_password("admin123")
        op_hash, op_salt = hash_password("operator123")
        now = time.time()
        
        db.execute(
            "INSERT INTO users (username, email, password_hash, salt, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("admin", "admin@coalmine.gov.in", admin_hash, admin_salt, "admin", now)
        )
        db.execute(
            "INSERT INTO users (username, email, password_hash, salt, role, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("mine_operator", "safety.operator@coalmine.gov.in", op_hash, op_salt, "operator", now)
        )
        db.commit()


def create_user(db: sqlite3.Connection, username: str, password: str, email: str = "", role: str = "operator") -> Dict:
    """Creates a new user account."""
    pwd_hash, salt = hash_password(password)
    now = time.time()
    cur = db.execute(
        "INSERT INTO users (username, email, password_hash, salt, role, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (username.strip(), email.strip(), pwd_hash, salt, role, now)
    )
    user_id = cur.lastrowid
    db.commit()
    return {"id": user_id, "username": username.strip(), "email": email.strip(), "role": role}


def authenticate(db: sqlite3.Connection, username: str, password: str) -> Optional[Dict]:
    """Authenticates username/password and creates a session token."""
    row = db.execute("SELECT * FROM users WHERE username = ?", (username.strip(),)).fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"], row["salt"]):
        return None

    # Create session (7 days validity)
    token = secrets.token_urlsafe(32)
    now = time.time()
    expires_at = now + (7 * 86400)
    db.execute(
        "INSERT INTO user_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, row["id"], now, expires_at)
    )
    db.commit()

    return {
        "token": token,
        "user": {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "role": row["role"]
        }
    }


def get_current_user(db: sqlite3.Connection, token: Optional[str]) -> Optional[Dict]:
    """Retrieves authenticated user from session token."""
    if not token:
        return None
    now = time.time()
    row = db.execute("""
        SELECT u.id, u.username, u.email, u.role
        FROM user_sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.token = ? AND s.expires_at > ?
    """, (token, now)).fetchone()
    
    if not row:
        return None
    return dict(row)


def logout(db: sqlite3.Connection, token: str):
    """Terminates session."""
    db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
    db.commit()


def get_user_allowed_gateways(db: sqlite3.Connection, user: Dict) -> Optional[List[str]]:
    """Returns list of gateway MACs the user has access to.
    If user is 'admin', returns None (meaning unrestricted access).
    """
    if user["role"] == "admin":
        return None # Unrestricted
    
    rows = db.execute(
        "SELECT gateway_mac_address FROM user_gateways WHERE user_id = ?",
        (user["id"],)
    ).fetchall()
    return [r["gateway_mac_address"] for r in rows]


def assign_gateway_to_user(db: sqlite3.Connection, user_id: int, gateway_mac: str):
    """Grants a user access to a specific gateway."""
    db.execute(
        "INSERT OR IGNORE INTO user_gateways (user_id, gateway_mac_address) VALUES (?, ?)",
        (user_id, gateway_mac)
    )
    db.commit()
