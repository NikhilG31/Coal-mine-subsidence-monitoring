# Coal Mine Subsidence Monitoring System

## 1. Overview and Pipeline
The Coal Mine Subsidence Monitoring System is an IoT telemetry, geotechnical analysis, and GIS monitoring platform designed for surface subsidence detection, incline tracking, and DGMS compliance in underground coal mining operations.

### Data Pipeline
```
[ESP32 Sensor Nodes]
        │
        ▼ (ESP-NOW wireless mesh, 37 bytes)
[ESP32 Gateway]
        │
        ▼ (HTTP POST binary packet: 38 bytes)
[FastAPI Server]
        │
        ▼ (SQLAlchemy / Alembic / psycopg v3)
[PostgreSQL Database]
        │
        ▼ (REST API / Polling)
[Leaflet Web Dashboard]
```

> [!WARNING]
> **College Prototype Notice**: This system is an academic engineering prototype and is **not** a certified industrial life-safety system. It must not be used as the sole protection mechanism in hazardous or explosive mining atmospheres without formal regulatory certification.

---

## 2. Directory Map
```
coal-mine-subsidence-monitoring/
├── .env.example                                  # Template for environment configuration (placeholders only)
├── .gitignore                                    # Ignores __pycache__, .env, virtual environments, build artifacts
├── README.md                                     # System documentation, run guide, and step tracking
├── archive/                                      # Archived legacy artifacts
│   └── legacy_sqlite/                            # Unused SQLite-era scripts, CSV logs, and model trainer
│       └── README.md                             # Notice: unused SQLite files, kept for reference only
├── docs/                                         # Project architecture specifications
│   └── design-document-v2.pdf                    # Design Document (v2) system specification
├── firmware/                                     # ESP32 C firmware (unmodified wire formats)
│   ├── gateway/                                  # ESP-NOW to HTTP client bridge (Receiver_main.c)
│   └── sensor_node/                              # MPU-6050, SW160, SOS button node (Sender_main.c)
└── server/                                       # FastAPI backend server package
    ├── alembic.ini                               # Alembic migration configuration
    ├── requirements.txt                          # Production runtime dependencies (psycopg v3, PyJWT, openpyxl, FastAPI)
    ├── requirements-dev.txt                      # Development & test dependencies (-r requirements.txt, pytest, httpx)
    ├── migrations/                               # Alembic database migrations
    │   ├── env.py                                # Migration environment runner (compare_type & defaults enabled)
    │   ├── script.py.mako                        # Revision script template
    │   └── versions/                             # Schema migration versions
    │       ├── c79d6f612a5e_create_step1_tables.py # Initial 8 core PostgreSQL tables
    │       └── d84e2a87b91f_step1_hardening.py   # Server defaults, check constraint, index cleanup
    ├── app/                                      # Application package
    │   ├── config.py                             # Centralized environment variable loader (no defaults for DB)
    │   ├── database.py                           # SQLAlchemy engine, SessionLocal factory, and Base
    │   ├── main.py                               # FastAPI application factory, router registration, static mount
    │   ├── cli/                                  # Command-line tools
    │   │   └── seed_manager.py                   # Idempotent Company & Manager bootstrapping CLI
    │   ├── dev/                                  # Development-only helpers
    │   │   ├── seed_demo_data.py                 # Seeds test mine, tunnel, and simulator gateways
    │   │   └── simulator.py                      # Multi-node 38-byte ESP packet simulator
    │   ├── models/                               # SQLAlchemy ORM models (one file per table)
    │   │   ├── __init__.py                       # Package exports for all models
    │   │   ├── company.py                        # Company table and normalized name helper
    │   │   ├── gateway.py                        # Hardware gateway table
    │   │   ├── mine.py                           # Coal mine sites table
    │   │   ├── node.py                           # Sensor nodes table (1..255 range constraint)
    │   │   ├── reading.py                        # Telemetry readings table with partial unique index
    │   │   ├── tunnel.py                         # Underground mine tunnels table
    │   │   └── user.py                           # Users and UserMine association tables
    │   ├── routers/                              # FastAPI area routers (HTTP handling only)
    │   │   ├── alerts.py                         # GET /api/alerts, POST /api/alerts/{id}/ack
    │   │   ├── auth.py                           # POST /api/auth/login, GET /api/auth/me, POST /api/auth/logout
    │   │   ├── demo.py                           # /api/demo/* simulation and testing routes
    │   │   ├── export.py                         # GET /api/export.xlsx workbook export
    │   │   ├── gateways.py                       # GET /api/gateways hierarchy
    │   │   ├── health.py                         # GET /api/health monitoring endpoint
    │   │   ├── history.py                        # GET /api/history time-series queries (eager loaded)
    │   │   ├── ingest.py                         # POST /api/ingest 38-byte binary receiver
    │   │   ├── nodes.py                          # GET /api/nodes telemetry list
    │   │   ├── notifications.py                  # GET / POST /api/notifications/config
    │   │   └── placements.py                     # GET/POST /api/placements, DELETE, GET /api/unplaced
    │   ├── schemas/                              # Pydantic serialization schemas and view models
    │   │   ├── __init__.py                       # Package exports
    │   │   └── nodes.py                          # WorkerNodeView (strict leakage prevention) & SuperiorNodeView
    │   ├── security/                             # Authentication, RBAC, and cryptographic primitives
    │   │   ├── __init__.py                       # Security package exports
    │   │   ├── dependencies.py                   # FastAPI dependencies (auth, role checks, mine scoping)
    │   │   ├── passwords.py                      # PBKDF2-HMAC-SHA256 (600k iter) & legacy hash verification
    │   │   ├── rate_limit.py                     # Sliding-window IP+username login brute-force limiter
    │   │   ├── roles.py                          # Role definitions and role grouping logic
    │   │   └── tokens.py                         # HS256 JWT session tokens and cookie management
    │   ├── services/                             # Core business logic (no FastAPI imports)
    │   │   ├── hazard_classifier.py              # Pure function classifier matching firmware thresholds
    │   │   ├── ingest_service.py                 # Packet verification, deduplication, and persistence
    │   │   ├── node_serializer.py                # Telemetry dictionary serializer (superior & worker views)
    │   │   └── packet_codec.py                   # 38-byte struct parse and pack definition
    │   └── static/                               # Web dashboard client files
    │       ├── app.js                            # Leaflet and dashboard client logic
    │       ├── index.html                        # Dashboard HTML layout
    │       └── style.css                         # Neo-Brutalist dashboard styles
    └── tests/                                    # Pytest test suite (runs on test database)
        ├── conftest.py                           # TEST_DATABASE_URL fixture, migrations & table truncation
        ├── test_auth_and_roles.py                # RBAC matrix, tenant isolation, rate limit, worker leakage tests
        ├── test_classifier.py                    # Spec matrix unit tests for hazard classifier
        ├── test_dashboard_routes.py              # Dashboard API endpoints and placement workflow tests
        ├── test_ingest.py                        # Packet length, gateway check, dedup, and classification tests
        └── test_seed.py                          # seed_manager idempotency and password reset tests
```

---

## 3. Where to Look When Something Breaks

| Symptom | Location to Inspect |
|---|---|
| Node shows wrong hazard level or color |  
| Ingest packet rejected with HTTP 400  & [routers/ingest.py]
| Duplicate or missing readings; reboot counter issues 
| Database schema, constraint, or migration failure & [migrations/versions/]
| Environment variables not resolving or missing error | [config.py]
| Password verification or hashing discrepancy | [security/passwords.py]
| Dashboard map markers or inspector not updating | [static/app.js] & [routers/nodes.py] |
| Simulator fails to send packets or times out | [dev/simulator.py] |
| N+1 query slowdown on history or export | [routers/history.py] & [routers/export.py]

---

## 4. Setup and Environment Variables

Configuration is loaded from `.env` in the repository root using `python-dotenv`.

| Variable | Required | Description | Example / Default |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | PostgreSQL connection string (psycopg v3) | `postgresql+psycopg://postgres:password@localhost:5432/mine_subsidence` |
| `TEST_DATABASE_URL` | For Tests | PostgreSQL connection string for test suite (must end in `_test`) | `postgresql+psycopg://postgres:password@localhost:5432/mine_subsidence_test` |
| `SEED_COMPANY_NAME` | No | Company name for initial seed | `Bharat Coking Coal Limited` |
| `SEED_MANAGER_USERNAME` | No | Manager username for initial seed | `manager` |
| `SEED_MANAGER_PASSWORD` | No | Manager initial password (prompts if missing in tty) | (User secret) |
| `SEED_MANAGER_FULL_NAME` | No | Full display name for initial Manager | `General Manager` |
| `OFFLINE_AFTER_S` | No | Seconds of silence before a node is marked offline | `35` |
| `ENABLE_DEMO_ROUTES` | No | Controls accessibility of `/api/demo/*` endpoints | `false` (code default), `true` (.env.example) |
| `JWT_SECRET` | Phase B | Secret key for HS256 JWT tokens (>= 32 chars) | (User secret) |
| `SESSION_HOURS` | Phase B | Session lifetime in hours | `8` |
| `COOKIE_SECURE` | Phase B | Enforce HTTPS Secure flag on session cookie | `false` |
| `CORS_ORIGINS` | No | Comma-separated list of allowed CORS origins | `""` (same-origin only) |
| `ALLOW_UNKEYED_INGEST` | No | Development-only compatibility flag. When `true`, allows active gateways to ingest without `X-Gateway-Key`. Logs startup and rate-limited warnings. | `false` |
| `DEMO_GATEWAY_KEY` | Dev/Tests | Shared 256-bit API key for simulator and demo seeding (min 32 chars, no `CHANGE_ME`, min 12 distinct chars) | (User secret) |

---

## 5. How to Run

All server commands must be executed from the `server/` directory:

```bash
cd server
```

### 0. Install Dependencies
```bash
# Production runtime dependencies:
pip install -r requirements.txt

# Or development & test dependencies:
pip install -r requirements-dev.txt
```

### 1. Apply Database Migrations
```bash
alembic upgrade head
```

### 2. Seed Initial Company and Manager
```bash
python -m app.cli.seed_manager --company "Bharat Coking Coal Limited" --username "manager" --password "Manager@2026"
```
*(If `--password` is omitted in an interactive terminal, `getpass` prompts securely).*

### 3. Seed Demo Mine and Gateway Inventory (Dev Only)
```bash
python -m app.dev.seed_demo_data
```

### 4. Start Development Web Server
```bash
uvicorn app.main:app --reload
```
Dashboard is accessible at: `http://127.0.0.1:8000`.

### 5. Run Standalone Telemetry Simulator
```bash
python -m app.dev.simulator
# Or run a single burst step:
python -m app.dev.simulator --once
```

### 6. Run Automated Test Suite
Ensure `TEST_DATABASE_URL` is set in `.env` (pointing to a database whose name ends with `_test`):
```bash
python -m pytest -v
```

---

## 6. Data Model

The schema is managed strictly via Alembic migrations. The database contains only these 8 core tables:

### 1. `companies`
- `id` (`Integer`, PK, autoincrement)
- `name` (`String(255)`, nullable=False)
- `name_normalized` (`String(255)`, UNIQUE, index, nullable=False): Lowercase, trimmed, internal whitespace collapsed.
- `created_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)

### 2. `mines`
- `id` (`Integer`, PK, autoincrement)
- `company_id` (`Integer`, FK -> `companies.id` ON DELETE CASCADE, nullable=False, index)
- `name` (`String(255)`, nullable=False)
- `subsidiary` (`String(255)`, nullable=True)
- `status` (`String(32)`, nullable=False, server_default=`'active'`, check: `'active'`, `'maintenance'`, `'suspended'`, `'closed'`)
- `method` (`String(32)`, nullable=False, server_default=`'underground'`, check: `'opencast'`, `'underground'`, `'mixed'`)
- `area_ha`, `max_depth_m`, `lat`, `lon` (`Float`, nullable=True)
- **Constraint**: `UNIQUE (company_id, name)` named `uq_mines_company_name`

### 3. `tunnels`
- `id` (`Integer`, PK, autoincrement)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False, index)
- `name` (`String(255)`, nullable=False)
- `code` (`String(64)`, nullable=False)
- `depth_m` (`Float`, nullable=True)
- `status` (`String(32)`, nullable=False, server_default=`'active'`)
- **Constraint**: `UNIQUE (mine_id, code)`

### 4. `users`
- `id` (`Integer`, PK, autoincrement)
- `company_id` (`Integer`, FK -> `companies.id` ON DELETE CASCADE, nullable=False, index)
- `username` (`String(64)`, UNIQUE, index, nullable=False)
- `password_hash` (`Text`, nullable=False): PBKDF2-HMAC-SHA256 (`pbkdf2_sha256$<iter>$<salt>$<hash>`)
- `full_name` (`String(255)`, nullable=False)
- `role` (`String(32)`, nullable=False, check: `'manager'`, `'safety_officer'`, `'engineer'`, `'foreman'`, `'miner'`)
- `active` (`Boolean`, nullable=False, server_default=`true`)
- `force_pw_change` (`Boolean`, nullable=False, server_default=`false`)

### 5. `user_mines`
- `user_id` (`Integer`, FK -> `users.id` ON DELETE CASCADE, PK)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, PK)

### 6. `gateways`
- `id` (`Integer`, PK, autoincrement)
- `mac` (`String(32)`, UNIQUE, index, nullable=False): Canonical hardware MAC (`AA:BB:CC:DD:EE:FF`)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False, index)
- `name` (`String(255)`, nullable=False)
- `api_key_hash` (`Text`, nullable=True): SHA-256 hex digest of gateway 256-bit API key
- `active` (`Boolean`, nullable=False, server_default=`true`)
- `created_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `last_seen_at` (`TIMESTAMPTZ`, nullable=True)
- **Constraint**: `CHECK (mac ~ '^[0-9A-F]{2}(:[0-9A-F]{2}){5}$')` named `ck_gateways_mac_canonical`

### 7. `nodes`
- `id` (`Integer`, PK, autoincrement)
- `gateway_id` (`Integer`, FK -> `gateways.id` ON DELETE CASCADE, nullable=False, index)
- `node_no` (`SmallInteger`, nullable=False, check constraint: `node_no BETWEEN 1 AND 255`)
- `mac` (`String(32)`, nullable=True)
- `tunnel_id` (`Integer`, FK -> `tunnels.id` ON DELETE SET NULL, nullable=True, index)
- `lat`, `lon` (`Float`, nullable=True)
- `placed_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `placed_at` (`TIMESTAMPTZ`, nullable=True)
- `status` (`String(32)`, nullable=False, server_default=`'normal'`)
- `battery_pct` (`Float`, nullable=True)
- `fast_mode` (`Boolean`, nullable=False, server_default=`false`)
- `buzzer_on` (`Boolean`, nullable=False, server_default=`false`)
- `last_cmd_seq` (`Integer`, nullable=False, server_default=`0`)
- `last_seen_at` (`TIMESTAMPTZ`, nullable=True)
- **Constraint**: `UNIQUE (gateway_id, node_no)`
- **Constraint**: `CHECK ((tunnel_id IS NULL AND lat IS NULL AND lon IS NULL) OR (tunnel_id IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL))` named `ck_nodes_placement_complete`

> **Placement Definition and Completeness Rule (Single Source of Truth)**:  
> A node is **PLACED** if and only if `tunnel_id`, `lat`, and `lon` are **ALL** set. It is **UNPLACED** if all three are `NULL`. Mixed partial placement is strictly rejected at both the database level via `ck_nodes_placement_complete` and at the API layer. Furthermore, the node's assigned tunnel must belong to the exact same mine site as the node's forwarding gateway.

### 8. `readings`
- `id` (`BigInteger`, PK, autoincrement)
- `node_id` (`Integer`, FK -> `nodes.id` ON DELETE CASCADE, nullable=False)
- `ts` (`TIMESTAMPTZ`, nullable=False): Server reception timestamp
- `boot_id` (`BigInteger`, nullable=False, server_default=`0`): Boot cycle identifier
- `counter` (`BigInteger`, nullable=False): Incremental packet counter
- `tilt_x`, `tilt_y` (`Float`, nullable=False): Angular deviation in degrees
- `vib_rms` (`Float`, nullable=False): Vibration magnitude in g RMS
- `shock` (`Boolean`, nullable=False): SW160 digital shock sensor trigger
- `batt_v` (`Float`, nullable=False): Battery voltage
- `batt_pct` (`Float`, nullable=False): Battery percentage
- `node_level` (`SmallInteger`, nullable=False): Danger level reported by node firmware
- `level` (`SmallInteger`, nullable=False): Independent server hazard classification (0=normal, 1=watch, 2=warning, 3=critical)
- **Indexes**:
  - `ix_readings_node_id_ts`: `(node_id, ts DESC)`
  - `uq_readings_node_boot_counter`: `UNIQUE (node_id, boot_id, counter) WHERE boot_id <> 0` (Partial Unique Index)

### 9. `audit_log`
- `id` (`BigInteger`, PK, autoincrement)
- `ts` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `company_id` (`Integer`, FK -> `companies.id` ON DELETE CASCADE, nullable=False, index)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `action` (`String(64)`, nullable=False): Operation tag (e.g. `mine.create`, `gateway.register`, `gateway.rotate_key`, `node.place`, `user.update`)
- `entity` (`String(64)`, nullable=False): Target entity type (`mine`, `tunnel`, `gateway`, `node`, `user`)
- `entity_id` (`Integer`, nullable=True): Primary key of the affected entity
- `detail` (`JSONB`, nullable=False, server_default=`'{}'`)
- **Index**: `ix_audit_log_company_id_ts`: `(company_id, ts DESC)`
- **Immutability**: Audit logs record all administrative, security, and hardware mutations atomically with the change in the same transaction. Failed operations write nothing. Sensitive tokens, passwords, and API keys are strictly excluded from the `detail` payload.

> **Why a Partial Unique Index on readings?**  
> While firmware transmits `boot_id = 0`, node transmission counters restart from 0 after each hardware reboot. A strict non-partial unique constraint on `(node_id, boot_id, counter)` would permanently reject valid post-reboot readings that reuse earlier counter values. Therefore, the unique index applies **only** where `boot_id <> 0`. For `boot_id = 0`, deduplication is performed at the application layer with a 4-second time window rule.

---

## 7. Wire Formats

The wire formats are fixed by existing ESP32 firmware and **must not be changed**.

### A. Gateway-to-Server Payload (`POST /api/ingest`)
Packed little-endian struct without padding (`<6sBIfffBffBBBBH`), exactly **38 bytes**:

| Field | Type | Bytes | Description |
|---|---|---|---|
| `gateway_mac_address` | `uint8_t[6]` | 6 | Hardware MAC address of the forwarding gateway |
| `node_id` | `uint8_t` | 1 | Node identifier within this gateway branch (1 - 255) |
| `counter` | `uint32_t` | 4 | Packet sequence counter |
| `tiltX` | `float` | 4 | Pitch angular deviation from calibrated baseline (°) |
| `tiltY` | `float` | 4 | Roll angular deviation from calibrated baseline (°) |
| `vibration_magnitude`| `float` | 4 | MPU-6050 vibration acceleration RMS ($g$) |
| `vibration_detected` | `uint8_t` (bool) | 1 | SW160 digital shock sensor trigger |
| `battery_voltage` | `float` | 4 | Battery terminal voltage ($V$) |
| `battery_percentage` | `float` | 4 | Battery charge percentage remaining ($0 - 100\%$) |
| `sos_active` | `uint8_t` (bool) | 1 | Physical SOS button pressed on sensor node |
| `danger_level` | `uint8_t` | 1 | Node onboard computed level (0: normal, 1: watch, 2: warn, 3: crit) |
| `fast_mode` | `uint8_t` (bool) | 1 | Node reporting in fast mode (2 s interval) |
| `buzzer_on` | `uint8_t` (bool) | 1 | Node acoustic alarm buzzer active |
| `last_cmd_seq` | `uint16_t` | 2 | Sequence number of last server command applied by node |

### B. Node-to-Gateway ESP-NOW Packet (37 Bytes)
Fixed packed struct transmitted between ESP32 nodes and gateway:
```c
typedef struct __attribute__((packed)) {
    uint8_t  mac_address[6];       // Node hardware MAC address
    uint32_t counter;              // Packet sequence counter
    float    tiltX;                // Pitch angle deviation (degrees)
    float    tiltY;                // Roll angle deviation (degrees)
    float    vibration_magnitude;  // Vibration RMS (g)
    bool     vibration_detected;   // SW160 digital shock trigger
    float    battery_voltage;      // Battery cell voltage
    float    battery_percentage;   // Battery percentage
    bool     sos_active;           // SOS button flag
    uint8_t  danger_level;         // Node danger level (0-3)
    bool     fast_mode;            // Fast transmission mode (2s)
    bool     buzzer_on;            // Buzzer active
    uint16_t last_cmd_seq;         // Last server command acked
} espnow_rx_payload_t;
```

### C. Gateway-to-Node Command Packet (10 Bytes)
Radio command sent by gateway over ESP-NOW:
- `magic` (`uint8_t`, 1 byte): `0xA5`
- `cmd` (`uint8_t`, 1 byte): `1` (BUZZER_ON), `2` (BUZZER_OFF), `3` (RESUME_NORMAL)
- `target_mac` (`uint8_t[6]`, 6 bytes): Destination node MAC
- `seq` (`uint16_t`, 2 bytes): Server-assigned sequence number (1 - 65535)

---

## 8. API Reference

### Implemented Routes

| Method | Path | Allowed Callers | Request | Response | Status Codes |
|---|---|---|---|---|---|
| `POST` | `/api/ingest` | ESP32 Gateways | Body: Exactly 38-byte binary packet. Header: `X-Gateway-Key` (required unless `ALLOW_UNKEYED_INGEST=true`). `X-Gateway-Id` is ignored. | Empty body | `200`, `400`, `401` |
| `GET` | `/api/health` | Public | None | `{"status": "healthy", "database": "PostgreSQL", "payload_size": 38}` | `200` |
| `POST` | `/api/auth/login` | Public | JSON: `{"username": "...", "password": "..."}` | Sets HttpOnly cookie `session`. Body: `{"token": "...", "user": {...}}` | `200`, `400`, `401`, `429` |
| `GET` | `/api/auth/me` | Any Authenticated User | None (requires `session` cookie; works during force_pw_change) | `{"user": {...}, "allowed_mine_ids": [...]}` | `200`, `401` |
| `POST` | `/api/auth/logout` | Public | None (always returns 200 and clears `session` cookie) | `{"message": "Logged out"}` | `200` |
| `POST` | `/api/auth/change-password` | Any Authenticated User | JSON: `{"current_password": "...", "new_password": "..."}` | Clears `force_pw_change`, records audit. Body: `{"message": "..."}` | `200`, `400`, `401` |
| `GET` | `/api/mines` | Any Role (Scoped) | Scoped to permitted mines. | `{"mines": [...]}` | `200`, `401` |
| `POST` | `/api/mines` | Manager Only | JSON: `{"name", "subsidiary", "status", "method", "lat", "lon", "area_ha", "max_depth_m"}`. Records audit, auto-assigns creator. | Created mine object | `200`, `400`, `401`, `403`, `409` |
| `GET` | `/api/mines/{id}/tunnels` | Any Role (Scoped) | Scoped to permitted mines. | `{"tunnels": [...]}` | `200`, `401`, `404` |
| `POST` | `/api/mines/{id}/tunnels` | Manager Only | JSON: `{"name", "code", "depth_m", "status"}`. Records audit. | Created tunnel object | `200`, `400`, `401`, `403`, `404`, `409` |
| `POST` | `/api/mines/{id}/gateways` | Manager Only | JSON: `{"name", "mac"}`. Generates 256-bit key, records audit. Header `Cache-Control: no-store`. | `{"id", "name", "mac", "active", "mine_id", "api_key"}` | `200`, `400`, `401`, `403`, `404`, `409` |
| `GET` | `/api/mines/{id}/gateways` | Manager Only | Path param. Excludes key and hash. | `{"gateways": [...]}` | `200`, `401`, `403`, `404` |
| `PATCH` | `/api/gateways/{id}` | Manager Only | JSON: `{"name", "active"}`. Records audit. | Updated gateway object (no key) | `200`, `400`, `401`, `403`, `404` |
| `POST` | `/api/gateways/{id}/rotate-key` | Manager Only | Path param. Generates new key, updates hash, records audit. Header `Cache-Control: no-store`. | `{"id", "name", "mac", "active", "mine_id", "api_key"}` | `200`, `401`, `403`, `404` |
| `DELETE` | `/api/gateways/{id}` | Manager Only | Path param. Only succeeds if gateway has zero nodes. Records audit. | `{"message": "Gateway deleted successfully"}` | `200`, `401`, `403`, `404`, `409` |
| `GET` | `/api/nodes` | Any Role (Scoped) | Query: `gateway` (optional), `mine_id` (optional). Scoped to permitted mines.<br>• **Workers**: `WorkerNodeView` (stripped).<br>• **Superiors**: `SuperiorNodeView`. | `{"now": float, "nodes": [...]}` | `200`, `401`, `404` |
| `GET` | `/api/placements` | Any Role (Scoped) | Query: `mine_id` (optional). Scoped to permitted mines.<br>• **Workers**: `WorkerPlacementView`.<br>• **Superiors**: `SuperiorPlacementView`. | `{"placements": [...]}` | `200`, `401`, `404` |
| `GET` | `/api/unplaced` | Superiors Only | Query: `mine_id` (optional). Scoped to permitted mines. | `{"unplaced": [...]}` | `200`, `401`, `403`, `404` |
| `PATCH` | `/api/nodes/{id}/placement` | Superiors Only | JSON: `{"tunnel_id", "lat", "lon"}`. Validates tunnel belongs to gateway's mine. Records audit. | Serialized node object | `200`, `400`, `401`, `403`, `404` |
| `DELETE` | `/api/nodes/{id}/placement` | Superiors Only | Sets `tunnel_id`, `lat`, `lon`, `placed_by`, `placed_at` to `NULL`. Records audit. | Serialized node object | `200`, `401`, `403`, `404` |
| `GET` | `/api/gateways` | Superiors Only | Query: `mine_id` (optional). Scoped to permitted mines. | `{"now": float, "gateways": [...]}` | `200`, `401`, `403`, `404` |
| `GET` | `/api/history` | Superiors Only | Query: `gateway`, `node_id`, `limit`, `only_alerts`. Permitted mines only. | `{"readings": [...]}` | `200`, `401`, `403`, `404` |
| `GET` | `/api/export.xlsx` | Superiors Only | Query: `gateway`, `node_id`. Permitted mines only. | Excel binary spreadsheet (.xlsx) | `200`, `401`, `403`, `404` |
| `GET` | `/api/alerts` | Superiors Only | Scoped to permitted mines. | `{"alerts": []}` | `200`, `401`, `403` |
| `POST` | `/api/alerts/{alert_id}/ack` | Superiors Only | Path param. Permitted mines only. | `{"message": "Alert acknowledged", "id": ...}` | `200`, `401`, `403`, `404` |
| `GET` | `/api/notifications/config` | Superiors Only | Scoped to user's company. | `{"config": {}}` | `200`, `401`, `403` |
| `POST` | `/api/notifications/config` | Superiors Only | JSON: channel settings. Scoped to user's company. | `{"message": "Notification configurations updated"}` | `200`, `401`, `403` |
| `POST` | `/api/notifications/test` | Superiors Only | None | `{"detail": "Notification testing will be available in Step 10"}` | `501`, `401`, `403` |
| `GET` | `/api/users` | Manager Only | Company tenant isolated. | `{"users": [...]}` | `200`, `401`, `403` |
| `POST` | `/api/users` | Manager Only | JSON: `{"username", "full_name", "role", "mine_ids", "initial_password"}`. Records audit. | Created user object | `200`, `400`, `401`, `403`, `404`, `409` |
| `PATCH` | `/api/users/{user_id}` | Manager Only | JSON: `{"role", "active", "mine_ids", "reset_password", "full_name"}`. Records audit. | Updated user object | `200`, `400`, `401`, `403`, `404` |
| `DELETE` | `/api/users/{user_id}` | Manager Only | Path param. Soft-deactivates user (`active=false`). Records audit. | `{"message": "User deactivated successfully"}` | `200`, `400`, `401`, `403`, `404` |
| `POST` | `/api/demo/seed` | Manager Only | Returns 404 when `ENABLE_DEMO_ROUTES=false`. Seeds mine, tunnel, and simulator gateways with `DEMO_GATEWAY_KEY`. | `{"message": "Demo coal mine datasets seeded"}` | `200`, `401`, `403`, `404` |
| `POST` | `/api/demo/step` | Manager Only | Returns 404 when `ENABLE_DEMO_ROUTES=false`. | `{"message": "Single simulated step generated"}` | `200`, `401`, `403`, `404` |
| `POST` | `/api/demo/toggle-live`| Manager Only | JSON (optional): `{"enable": bool}`. Returns 404 when `ENABLE_DEMO_ROUTES=false`. | `{"running": bool}` | `200`, `401`, `403`, `404` |
| `GET` | `/api/demo/status` | Any Authenticated User | Returns 404 when `ENABLE_DEMO_ROUTES=false`. | `{"running": bool}` | `200`, `401`, `404` |
| `POST` | `/api/demo/trigger-hazard` | Manager Only | JSON: `{"gateway_mac_address", "node_id", "level"}`. 404 when demo disabled. | `{"message": "..."}` | `200`, `401`, `403`, `404` |
| `GET` | `/` | Browser | None | HTML dashboard (`index.html`) | `200` |

### Planned Routes (Future Steps)
- `GET /api/mines/{id}/profile` (*Planned - Step 9*)
- `POST /api/nodes/{id}/commands` (*Planned - Step 7*)
- `POST /api/assignments` (*Planned - Step 5*)
- `POST /api/checkins` (*Planned - Step 6*)
- `POST /api/incidents` (*Planned - Step 7*)
- `POST /api/worker-reports` (*Planned - Step 8*)

---

## 9. Ingest Behaviour and Gateway Security

### Ingest Authentication Pipeline (`POST /api/ingest`)
1. **Payload Length & Format Enforcement**:
   The ingest endpoint verifies `len(body) == 38` and parses the binary packet with `<6sBIfffBffBBBBH`. Invalid packet sizes or unparseable formats immediately return HTTP 400.
2. **Gateway Resolution & MAC Extraction**:
   Extracts `gateway_mac` directly from the validated binary packet body. Resolves the gateway row in PostgreSQL.
3. **Gateway Authentication & Key Verification**:
   - Requires header `X-Gateway-Key`.
   - The key is verified against the stored SHA-256 hex digest (`api_key_hash`) using constant-time `hmac.compare_digest`.
   - **Unified 401 Rejection**: EVERY authentication failure (missing key, incorrect key, unknown gateway MAC, inactive gateway, or NULL/placeholder key hash) strictly returns HTTP 401 `{"detail": "Gateway authentication failed"}` and logs a WARNING with MAC, client IP, and specific reason. The secret key is never logged.
   - Legacy header `X-Gateway-Id` is ignored entirely.
4. **Dev Compatibility Mode (`ALLOW_UNKEYED_INGEST`)**:
   When set to `true` in `.env`, active gateways with valid hashes may ingest telemetry without the `X-Gateway-Key` header (to support un-reflashed hardware during testing). A loud warning is emitted at server startup and rate-limited to at most once per hour per gateway. If a wrong key is sent, HTTP 401 is still returned.
5. **Node ID Validation**:
   `node_id == 0` is strictly rejected with HTTP 400 (valid range is 1..255).
6. **Field Mapping**:
   Unpacks `<6sBIfffBffBBBBH` into:
   - `tiltX -> tilt_x`
   - `tiltY -> tilt_y`
   - `vibration_magnitude -> vib_rms`
   - `vibration_detected -> shock`
   - `battery_voltage -> batt_v`
   - `battery_percentage -> batt_pct`
   - `danger_level -> node_level`
   - `ts` is assigned server reception time (timestamptz UTC).
   - `boot_id` is stored as `0`.
7. **Independent Server Hazard Classification**:
   The server determines hazard level independently using pure DGMS threshold rules:
   - `tilt = max(|tilt_x|, |tilt_y|)`.
   - Thresholds:
     - Tilt: `< 0.5°` (0: Normal), `0.5° - 1.0°` (1: Watch), `1.0° - 2.0°` (2: Warning), `>= 2.0°` (3: Critical).
     - Vibration RMS: `< 0.05g` (0: Normal), `0.05 - 0.15g` (1: Watch), `0.15 - 0.30g` (2: Warning), `>= 0.30g` (3: Critical).
   - `level = max(tilt_level, vib_level)`.
   - The SW160 shock flag is recorded and displayed, but does not alter hazard level.
   - Node transmitted `danger_level` is stored only as `node_level` for auditing.
8. **Deduplication Rule (`boot_id = 0`)**:
   A reading is classified as a duplicate **only if** a row already exists in `readings` with the same `(node_id, boot_id = 0, counter)` **AND** `|now - existing.ts| < 4.0s`.
   - If duplicate: reading insertion is skipped, node metadata is refreshed, and HTTP 200 with an empty body is returned.
   - If delta $\ge 4.0\text{s}$: classified as a post-reboot reading and inserted into `readings`.
9. **Node Auto-Creation & State Updating**:
   Nodes are discovered and created on the first packet received by their gateway, with `tunnel_id`, `lat`, and `lon` set to `NULL` (unplaced queue). Every packet updates `nodes.last_seen_at`, `battery_pct`, `status`, `fast_mode`, `buzzer_on`, and `last_cmd_seq`.
10. **Response**:
   Returns HTTP 200 with an empty body.

---

## 9.1. Registering and Flashing a Gateway

### End-to-End Gateway Provisioning Workflow:
1. **Register in UI**:
   - Manager logs in and clicks **⛏️ Mines & Gateways** in the masthead.
   - Under the target mine, click **+ Add Gateway** and enter a human-readable name and canonical MAC (`AA:BB:CC:DD:EE:FF`).
2. **Save API Key (One-Time Display)**:
   - The server generates a random 256-bit token (`gwk_` + 32 urlsafe bytes) and stores only its SHA-256 hex digest.
   - The modal displays the plaintext API key **once** with `Cache-Control: no-store` headers.
   - Click **📋 Copy to Clipboard** and verify you have recorded it.
3. **Configure Gateway Hardware / Simulator**:
   - In ESP32 gateway C firmware (`firmware/gateway/Receiver_main.c` or build config), set header `X-Gateway-Key: <key>`.
   - For local development simulation, set `DEMO_GATEWAY_KEY=<key>` in `.env` or use `seed_demo_data`.
4. **Key Rotation**:
   - If a key is compromised, Manager clicks **Rotate Key** on the gateway.
   - The server immediately invalidates previous keys and generates a new one shown once. Old keys are rejected with HTTP 401.
5. **Security Notice (Plain HTTP Prototype)**:
   - In this college engineering prototype, telemetry travels over plain HTTP. Keys and passwords are cryptographically hashed in database storage and scrubbed from application logs, but are not encrypted over the local LAN/wire. Production deployment requires HTTPS reverse proxy termination (see HTTPS backlog item).

---

## 10. Build Progress

| Step | Scope | Status | Done When Condition | Date Completed |
|---|---|---|---|---|
| **Step 1** | PostgreSQL migration and seed | **Done** | The simulator posts packets, rows land in PostgreSQL, the existing dashboard shows live nodes and values exactly as before, and re-running the migration and seed on a fresh database works. | 2026-11-22 |
| **Step 1.5** | Restructure and Hardening | **Done** | Modular architecture, strict 38-byte codec, pure hazard classifier matching firmware, eager loading for N+1 queries, psycopg v3 driver only, isolated test database suite. | 2026-11-22 |
| **Step 2** | Login and roles | **Done** | Users table, login, require_role, company and mine scoping. A worker token gets 403 on every superior route. Worker view strictly prevents field leakage. | 2026-11-22 |
| **Step 2.5** | Hardening and Secret Policies | **Done** | Fail-fast secret validation at startup, centralized password policy, non-blocking handlers, data-driven route policy matrix, worker placement segregation, distinct_on query extraction. | 2026-11-22 |
| **Step 3** | Mines, tunnels, gateways and node placement | **Done** | A brand-new Manager can go from an empty account to a placed node carrying a tunnel, entirely through the app: log in, change password, create a mine, add a tunnel, register a gateway (key shown once), see the device appear in the Unplaced queue after it posts with that key, and place it on the map with a tunnel. | 2026-11-22 |
| **Step 4** | Watchdog and offline detection | *Not started* | ESP watchdog, sensor-fault flag, gateway heartbeat, server last-seen check, gateway-offline alert. Unplugging a node shows it offline and fires one alert. | — |
| **Step 8 (SOS path)** | Worker reports and node SOS | *Not started* | Report form, inbox, status flow, node SOS button and SOS packet. SOS packet creates an incident and alert within a few seconds. | — |

---

## 11. Known Limitations and Deviations

1. **Pre-Step-3 Dev Gateways Key Invalidation**:
   Gateways seeded in earlier steps with `placeholder_dev_api_key_hash` cannot ingest telemetry until their API key is rotated via `POST /api/gateways/{id}/rotate-key` or re-seeded with `DEMO_GATEWAY_KEY`.
2. **Tunnel Scoping in Step 2/3**:
   In Step 3, miners and foremen see nodes across all mines assigned to them in `user_mines`. Tunnel-level assignment filtering (`assignments` table and active shift checks) belongs to Step 5.
3. **In-Memory Rate Limiting**:
   Failed login attempts are rate limited in-memory (5 failures per username + IP sliding window over 15 minutes returning HTTP 429 with `Retry-After`). In a multi-worker production deployment, this will be backed by Redis.
4. **Commands and SOS Alerting**:
   Ingest parses `sos_active` and returns an empty response body without dispatching commands or triggering external webhooks (slated for later steps).

---

## 12. Changelog

### 2026-11-22 - Step 3: Mines, Tunnels, Gateways, Node Placement, and Audit Logging
- **Database Migration (`e95f3b721a01`)**:
  - Created `audit_log` table with `id`, `ts`, `company_id`, `user_id`, `action`, `entity`, `entity_id`, and `detail` (JSONB) with compound index `(company_id, ts DESC)`.
  - Added unique constraint `uq_mines_company_name` on `mines(company_id, name)`.
  - Added regex CHECK constraint `ck_gateways_mac_canonical` on `gateways(mac)`.
  - Repaired incomplete node placements and enforced CHECK constraint `ck_nodes_placement_complete` on `nodes`: `(tunnel_id IS NULL AND lat IS NULL AND lon IS NULL) OR (tunnel_id IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL)`.
  - Tested migration downgrade `-1` and upgrade `head` cleanly with zero drift on `alembic check`.
- **Gateway Keys and Ingest Authentication**:
  - Implemented cryptographic key lifecycle in `services/gateway_keys.py`: `generate_key()` returns 256-bit `gwk_` tokens; `hash_key()` generates SHA-256 hex digests; `verify_key()` performs constant-time verification.
  - Enforced `X-Gateway-Key` on `POST /api/ingest`. Any authentication failure (missing key, wrong key, unknown MAC, inactive gateway, placeholder hash) uniformly returns HTTP 401 with warning logging.
  - Added compatibility flag `ALLOW_UNKEYED_INGEST` with loud startup warning and hourly rate-limited warning per gateway.
  - Updated simulator and demo seed to require and authenticate with `DEMO_GATEWAY_KEY`.
- **Mine, Tunnel, Gateway, and Placement Routes**:
  - Added `POST /api/mines`, `GET /api/mines`, `POST /api/mines/{id}/tunnels`, `GET /api/mines/{id}/tunnels`.
  - Added gateway provisioning (`POST /api/mines/{id}/gateways`, `GET /api/mines/{id}/gateways`, `PATCH /api/gateways/{id}`, `POST /api/gateways/{id}/rotate-key`, `DELETE /api/gateways/{id}`) with one-time key display and `Cache-Control: no-store`.
  - Replaced legacy placements endpoints with `PATCH /api/nodes/{id}/placement` (enforcing same-mine validation) and `DELETE /api/nodes/{id}/placement`.
  - Added optional `?mine_id=` query filter across `/api/nodes`, `/api/gateways`, `/api/placements`, and `/api/unplaced`.
- **Atomic Audit Logging**:
  - Built `services/audit.py` recording audit rows atomically for all state mutations (`mine.create`, `tunnel.create`, `gateway.register`, `gateway.update`, `gateway.rotate_key`, `gateway.delete`, `node.place`, `node.unplace`, `user.create`, `user.update`, `user.delete`, `user.change_password`).
  - Enforced automatic sanitization stripping passwords, tokens, and keys from audit details.
- **Frontend Enhancements**:
  - Created 4-step Manager onboarding wizard for accounts with empty mine scopes.
  - Added restricted "No mines assigned" modal for subordinate roles.
  - Built Manager-only "Mines, Tunnels & Gateways" management panel.
  - Added One-Time Gateway Key modal with clipboard copy button.
  - Added required underground tunnel picker before interactive map placement.
  - Scoped UI controls dynamically based on worker vs superior roles.
- **Automated Verification**:
  - Added `tests/test_step3_phase_b.py` with 5 comprehensive test suites covering ingest authentication matrices, key safety, placement check constraints, cross-mine validation, audit logging atomicity, and the complete Done-When manager lifecycle journey. All 42 tests passing.

---

## 12. Changelog

### 2026-11-22 - Step 2.5: Hardening
- **Secret & Password Policy Hardening**:
  - Implemented fail-fast JWT secret validation in `config.py` enforced at application startup in `main.py` (>= 32 chars, >= 12 distinct characters, case-insensitive rejection of `CHANGE_ME`).
  - Added centralized password policy in `security/password_policy.py` (`validate_new_password` enforcing min 10 chars, username mismatch, and `CHANGE_ME` rejection) shared across `auth/change-password`, `users` create/reset, and `seed_manager`.
  - Added secure secret generation guidance in `.env.example`.
- **Event Loop & Concurrency Protection**:
  - Converted `api_login`, `api_change_password`, and `api_trigger_hazard` to standard synchronous `def` handlers dispatched in threadpool, eliminating 14-second event loop deadlocks during concurrent HTTP bursts.
  - Hardened `/api/ingest` to execute packet parsing and database persistence asynchronously via `starlette.concurrency.run_in_threadpool` with dedicated short-lived database sessions.
- **Data Leakage & Schema Tightening**:
  - Added strict `WorkerPlacementView` and `SuperiorPlacementView` with `extra="forbid"` on `GET /api/placements`, preventing gateway MAC and admin identifiers from leaking to workers (`foreman`, `miner`).
  - Removed `dict` from `NodesResponse` union schema to fail loudly on unexpected data shapes.
  - Made `POST /api/auth/logout` public and unconditional, always clearing session cookies and returning HTTP 200 without requiring active session tokens.
- **Architectural Modularity**:
  - Split `server/app/routers/auth.py` by moving `/api/users` manager administration endpoints to dedicated `routers/users.py`.
  - Extracted shared telemetry query to `services/latest_readings.py` using non-deprecated PostgreSQL `distinct_on` extension, eliminating `SADeprecationWarning`.
  - Split requirements into `requirements.txt` (runtime) and `requirements-dev.txt` (development/testing).
- **Automated Verification**:
  - Rewrote RBAC matrix test in `tests/test_auth_and_roles.py` into a data-driven policy engine derived directly from `POLICY_TABLE` covering all callers (anonymous + 5 roles) across all endpoints.
  - Added integration latency test verifying `POST /api/demo/trigger-hazard` completes in under 5 seconds against a running server.

### 2026-11-22 - Step 2: Login and RBAC Roles
- **Authentication & Session Tokens**:
  - Implemented HS256 JWT sessions stored in HttpOnly, SameSite=Strict cookies (`session`).
  - Added password hashing with PBKDF2-HMAC-SHA256 (600,000 iterations, 16-byte cryptographically secure random salt).
  - Backward compatibility for legacy `<salt>$<hash>` format (100,000 iterations) with transparent automatic rehash upon successful authentication.
  - Constant-time dummy hash on non-existent usernames to mitigate timing side-channel attacks.
  - In-memory rate limiting for login attempts (5 failures / 15 min per username + client IP, returning 429 with `Retry-After`).
  - Forced password change workflow: new users must change their password on first login via `POST /api/auth/change-password`; all other non-auth endpoints return 403 `PASSWORD_CHANGE_REQUIRED`.
- **Role-Based Access Control (RBAC) & Scoping**:
  - Implemented role groups: Workers (`miner`, `foreman`) and Superiors (`safety_officer`, `engineer`, `manager`).
  - Protected all sensitive endpoints (`/api/gateways`, `/api/history`, `/api/export.xlsx`, `/api/alerts`, `/api/unplaced`, `/api/placements` mutations) to require superior roles (403 for workers).
  - Tenant and mine scoping: Managers access all company mines; other users access only assigned mines (`user_mines`). Multi-tenant isolation returns 404 for resources belonging to another company.
  - Segregated node view serialization: `WorkerNodeView` uses Pydantic with `extra="forbid"` to strictly prevent leakage of gateway MAC, counter, raw scores, reasons, and placement metadata to miners and foremen.
- **User Management & Hierarchy Protections**:
  - Added Manager-only user management endpoints (`GET/POST /api/users`, `PATCH/DELETE /api/users/{id}`).
  - Managers cannot create managers, cannot demote/deactivate their own account, and cannot deactivate the company's last active manager.
- **Frontend Hardening**:
  - Removed `localStorage` token storage in favor of HttpOnly cookie session handling (`credentials: "same-origin"`).
  - Added modal for forced password changes (`#changePasswordModal`).
  - Implemented user creation modal for managers (`#addUserModal`) with automatic initial password presentation.
  - Scoped UI controls dynamically based on user role (e.g. hiding Place Node, Unplace, Demo, History, Export for workers).
- **Automated Verification**:
  - Created comprehensive RBAC test suite (`tests/test_auth_and_roles.py`) with 12 tests covering route policy compliance, role matrices, company tenant isolation, mine scoping, force password change, user deactivation, JWT expiry, rate limiting, and field leakage. All 32 test cases passing.

### 2026-11-22 - Step 1.5: Restructure and Hardening
- **Restructured Repository**:
  - Reorganized project into modular `server/app/` layout: `config.py`, `database.py`, `models/` (one file per table), `routers/` (one file per area), `services/` (pure business logic), `cli/`, `dev/`, and `tests/`.
  - Moved legacy SQLite files to `archive/legacy_sqlite/`.
  - Moved firmware directories to `firmware/sensor_node/` and `firmware/gateway/` without touching code content.
  - Moved specification document to `docs/design-document-v2.pdf`.
- **Hardened Data Layer & Migrations**:
  - Consolidated on `psycopg` (v3) driver and removed `psycopg2-binary`.
  - Added new Alembic migration `d84e2a87b91f_step1_hardening.py` with server defaults for all NOT NULL columns having Python-side defaults, dropped redundant index `ix_readings_node_id`, and added CHECK constraint `node_no BETWEEN 1 AND 255`.
  - Verified autogenerate produces an empty diff with `compare_type=True` and `compare_server_default=True`.
- **Classifier Alignment & Optimization**:
  - Built pure rule-based hazard classifier in `services/hazard_classifier.py` matching firmware thresholds (tilt: 0.5/1.0/2.0°, vibration RMS: 0.05/0.15/0.30g, tilt = max(|tilt_x|, |tilt_y|)).
  - Discarded external ML joblib loading from classification path.
  - Resolved N+1 queries in `history.py` and `export.py` using eager `joinedload`.
  - Enforced HTTP 400 for `node_id == 0`.
- **Test Suite**:
  - Replaced legacy tests with pytest suite in `server/tests/` running against isolated test database (`_test` suffix enforced).
