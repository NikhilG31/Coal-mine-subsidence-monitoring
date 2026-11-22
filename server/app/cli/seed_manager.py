"""CLI tool for initial company and Manager account bootstrapping.

Owns creating the initial Company and Manager user with secure PBKDF2 password hashing.
Must NOT create mines, tunnels, gateways, or non-manager user accounts.
"""
import argparse
import getpass
import sys
from typing import Optional

from app.config import (
    SEED_COMPANY_NAME,
    SEED_MANAGER_FULL_NAME,
    SEED_MANAGER_PASSWORD,
    SEED_MANAGER_USERNAME,
)
from app.database import SessionLocal
from app.models.company import Company, normalize_company_name
from app.models.user import User
from app.security.passwords import hash_password
from app.security.password_policy import validate_new_password


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed initial Company and Manager user.")
    parser.add_argument("--company", default=SEED_COMPANY_NAME, help="Company name")
    parser.add_argument("--username", default=SEED_MANAGER_USERNAME, help="Manager username")
    parser.add_argument("--password", default=SEED_MANAGER_PASSWORD, help="Manager initial password")
    parser.add_argument("--full-name", default=SEED_MANAGER_FULL_NAME, help="Manager full name")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Explicitly reset password if the user already exists",
    )
    return parser.parse_args()


def seed_manager(
    company_name: Optional[str],
    username: Optional[str],
    password: Optional[str],
    full_name: str,
    reset_password: bool = False,
) -> None:
    """Creates company and manager account with strict idempotency."""
    if not company_name or not company_name.strip():
        sys.exit("Error: Company name must be provided via --company or SEED_COMPANY_NAME env var.")

    if not username or not username.strip():
        sys.exit("Error: Manager username must be provided via --username or SEED_MANAGER_USERNAME env var.")

    clean_company = company_name.strip()
    clean_username = username.strip()

    if "change_me" in clean_company.lower():
        sys.exit("Error: Company name cannot contain 'CHANGE_ME'.")

    if "change_me" in clean_username.lower():
        sys.exit("Error: Manager username cannot contain 'CHANGE_ME'.")

    norm_name = normalize_company_name(clean_company)

    # Resolve password: argument -> env -> getpass -> exit
    if not password:
        if sys.stdin.isatty():
            password = getpass.getpass(f"Enter initial password for Manager '{clean_username}': ")
        else:
            sys.exit(
                "Error: Manager password must be provided via --password or SEED_MANAGER_PASSWORD "
                "when running non-interactively."
            )

    if not password or not password.strip():
        sys.exit("Error: Manager password cannot be empty.")

    clean_password = password.strip()
    try:
        validate_new_password(clean_password, clean_username)
    except ValueError as e:
        sys.exit(f"Error: Invalid password: {e}")

    clean_full_name = full_name.strip() if full_name else "General Manager"

    db = SessionLocal()
    try:
        # 1. Company (Idempotent by normalized name)
        company = db.query(Company).filter(Company.name_normalized == norm_name).first()
        if not company:
            company = Company(name=clean_company, name_normalized=norm_name)
            db.add(company)
            db.flush()
            print(f"[seed_manager] Created company: '{company.name}' (id={company.id})")
        else:
            print(f"[seed_manager] Company already exists: '{company.name}' (id={company.id})")

        # 2. Manager user (Idempotent)
        user = db.query(User).filter(User.username == clean_username).first()
        if user:
            if reset_password:
                user.password_hash = hash_password(clean_password)
                user.force_pw_change = True
                db.commit()
                print(f"[seed_manager] Password reset successfully for existing user '{clean_username}'.")
            else:
                print(
                    f"[seed_manager] User '{clean_username}' already exists. No changes made. "
                    "(Use --reset-password to update password)."
                )
        else:
            user = User(
                company_id=company.id,
                username=clean_username,
                password_hash=hash_password(clean_password),
                full_name=clean_full_name,
                role="manager",
                active=True,
                force_pw_change=True,
            )
            db.add(user)
            db.commit()
            print(f"[seed_manager] Created Manager user: '{user.username}' (id={user.id}, role='manager')")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def main() -> None:
    args = parse_args()
    seed_manager(
        company_name=args.company,
        username=args.username,
        password=args.password,
        full_name=args.full_name,
        reset_password=args.reset_password,
    )


if __name__ == "__main__":
    main()
