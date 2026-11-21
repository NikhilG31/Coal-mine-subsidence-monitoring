"""Integration tests for company and Manager bootstrapping.

Owns validating seed script idempotency and password reset rules.
Must NOT touch production or development databases.
"""
from app.cli.seed_manager import seed_manager
from app.models.company import Company
from app.models.user import User
from app.security.passwords import verify_password


def test_seed_manager_idempotency_and_reset(db):
    """Verifies seed_manager idempotency and password reset flag behaviour."""
    company_name = "Singareni Collieries Company"
    username = "gm_admin"
    initial_pass = "InitialPass@2026"
    full_name = "General Manager Kothagudem"

    # 1. Initial run creates Company and User
    seed_manager(company_name, username, initial_pass, full_name, reset_password=False)

    comp = db.query(Company).filter(Company.name == company_name).first()
    assert comp is not None
    user = db.query(User).filter(User.username == username).first()
    assert user is not None
    assert user.role == "manager"
    assert user.company_id == comp.id
    assert verify_password(initial_pass, user.password_hash)

    # 2. Re-run without --reset-password must change NOTHING
    new_unapplied_pass = "ShouldNotChange@2026"
    seed_manager(company_name, username, new_unapplied_pass, full_name, reset_password=False)

    db.refresh(user)
    assert verify_password(initial_pass, user.password_hash)
    assert not verify_password(new_unapplied_pass, user.password_hash)

    # 3. Re-run with --reset-password must update the password
    seed_manager(company_name, username, new_unapplied_pass, full_name, reset_password=True)

    db.refresh(user)
    assert verify_password(new_unapplied_pass, user.password_hash)
    assert user.role == "manager"
    assert user.company_id == comp.id
