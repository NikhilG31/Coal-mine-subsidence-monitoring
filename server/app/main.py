"""FastAPI application entrypoint for Coal Mine Subsidence Monitoring System.

Owns application lifecycle, router registration, CORS configuration, and static file mounting.
Must NOT define route implementations, database schemas, or CLI handlers directly.
"""
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (
    alerts,
    auth,
    demo,
    export,
    gateways,
    health,
    history,
    ingest,
    nodes,
    notifications,
    placements,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Coal Mine Subsidence Monitoring System")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Area Routers
app.include_router(ingest.router)
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


@app.get("/")
def index():
    """Serves the dashboard index page."""
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
