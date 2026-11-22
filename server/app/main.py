"""FastAPI application entrypoint for Coal Mine Subsidence Monitoring System.

Owns application lifecycle, router registration, CORS configuration, and static file mounting.
Must NOT define route implementations, database schemas, or CLI handlers directly.
"""
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    ALLOW_UNKEYED_INGEST,
    CORS_ORIGINS,
    validate_jwt_secret,
)

# Enforce fail-fast secret validation at startup
validate_jwt_secret()

if ALLOW_UNKEYED_INGEST:
    logging.getLogger("uvicorn.error").warning(
        "*** CRITICAL SECURITY WARNING: ALLOW_UNKEYED_INGEST is ENABLED! "
        "Gateways may ingest telemetry without authentication keys. Do NOT use in production! ***"
    )

from app.routers import (
    alerts,
    auth,
    demo,
    export,
    gateways,
    health,
    history,
    ingest,
    mines,
    nodes,
    notifications,
    placements,
    users,
)
from app.security.dependencies import PasswordChangeRequiredException

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Coal Mine Subsidence Monitoring System")

# Spec B8: Remove allow_origins=["*"] with allow_credentials=True.
# Default is same-origin only. Use explicit origins from CORS_ORIGINS when provided.
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Exception handler for forced password change gate
@app.exception_handler(PasswordChangeRequiredException)
async def password_change_required_handler(request: Request, exc: PasswordChangeRequiredException):
    return JSONResponse(
        status_code=403,
        content={
            "code": "password_change_required",
            "detail": "Password change required before accessing this resource.",
        },
    )


# Register Area Routers
app.include_router(ingest.router)
app.include_router(mines.router)
app.include_router(nodes.router)
app.include_router(gateways.router)
app.include_router(history.router)
app.include_router(placements.router)
app.include_router(export.router)
app.include_router(health.router)
app.include_router(alerts.router)
app.include_router(notifications.router)
app.include_router(demo.router)
app.include_router(auth.router)
app.include_router(users.router)


@app.get("/")
def index():
    """Serves the dashboard index page."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
