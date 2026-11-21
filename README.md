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
    ├── requirements.txt                          # Python dependencies (psycopg v3, PyJWT, pytest, httpx)
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
    │   ├── security/                             # Authentication and cryptographic primitives
    │   │   ├── __init__.py                       # Security package exports
    │   │   └── passwords.py                      # PBKDF2-HMAC-SHA256 (600k iter) & legacy hash verification
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
        ├── test_classifier.py                    # Spec matrix unit tests for hazard classifier
        ├── test_dashboard_routes.py              # Dashboard API endpoints and placement workflow tests
        ├── test_ingest.py                        # Packet length, gateway check, dedup, and classification tests
        └── test_seed.py                          # seed_manager idempotency and password reset tests
```

---

## 3. Where to Look When Something Breaks

| Symptom | Location to Inspect |
|---|---|
| Node shows wrong hazard level or color | [services/hazard_classifier.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/services/hazard_classifier.py) |
| Ingest packet rejected with HTTP 400 | [services/packet_codec.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/services/packet_codec.py) & [routers/ingest.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/routers/ingest.py) |
| Duplicate or missing readings; reboot counter issues | [services/ingest_service.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/services/ingest_service.py) |
| Database schema, constraint, or migration failure | [models/](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/models/) & [migrations/versions/](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/migrations/versions/) |
| Environment variables not resolving or missing error | [config.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/config.py) |
| Password verification or hashing discrepancy | [security/passwords.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/security/passwords.py) |
| Dashboard map markers or inspector not updating | [static/app.js](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/static/app.js) & [routers/nodes.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/routers/nodes.py) |
| Simulator fails to send packets or times out | [dev/simulator.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/dev/simulator.py) |
| N+1 query slowdown on history or export | [routers/history.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/routers/history.py) & [routers/export.py](file:///c:/Nikhil/Mine%20Subsidence%20Monitoring/server/app/routers/export.py) |

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

---

## 5. How to Run

All server commands must be executed from the `server/` directory:

```bash
cd server
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
- `api_key_hash` (`Text`, nullable=True)
- `active` (`Boolean`, nullable=False, server_default=`true`)
- `created_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `last_seen_at` (`TIMESTAMPTZ`, nullable=True)

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
| `POST` | `/api/ingest` | ESP32 Gateway | Body: Exactly 38-byte binary packet. Header: `X-Gateway-Id` (optional) | Empty body | `200`, `400` |
| `GET` | `/api/nodes` | All (Dashboard) | Query: `gateway` (optional) | `{"now": float, "nodes": [...]}` | `200` |
| `GET` | `/api/gateways` | All (Dashboard) | None | `{"now": float, "gateways": [...]}` | `200` |
| `GET` | `/api/history` | All (Dashboard) | Query: `gateway`, `node_id`, `limit`, `only_alerts` | `{"readings": [...]}` | `200` |
| `GET` | `/api/placements` | All (Dashboard) | None | `{"placements": [...]}` | `200` |
| `POST` | `/api/placements` | All (Dashboard) | JSON: `{"gateway_mac_address", "node_id", "latitude", "longitude"}` | `{"message", "node_id", "gateway_mac"}` | `200`, `400`, `404` |
| `DELETE` | `/api/placements/{gateway}/{node_id}` | All (Dashboard) | Path parameters | `{"message": "Node unplaced from map"}` | `200` |
| `GET` | `/api/unplaced` | All (Dashboard) | None | `{"unplaced": [...]}` | `200` |
| `GET` | `/api/health` | Public | None | `{"status": "healthy", "database": "PostgreSQL", ...}` | `200` |
| `GET` | `/api/export.xlsx` | All (Dashboard) | Query: `gateway`, `node_id` | Excel binary spreadsheet (.xlsx) | `200` |
| `GET` | `/api/auth/me` | All | None | `{"user": {...}, "allowed_gateways": null}` | `200` |
| `POST` | `/api/auth/login` | All | JSON: credentials | `{"token": "...", "user": {...}}` | `200` |
| `POST` | `/api/auth/logout` | All | None | `{"message": "Logged out"}` | `200` |
| `GET` | `/api/users` | All | None | `{"users": [...]}` | `200` |
| `POST` | `/api/users` | All | JSON: user fields | `{"message": "User added successfully"}` | `200` |
| `GET` | `/api/alerts` | All (Dashboard) | None | `{"alerts": []}` | `200` |
| `POST` | `/api/alerts/{alert_id}/ack` | All | Path parameter | `{"message": "Alert acknowledged", "id": ...}` | `200` |
| `GET` | `/api/notifications/config` | All | None | `{"config": {}}` | `200` |
| `POST` | `/api/notifications/config` | All | JSON: channel settings | `{"message": "Notification configurations updated"}` | `200` |
| `POST` | `/api/demo/seed` | All (Dashboard) | None | `{"message": "Demo coal mine datasets seeded"}` | `200` |
| `POST` | `/api/demo/step` | All (Dashboard) | None | `{"message": "Single simulated step generated"}` | `200` |
| `POST` | `/api/demo/toggle-live`| All (Dashboard)| JSON (optional): `{"enable": bool}` | `{"running": bool}` | `200` |
| `GET` | `/api/demo/status` | All (Dashboard) | None | `{"running": bool}` | `200` |
| `POST` | `/api/demo/trigger-hazard` | All (Dashboard) | JSON: `{"gateway_mac_address", "node_id", "level"}` | `{"message": "..."}` | `200` |
| `GET` | `/` | Browser | None | HTML dashboard (`index.html`) | `200` |

### Planned Routes (Future Steps)
- `POST /api/mines` (*Planned - Step 3*)
- `GET /api/mines/{id}/profile` (*Planned - Step 9*)
- `POST /api/mines/{id}/gateways` (*Planned - Step 3*)
- `POST /api/gateways/{id}/rotate-key` (*Planned - Step 3*)
- `POST /api/nodes/{id}/commands` (*Planned - Step 7*)
- `POST /api/assignments` (*Planned - Step 5*)
- `POST /api/checkins` (*Planned - Step 6*)
- `POST /api/incidents` (*Planned - Step 7*)
- `POST /api/worker-reports` (*Planned - Step 8*)

---

## 9. Ingest Behaviour

1. **Strict 38-Byte Check**:
   The ingest endpoint verifies `len(body) == 38`. Any payload of different length is rejected immediately with HTTP 400.
2. **Node ID Validation**:
   `node_id == 0` is strictly rejected with HTTP 400 (valid range is 1..255).
3. **Field Mapping**:
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
4. **Independent Server Hazard Classification**:
   The server determines hazard level independently using pure DGMS threshold rules:
   - `tilt = max(|tilt_x|, |tilt_y|)`.
   - Thresholds:
     - Tilt: `< 0.5°` (0: Normal), `0.5° - 1.0°` (1: Watch), `1.0° - 2.0°` (2: Warning), `>= 2.0°` (3: Critical).
     - Vibration RMS: `< 0.05g` (0: Normal), `0.05 - 0.15g` (1: Watch), `0.15 - 0.30g` (2: Warning), `>= 0.30g` (3: Critical).
   - `level = max(tilt_level, vib_level)`.
   - The SW160 shock flag is recorded and displayed, but does not alter hazard level.
   - Node transmitted `danger_level` is stored only as `node_level` for auditing.
5. **Deduplication Rule (`boot_id = 0`)**:
   A reading is classified as a duplicate **only if** a row already exists in `readings` with the same `(node_id, boot_id = 0, counter)` **AND** `|now - existing.ts| < 4.0s`.
   - If duplicate: reading insertion is skipped, node metadata is refreshed, and HTTP 200 with an empty body is returned.
   - If delta $\ge 4.0\text{s}$: classified as a post-reboot reading and inserted into `readings`.
6. **Node Auto-Creation**:
   Nodes are discovered and created on the first packet received by their gateway, with `tunnel_id`, `lat`, and `lon` set to `NULL` (unplaced queue).
7. **Node State Updating**:
   Every packet updates `nodes.last_seen_at`, `battery_pct`, `status`, `fast_mode`, `buzzer_on`, and `last_cmd_seq`.
8. **Response**:
   Returns HTTP 200 with an empty body.

---

## 10. Build Progress

| Step | Scope | Status | Done When Condition | Date Completed |
|---|---|---|---|---|
| **Step 1** | PostgreSQL migration and seed | **Done** | The simulator posts packets, rows land in PostgreSQL, the existing dashboard shows live nodes and values exactly as before, and re-running the migration and seed on a fresh database works. | 2026-11-22 |
| **Step 1.5** | Restructure and Hardening | **Done** | Modular architecture, strict 38-byte codec, pure hazard classifier matching firmware, eager loading for N+1 queries, psycopg v3 driver only, isolated test database suite. | 2026-11-22 |
| **Step 2** | Login and roles | *In Progress* | Users table, login, require_role, company and mine scoping. A worker token gets 403 on every superior route. | — |
| **Step 3** | Mines, tunnels, gateways and node placement | *Not started* | Onboarding screen, create mine, add tunnels, register gateway (key shown once), tunnel picker on Place on Map with same-mine check. | — |
| **Step 4** | Watchdog and offline detection | *Not started* | ESP watchdog, sensor-fault flag, gateway heartbeat, server last-seen check, gateway-offline alert. Unplugging a node shows it offline and fires one alert. | — |
| **Step 8 (SOS path)** | Worker reports and node SOS | *Not started* | Report form, inbox, status flow, node SOS button and SOS packet. SOS packet creates an incident and alert within a few seconds. | — |

---

## 11. Known Limitations and Deviations

1. **Interim `X-Gateway-Id` Header**:
   As required for Step 1 compatibility, the gateway check resolves the gateway row by the MAC address contained in the 38-byte body. Cryptographic `X-Gateway-Key` verification belongs to Step 3.
2. **Permissive Dashboard Auth (Phase A)**:
   Full JWT session enforcement and multi-tenant scoping are addressed in Step 2.
3. **`seed_demo_data.py` Placeholder Hash**:
   Generates gateways with placeholder `api_key_hash` because gateway key generation belongs to Step 3.
4. **Commands and SOS Alerting**:
   Ingest parses `sos_active` and returns an empty response body without dispatching commands or triggering external webhooks (slated for later steps).

---

## 12. Changelog

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
