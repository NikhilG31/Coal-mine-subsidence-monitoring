# Coal Mine Subsidence Monitoring - Server Backend

This directory contains the FastAPI server, SQLite database, branch file logging, ML classifier engine, and static web dashboard.

Refer to the main [README.md](../README.md) in the project root for comprehensive documentation, firmware struct definitions, notification configurations, and architecture diagrams.

## Quick Start
```bash
# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Run server
uvicorn server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser.
Default accounts:
- `admin` / `admin123`
- `mine_operator` / `operator123`

## Running Tests
```bash
python test_integration.py
```
