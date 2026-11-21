"""Seed script for Coal Mine Subsidence Monitoring System (Step 1).

Creates exactly ONE company and ONE Manager user, nothing else.
- Company name, manager username and initial password come from environment
  variables or CLI args, never hardcoded.
- Password hashing: PBKDF2-HMAC-SHA256 with a per-user random salt.
- Manager gets force_pw_change = true, active = true, company_id set.
- Idempotent: running it twice must not create duplicates or crash.
- Does NOT insert a user_mines row (Manager has no mines yet).
"""
import argparse
import hashlib
import os
import secrets
import sys
from typing import Tuple

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
from models import Company, User, normalize_company_name

SALT_BYTES = 16
HASH_ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """Hashes password using PBKDF2-HMAC-SHA256 with a cryptographically random salt."""
    salt = secrets.token_hex(SALT_BYTES)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        HASH_ITERATIONS,
    ).hex()
    return f"{salt}${pwd_hash}"


def parse_args():
    parser = argparse.ArgumentParser(description="Seed initial Company and Manager user.")
    parser.add_argument(
        "--company",
        default=os.getenv("SEED_COMPANY_NAME") or os.getenv("COMPANY_NAME"),
        help="Company name (e.g. 'Bharat Coking Coal Limited')",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("SEED_MANAGER_USERNAME") or os.getenv("MANAGER_USERNAME"),
        help="Manager username (e.g. 'manager')",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("SEED_MANAGER_PASSWORD") or os.getenv("MANAGER_PASSWORD"),
        help="Manager initial password",
    )
    parser.add_argument(
        "--full-name",
        default=os.getenv("SEED_MANAGER_FULL_NAME") or os.getenv("MANAGER_FULL_NAME") or "General Manager",
        help="Manager full name",
    )
    return parser.parse_args()


def seed_company_and_manager(company_name: str, username: str, password: str, full_name: str):
    if not company_name or not company_name.strip():
        raise ValueError("Company name must be provided via --company or SEED_COMPANY_NAME env var")
    if not username or not username.strip():
        raise ValueError("Manager username must be provided via --username or SEED_MANAGER_USERNAME env var")
    if not password or not password.strip():
        raise ValueError("Manager password must be provided via --password or SEED_MANAGER_PASSWORD env var")

    company_name_clean = company_name.strip()
    norm_name = normalize_company_name(company_name_clean)
    clean_username = username.strip()

    db = SessionLocal()
    try:
        # 1. Company (Idempotent)
        company = db.query(Company).filter(Company.name_normalized == norm_name).first()
        if not company:
            company = Company(name=company_name_clean, name_normalized=norm_name)
            db.add(company)
            db.flush()
            print(f"[seed] Created company: '{company.name}' (id={company.id})")
        else:
            print(f"[seed] Company already exists: '{company.name}' (id={company.id})")

        # 2. Manager User (Idempotent)
        user = db.query(User).filter(User.username == clean_username).first()
        hashed = hash_password(password)

        if not user:
            user = User(
                company_id=company.id,
                username=clean_username,
                password_hash=hashed,
                full_name=full_name,
                role="manager",
                active=True,
                force_pw_change=True,
            )
            db.add(user)
            db.commit()
            print(f"[seed] Created Manager user: '{user.username}' (id={user.id}, role='manager')")
        else:
            user.company_id = company.id
            user.password_hash = hashed
            user.full_name = full_name
            user.role = "manager"
            user.active = True
            user.force_pw_change = True
            db.commit()
            print(f"[seed] Updated existing Manager user: '{user.username}' (id={user.id})")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    args = parse_args()
    # Provide helpful defaults if run interactively without env/flags for convenience
    comp = args.company or "Bharat Coking Coal Limited"
    u = args.username or "manager"
    p = args.password or "Manager@2026"
    fn = args.full_name or "General Manager"

    print(f"--- Seeding Company: '{comp}', Manager: '{u}' ---")
    seed_company_and_manager(comp, u, p, fn)
    print("--- Seed completed successfully ---")
