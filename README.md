# Coal Mine Subsidence Monitoring System

## 1. Overview
The Coal Mine Subsidence Monitoring System is a real-time IoT, telemetry, and GIS platform designed for surface subsidence detection, structural incline tracking, and DGMS compliance in underground coal mining operations.

### Data Pipeline
```
[ESP32 Sensor Nodes]
        │
        ▼ (ESP-NOW wireless mesh)
[ESP32 Gateway]
        │
        ▼ (HTTP POST binary packet: 38 bytes)
[FastAPI Server]
        │
        ▼ (SQLAlchemy / Alembic)
[PostgreSQL Database]
        │
        ▼ (REST API / Polling)
[Leaflet Web Dashboard]
```

> **College Prototype Notice**: This system is an academic engineering prototype and is **not** a certified industrial life-safety system. It must not be used as the sole protection mechanism in hazardous or explosive mining atmospheres without formal regulatory certification.

---

## 2. Repo Layout
```
.
├── .env.example             # Example environment variables for database & seed configuration
├── README.md                # System documentation, run guide, and step tracking
├── alembic.ini              # Root Alembic configuration pointing to Server migrations
├── seed.py                  # Root CLI wrapper for initial Company & Manager seed
├── dev_seed.py              # Root CLI wrapper for developer mine, tunnel & gateway seed
├── simulator.py             # Root CLI wrapper for standalone telemetry packet simulator
├── Design Document/         # System architecture & specification design documents
│   └── Coal Mine Subsidence Monitoring System Design Document (v2).pdf
├── ESP_Receiver/            # ESP32 Gateway firmware (ESP-NOW to HTTP client bridge)
│   └── main/Receiver_main.c
├── ESP_Sender/              # ESP32 Sensor Node firmware (MPU-6050, SW160, SOS button)
│   └── main/Sender_main.c
└── Server/                  # FastAPI backend server & storage layer
    ├── .env.example         # Environment template for server
    ├── alembic.ini          # Alembic configuration for migrations
    ├── alembic/             # Database migration version scripts and env.py
    │   ├── env.py
    │   └── versions/c79d6f612a5e_create_step1_tables.py
    ├── analyzer.py          # DGMS subsidence hazard classifier (tilt & vibration thresholds)
    ├── database.py          # SQLAlchemy PostgreSQL engine, sessionmaker, and Base
    ├── dev_seed.py          # DEV ONLY helper creating demo mine, tunnel & gateways
    ├── models.py            # SQLAlchemy models for the 8 core tables
    ├── requirements.txt     # Python dependency specifications
    ├── seed.py              # Production seed script for initial Company & Manager
    ├── server.py            # FastAPI application, ingest handler, and dashboard API
    ├── simulator.py         # Real-time 38-byte ESP packet simulator
    ├── static/              # Leaflet map frontend and Neo-Brutalist dashboard UI
    │   ├── app.js
    │   ├── index.html
    │   └── style.css
    ├── test_e2e.py          # End-to-end server + simulator HTTP verification script
    └── test_integration.py  # Step 1 comprehensive test suite
```

---

## 3. Prerequisites and Setup

### Prerequisites
- **Python**: 3.11 or higher
- **PostgreSQL**: 15 or higher (tested on PostgreSQL 18)

### Database Creation
Using `psql` or pgAdmin, create a database for the project:
```sql
CREATE DATABASE mine_subsidence;
```

### Dependency Installation
Activate your Python virtual environment and install dependencies:
```bash
cd Server
pip install -r requirements.txt
cd ..
```

### Environment Variables
Configure environment variables using `.env.example` as a template:

| Variable | Description | Default / Example |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection URL | `postgresql://postgres:password@localhost:5432/mine_subsidence` |
| `SEED_COMPANY_NAME` | Default company name for `seed.py` | `Bharat Coking Coal Limited` |
| `SEED_MANAGER_USERNAME` | Initial Manager username for `seed.py` | `manager` |
| `SEED_MANAGER_PASSWORD` | Initial Manager password for `seed.py` | (User-defined secret) |
| `SEED_MANAGER_FULL_NAME` | Manager full display name | `General Manager` |
| `OFFLINE_AFTER_S` | Silence threshold in seconds before node is marked offline | `35` |

---

## 4. How to Run (In Order)

Run the following commands in order from either the repository root or the `Server/` directory:

### Step 1: Run Alembic Migration
Apply database migrations to create the 8 PostgreSQL tables and enable extensions:
```bash
alembic upgrade head
```

### Step 2: Run Seed Script
Create the initial Company and Manager user account (idempotent):
```bash
python seed.py --company "Bharat Coking Coal Limited" --username "manager" --password "Manager@2026"
```
*(Arguments can also be passed via `SEED_COMPANY_NAME`, `SEED_MANAGER_USERNAME`, `SEED_MANAGER_PASSWORD` environment variables).*

### Step 3: Run Dev Seed Helper (DEV ONLY)
Create development mine, tunnel, and gateway records matching the simulator MACs:
```bash
python dev_seed.py
```

### Step 4: Start FastAPI Server
Start the Uvicorn web server hosting the REST API and Leaflet dashboard:
```bash
python -m uvicorn server:app --app-dir Server --host 127.0.0.1 --port 8000 --reload
```
Access the dashboard in your browser at `http://127.0.0.1:8000`.

### Step 5: Start Simulator
In a separate terminal, run the telemetry packet simulator:
```bash
python simulator.py
```
To run a single step verification burst:
```bash
python simulator.py --once
```

---

## 5. Data Model

The schema is managed strictly via Alembic migrations. The database contains only these 8 core tables:

### 1. `companies`
- `id` (Integer, PK, autoincrement)
- `name` (String, nullable=False)
- `name_normalized` (String, UNIQUE, index, nullable=False): Lowercase, trimmed, internal whitespace collapsed so duplicate variations (e.g. "Tata Coal" and "TATA  coal") cannot coexist.
- `created_at` (TIMESTAMPTZ, default `now()`)

### 2. `mines`
- `id` (Integer, PK, autoincrement)
- `company_id` (Integer, FK -> `companies.id` ON DELETE CASCADE)
- `name` (String, nullable=False)
- `subsidiary` (String, nullable=True)
- `status` (String, nullable=False, check constraint: `active`, `maintenance`, `suspended`, `closed`)
- `method` (String, nullable=False, check constraint: `opencast`, `underground`, `mixed`)
- `area_ha`, `max_depth_m`, `lat`, `lon` (Float, nullable=True)

### 3. `tunnels`
- `id` (Integer, PK, autoincrement)
- `mine_id` (Integer, FK -> `mines.id` ON DELETE CASCADE)
- `name` (String, nullable=False)
- `code` (String, nullable=False)
- `depth_m` (Float, nullable=True)
- `status` (String, nullable=False, default `active`)
- **Constraint**: `UNIQUE (mine_id, code)`

### 4. `users`
- `id` (Integer, PK, autoincrement)
- `company_id` (Integer, FK -> `companies.id` ON DELETE CASCADE)
- `username` (String, UNIQUE, index, nullable=False)
- `password_hash` (Text, nullable=False): PBKDF2-HMAC-SHA256 hash with per-user random salt (`<salt>$<hash>`)
- `full_name` (String, nullable=False)
- `role` (String, nullable=False, check constraint: `manager`, `safety_officer`, `engineer`, `foreman`, `miner`)
- `active` (Boolean, nullable=False, default True)
- `force_pw_change` (Boolean, nullable=False, default False)

### 5. `user_mines`
- `user_id` (Integer, FK -> `users.id` ON DELETE CASCADE)
- `mine_id` (Integer, FK -> `mines.id` ON DELETE CASCADE)
- **Constraint**: Composite Primary Key `(user_id, mine_id)`

### 6. `gateways`
- `id` (Integer, PK, autoincrement)
- `mac` (String, UNIQUE, index, nullable=False): Canonical hardware MAC (`AA:BB:CC:DD:EE:FF`)
- `mine_id` (Integer, FK -> `mines.id` ON DELETE CASCADE): One gateway serves exactly one mine.
- `name` (String, nullable=False)
- `api_key_hash` (Text, nullable=True)
- `active` (Boolean, nullable=False, default True)
- `created_by` (Integer, nullable=True, FK -> `users.id` ON DELETE SET NULL)
- `last_seen_at` (TIMESTAMPTZ, nullable=True)

### 7. `nodes`
- `id` (Integer, PK, autoincrement)
- `gateway_id` (Integer, FK -> `gateways.id` ON DELETE CASCADE)
- `node_no` (SmallInteger, 1..255, nullable=False)
- `mac` (String, nullable=True)
- `tunnel_id` (Integer, nullable=True, FK -> `tunnels.id` ON DELETE SET NULL): A node with `tunnel_id` NULL is unplaced.
- `lat`, `lon` (Float, nullable=True)
- `placed_by` (Integer, nullable=True, FK -> `users.id` ON DELETE SET NULL)
- `placed_at` (TIMESTAMPTZ, nullable=True)
- `status` (String, nullable=False, default `normal`): Latest classification (`normal`, `watch`, `warning`, `critical`)
- `battery_pct` (Float, nullable=True): Latest battery charge percentage
- `fast_mode` (Boolean, nullable=False, default False)
- `buzzer_on` (Boolean, nullable=False, default False)
- `last_cmd_seq` (Integer, nullable=False, default 0)
- `last_seen_at` (TIMESTAMPTZ, nullable=True)
- **Constraint**: `UNIQUE (gateway_id, node_no)`

### 8. `readings`
- `id` (BigInteger, PK, autoincrement)
- `node_id` (Integer, FK -> `nodes.id` ON DELETE CASCADE)
- `ts` (TIMESTAMPTZ, nullable=False): Server reception timestamp
- `boot_id` (BigInteger, default 0): Boot cycle identifier (0 until firmware sends one)
- `counter` (BigInteger, nullable=False): Incremental packet counter
- `tilt_x`, `tilt_y` (Float, nullable=False): Pitch/roll angular deviation in degrees
- `vib_rms` (Float, nullable=False): Vibration magnitude in g RMS
- `shock` (Boolean, nullable=False): Digital shock sensor flag
- `batt_v` (Float, nullable=False): Battery voltage
- `batt_pct` (Float, nullable=False): Battery percentage
- `node_level` (SmallInteger, nullable=False): Danger level reported by node firmware (cross-check only)
- `level` (SmallInteger, nullable=False): Independent server hazard classification (0=normal, 1=watch, 2=warning, 3=critical)
- **Indexes**:
  - `ix_readings_node_id_ts`: `(node_id, ts DESC)`
  - `uq_readings_node_boot_counter`: `UNIQUE (node_id, boot_id, counter) WHERE boot_id <> 0` (Partial Unique Index)

> **Why a Partial Unique Index on readings?**  
> While firmware transmits `boot_id = 0`, node transmission counters restart from 0 after each hardware reboot. A strict non-partial unique constraint on `(node_id, boot_id, counter)` would permanently reject valid post-reboot readings that reuse earlier counter values. Therefore, the unique index applies **only** where `boot_id <> 0`. For `boot_id = 0`, deduplication is performed at the application layer with a 4-second time window rule.

---

## 6. Wire Formats

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

## 7. API Reference

### Implemented Routes

| Method | Path | Allowed Callers | Request | Response | Status Codes |
|---|---|---|---|---|---|
| `POST` | `/api/ingest` | ESP32 Gateway | Body: Exactly 38-byte binary packet. Header: `X-Gateway-Id` (optional) | Empty body | `200` (Success / duplicate), `400` (Wrong length or unknown gateway) |
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
| `GET` | `/api/alerts` | All (Dashboard) | None | `{"alerts": []}` | `200` |
| `POST` | `/api/alerts/{id}/ack` | All | Path parameter | `{"message": "Alert acknowledged", "id": ...}` | `200` |
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

## 8. Ingest Behaviour

1. **Strict 38-Byte Check**:
   The ingest endpoint verifies `len(body) == 38`. Any payload of different length (such as the legacy 32-byte payload) is immediately rejected with HTTP 400.
2. **Field Mapping**:
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
3. **Independent Server Hazard Classification**:
   The server determines hazard level independently using DGMS thresholds:
   - **Tilt**: `< 0.5°` (0: Normal), `0.5° - 1.0°` (1: Watch), `1.0° - 2.0°` (2: Warning), `> 2.0°` (3: Critical).
   - **Vibration**: `< 0.05g` (0: Normal), `0.05 - 0.15g` (1: Watch), `0.15 - 0.30g` (2: Warning), `> 0.30g` (3: Critical).
   - SW160 digital shock detection triggers at least level 1 (Watch).
   - **The worse of tilt and vibration wins**: `level = max(tilt_level, vibration_level)`.
   - Node's transmitted `danger_level` is stored only as `node_level` for cross-check auditing and is never the source of truth.
4. **Deduplication Rule (`boot_id = 0`)**:
   A reading is classified as a duplicate **only if** a row already exists in `readings` with the same `(node_id, boot_id = 0, counter)` **AND** the delta between server reception time and existing reading `ts` is less than 4 seconds (`|now - existing.ts| < 4.0s`).
   - If duplicate: reading insertion is skipped, node metadata is refreshed, and HTTP 200 with an empty body is returned.
   - If delta $\ge 4.0\text{s}$: classified as a post-reboot reading and inserted into `readings`.
5. **Node Auto-Creation**:
   Nodes are automatically discovered and created on the first packet received by their gateway. Auto-created nodes have `tunnel_id`, `lat`, and `lon` set to `NULL` (unplaced queue).
6. **Node State Updating**:
   Every packet updates `nodes.last_seen_at`, `battery_pct`, `status`, `fast_mode`, `buzzer_on`, and `last_cmd_seq`.
7. **Response**:
   Returns HTTP 200 with an empty body.

---

## 9. Build Progress

| Step | Scope | Status | Done When Condition | Date Completed |
|---|---|---|---|---|
| **Step 1** | PostgreSQL migration and seed | **Done** | The simulator posts packets, rows land in PostgreSQL, the existing dashboard shows live nodes and values exactly as before, and re-running the migration and seed on a fresh database works. | 2026-11-22 |
| **Step 2** | Login and roles | *Not started* | Users table, login, require_role, company and mine scoping. A worker token gets 403 on every superior route. | — |
| **Step 3** | Mines, tunnels, gateways and node placement | *Not started* | Onboarding screen, create mine, add tunnels, register gateway (key shown once), tunnel picker on Place on Map with same-mine check. | — |
| **Step 4** | Watchdog and offline detection | *Not started* | ESP watchdog, sensor-fault flag, gateway heartbeat, server last-seen check, gateway-offline alert. Unplugging a node shows it offline and fires one alert. | — |
| **Step 8 (SOS path)** | Worker reports and node SOS | *Not started* | Report form, inbox, status flow, node SOS button and SOS packet. SOS packet creates an incident and alert within a few seconds. | — |

---

## 10. Known Limitations and Deviations

1. **Interim `X-Gateway-Id` Header**:
   As required for Step 1 compatibility, the gateway check uses the existing `X-Gateway-Id` header and resolves the gateway row by the MAC address contained in the 38-byte body. Cryptographic `X-Gateway-Key` and `api_key_hash` verification is slated for Step 3.
2. **Permissive Dashboard Auth**:
   Full JWT session enforcement, user permission checks, and company/mine tenant scoping are omitted in Step 1 to keep the frontend map functioning without edits. Full RBAC and scoping will be implemented in Step 2.
3. **`dev_seed.py` Placeholder Hash**:
   `dev_seed.py` generates gateways with a placeholder `api_key_hash` because the gateway key generation and onboarding flow belongs to Step 3.
4. **Commands and SOS Alerting**:
   Ingest parses `sos_active` and returns an empty response body without dispatching commands or triggering external webhooks (command lines and SOS incidents belong to later steps).

---

## 11. Changelog

### 2026-11-22 - Step 1: PostgreSQL Migration and Seed
- **Added**:
  - SQLAlchemy models for the 8 core tables: `companies`, `mines`, `tunnels`, `users`, `user_mines`, `gateways`, `nodes`, `readings`.
  - Alembic migration environment (`Server/alembic`) and initial migration `c79d6f612a5e_create_step1_tables.py` enabling `btree_gist` extension and creating all 8 tables with timestamptz columns and partial unique indexes.
  - Production seed script `Server/seed.py` and root wrapper `seed.py` creating exactly one Company and one Manager user with PBKDF2-HMAC-SHA256 password hashing.
  - Developer seed helper `Server/dev_seed.py` and root wrapper `dev_seed.py` initializing test mine, tunnel, and gateways matching simulator MACs.
  - Telemetry packet simulator `Server/simulator.py` and root wrapper `simulator.py` posting 38-byte binary packets (`<6sBIfffBffBBBBH`) over HTTP to `/api/ingest`.
  - Comprehensive integration test suite `Server/test_integration.py` and end-to-end verification script `Server/test_e2e.py`.
  - Configuration template `.env.example` at root and in `Server/`.
- **Changed**:
  - Replaced legacy SQLite database (`data.db`) with PostgreSQL connection pool in `database.py`.
  - Upgraded `POST /api/ingest` to strictly require 38-byte binary payload, reject unknown gateways, auto-create nodes, compute independent hazard level, and enforce 4-second boot_id=0 deduplication.
  - Ported dashboard endpoints (`/api/nodes`, `/api/gateways`, `/api/history`, `/api/placements`, `/api/unplaced`, `/api/export.xlsx`) to query PostgreSQL.
  - Updated `Server/requirements.txt` to include `sqlalchemy`, `alembic`, `psycopg2-binary`, and `psycopg[binary]`.
