"""HTTP router for user authentication and authorization stubs.

Owns auth and user endpoints (stub implementations for Step 1 compatibility).
Must NOT issue unverified credentials or compromise password integrity.
"""
from fastapi import APIRouter

router = APIRouter(tags=["auth"])


@router.get("/api/auth/me")
def api_me():
    return {
        "user": {"id": 1, "username": "manager", "role": "manager", "email": "manager@coalmine.gov.in"},
        "allowed_gateways": None,
    }


@router.post("/api/auth/login")
def api_login():
    return {
        "token": "step1-dev-token",
        "user": {"id": 1, "username": "manager", "role": "manager"},
    }


@router.post("/api/auth/logout")
def api_logout():
    return {"message": "Logged out"}


@router.get("/api/users")
def api_users():
    return {"users": [{"id": 1, "username": "manager", "role": "manager"}]}


@router.post("/api/users")
def api_create_user():
    return {"message": "User added successfully"}
