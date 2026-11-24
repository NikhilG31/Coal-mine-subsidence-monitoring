# Coal Mine Subsidence Monitoring System

## 1. Overview and Pipeline
The Coal Mine Subsidence Monitoring System is an IoT telemetry, geotechnical analysis, and GIS monitoring platform designed for surface subsidence detection, incline tracking, and DGMS compliance in underground coal mining operations.

### System Architecture

```mermaid
flowchart TD
    subgraph Physical["Physical Underground Tier"]
        Nodes["ESP32 Sensor Nodes (MPU-6050, SW160, SOS, Buzzer)"]
        Gateway["ESP32 Gateway Bridge (Receiver_main.c)"]
        Nodes -- "ESP-NOW (37-byte Telemetry)" --> Gateway
        Gateway -. "ESP-NOW (10-byte Downlink)" .-> Nodes
    end

    subgraph Ingestion["Ingestion & Boundary Tier"]
        IngestAPI["POST /api/ingest (38-byte binary packet)"]
        Gateway -- "HTTP / X-Gateway-Key" --> IngestAPI
        IngestAPI -. "HTTP 200 (CMD Downlink Line)" .-> Gateway
    end

    subgraph ServerTier["FastAPI Application Tier (Python 3.11)"]
        IngestAPI --> IngestService["Ingest & Deduplication Service"]
        IngestService --> Classifier["DGMS Hazard Classifier"]
        IngestService --> SOSService["SOS & Incident Service"]
        Watchdog["Watchdog Runner Daemon (Postgres Advisory Lock)"]
        HeadcountService["Headcount & Check Flag Service"]
        ProductionService["Production & Profile Domain Service"]
        AuthRBAC["RBAC & Session Service (HS256 JWT, PBKDF2)"]
    end

    subgraph Storage["PostgreSQL 16 Storage Tier"]
        DB[("mine_subsidence (21 Core Tables)")]
        IngestService --> DB
        SOSService --> DB
        Watchdog --> DB
        HeadcountService --> DB
        ProductionService --> DB
        AuthRBAC --> DB
    end

    subgraph Frontend["Web Presentation Tier"]
        Dashboard["Neo-Brutalist Dashboard (Leaflet Map, SVG Charts)"]
        Scanner["Gate Scanner UI (QR Check-in / Check-out)"]
        ProfileModal["Mine Profile & Output Modal (5 Groups Superior & Worker View)"]
        Dashboard <--> AuthRBAC
        Scanner <--> HeadcountService
        ProfileModal <--> ProductionService
    end
```

### Data Pipeline
```
[ESP32 Sensor Nodes]
        │ ▲
        │ │ (ESP-NOW wireless mesh: 37-byte node telemetry / 10-byte downlink command)
        ▼ │
[ESP32 Gateway]
        │ ▲
        │ │ HTTP POST binary packet (38 bytes) / HTTP 200 text/plain ("CMD,node_no,ACTION,seq\n")
        ▼ │
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
├── server/                                       # FastAPI backend server package
│   ├── alembic.ini                               # Alembic migration configuration
│   ├── requirements.txt                          # Production runtime dependencies (psycopg v3, PyJWT, openpyxl, FastAPI)
│   ├── requirements-dev.txt                      # Development & test dependencies (-r requirements.txt, pytest, httpx)
│   ├── migrations/                               # Alembic database migrations
│   │   ├── README                                # Migrations documentation
│   │   ├── env.py                                # Migration environment runner (compare_type & defaults enabled)
│   │   ├── script.py.mako                        # Revision script template
│   │   └── versions/                             # Schema migration versions
│   │       ├── c79d6f612a5e_create_step1_tables.py # Initial 8 core PostgreSQL tables
│   │       ├── d84e2a87b91f_step1_hardening.py   # Server defaults, check constraint, index cleanup
│   │       ├── e95f3b721a01_step3_placement_and_audit.py # Audit log, mine unique, gateway MAC regex, placement constraint
│   │       ├── e252124b61df_step4_alerts_and_token_version.py # Alerts table, partial unique indexes, user token_version
│   │       ├── c1506e641433_step5_workers_assignments_shifts.py # Worker profiles, assignments, and GIST exclusion constraint
│   │       ├── e2b8b74826ca_step6_qr_checkin_headcount_flags.py # QR tokens, check events, check flags, partial unique indexes
│   │       ├── a69df82fa18b_step7_incidents_roll_call_node_commands.py # Incidents, incident roster snapshot, node commands table
│   │       ├── 5d042c33b47c_step8_worker_reports_and_sos.py # Worker reports, device SOS, and partial unique index on open SOS incidents
│   │       └── 343347cf01f6_step9_mine_profile_and_production.py # Evacuation notes, emergency contacts, and daily production
│   ├── app/                                      # Application package
│   │   ├── __init__.py                           # App package root
│   │   ├── config.py                             # Centralized environment variable loader (no defaults for DB)
│   │   ├── database.py                           # SQLAlchemy engine, SessionLocal factory, and Base
│   │   ├── main.py                               # FastAPI application factory, router registration, static mount
│   │   ├── watchdog_runner.py                    # Background offline detection daemon with PostgreSQL advisory lock
│   │   ├── cli/                                  # Command-line tools
│   │   │   └── seed_manager.py                   # Idempotent Company & Manager bootstrapping CLI
│   │   ├── dev/                                  # Development-only helpers
│   │   │   ├── seed_demo_data.py                 # Seeds test mine, tunnel, and simulator gateways
│   │   │   └── simulator.py                      # Multi-node 38-byte ESP packet simulator
│   │   ├── models/                               # SQLAlchemy ORM models (one file per table)
│   │   │   ├── __init__.py                       # Package exports for all models
│   │   │   ├── alert.py                          # Alert model with offline states and partial unique constraints
│   │   │   ├── assignment.py                     # Underground tunnel shift assignment with GIST exclusion constraint
│   │   │   ├── audit_log.py                      # Immutable audit log model
│   │   │   ├── check_event.py                    # Check-in and check-out event database entity model
│   │   │   ├── check_flag.py                     # Shift check-out exception flag database entity model
│   │   │   ├── company.py                        # Company table and normalized name helper
│   │   │   ├── gateway.py                        # Hardware gateway table
│   │   │   ├── incident.py                       # Emergency incident entity with status check & auto partial unique index
│   │   │   ├── incident_roster.py                # Roll call roster snapshot entity with accounting timestamps
│   │   │   ├── mine.py                           # Coal mine sites table with evacuation notes
│   │   │   ├── mine_contact.py                   # Mine emergency contact phone hierarchy table
│   │   │   ├── mine_production.py                # Daily planned and actual coal production table
│   │   │   ├── node.py                           # Sensor nodes table (1..255 range constraint)
│   │   │   ├── node_command.py                   # Downlink node command entity with sequence wrapping & pending partial index
│   │   │   ├── qr_token.py                       # QR badge token entity with partial unique active index
│   │   │   ├── reading.py                        # Telemetry readings table with partial unique index
│   │   │   ├── tunnel.py                         # Underground mine tunnels table
│   │   │   ├── user.py                           # Users and UserMine association tables
│   │   │   ├── worker_profile.py                 # Worker profile and emergency contact table
│   │   │   └── worker_report.py                  # Worker safety problem reports and device SOS distress records
│   │   ├── routers/                              # FastAPI area routers (HTTP handling only)
│   │   │   ├── alerts.py                         # GET /api/alerts, POST /api/alerts/{id}/ack
│   │   │   ├── assignments.py                    # POST/PATCH/DELETE /api/assignments, GET /api/assignments
│   │   │   ├── auth.py                           # POST /api/auth/login, GET /api/auth/me, POST /api/auth/logout, POST /api/auth/change-password
│   │   │   ├── check_flags.py                    # GET /api/check-flags, PATCH /api/check-flags/{id}
│   │   │   ├── checkins.py                       # POST /api/checkins, POST /api/checkins/manual, GET /api/checkins, GET /api/headcount
│   │   │   ├── demo.py                           # /api/demo/* simulation and testing routes
│   │   │   ├── export.py                         # GET /api/export.xlsx workbook export
│   │   │   ├── gateways.py                       # GET/PATCH/DELETE /api/gateways, POST /api/gateways/{id}/rotate-key
│   │   │   ├── health.py                         # GET /api/health monitoring endpoint
│   │   │   ├── history.py                        # GET /api/history time-series queries (eager loaded)
│   │   │   ├── incidents.py                      # POST /api/incidents, GET /api/incidents, GET/POST roster, PATCH close, PATCH siren
│   │   │   ├── ingest.py                         # POST /api/ingest 38-byte binary receiver & downlink CMD text/plain response
│   │   │   ├── me.py                             # GET /api/me/shifts, GET /api/me/profile
│   │   │   ├── mine_profile.py                   # GET/PATCH /api/mines/{id}/profile, PUT contacts, GET headcount, GET/PUT production
│   │   │   ├── mines.py                          # GET/POST /api/mines, /api/mines/{id}/tunnels, /api/mines/{id}/gateways
│   │   │   ├── node_commands.py                  # POST /api/nodes/{id}/commands manual buzzer trigger
│   │   │   ├── nodes.py                          # GET /api/nodes telemetry list
│   │   │   ├── notifications.py                  # GET / POST /api/notifications/config
│   │   │   ├── placements.py                     # GET /api/placements, GET /api/unplaced, PATCH/DELETE /api/nodes/{id}/placement
│   │   │   ├── reports.py                        # POST /api/reports, GET /api/reports, PATCH /api/reports/{id}
│   │   │   ├── users.py                          # GET/POST /api/users, PATCH/DELETE /api/users/{id}
│   │   │   └── workers.py                        # GET /api/workers, GET /api/workers/{id}, PUT /api/workers/{id}/profile, POST/DELETE /api/workers/{id}/badge
│   │   ├── schemas/                              # Pydantic serialization schemas and view models
│   │   │   ├── __init__.py                       # Package exports
│   │   │   ├── checkins.py                       # Badge, scan, headcount, and check flag schemas
│   │   │   ├── incidents.py                      # IncidentOpenRequest, IncidentResponse, RosterMemberResponse, CloseIncidentRequest
│   │   │   ├── mine_profile.py                   # Mine profile 5-group schemas, worker-safe view, contacts & production schemas
│   │   │   ├── mines.py                          # Mine, tunnel, and gateway creation request schemas
│   │   │   ├── node_commands.py                  # CreateCommandRequest, CommandResponse
│   │   │   ├── nodes.py                          # WorkerNodeView (strict leakage prevention) & SuperiorNodeView
│   │   │   ├── placements.py                     # PlacementsResponse, WorkerPlacementView, SuperiorPlacementView
│   │   │   ├── reports.py                        # CreateReportRequest, UpdateReportRequest, SuperiorReportView, WorkerReportView
│   │   │   └── workers.py                        # ProfileUpsertRequest, CreateAssignmentRequest, and worker shift schemas
│   │   ├── security/                             # Authentication, RBAC, and cryptographic primitives
│   │   │   ├── __init__.py                       # Security package exports
│   │   │   ├── dependencies.py                   # FastAPI dependencies (auth, role checks, mine scoping)
│   │   │   ├── password_policy.py                # Centralized password complexity rules
│   │   │   ├── passwords.py                      # PBKDF2-HMAC-SHA256 (600k iter) & legacy hash verification
│   │   │   ├── rate_limit.py                     # Sliding-window login & gate scan throughput limiter
│   │   │   ├── roles.py                          # Role definitions and role grouping logic
│   │   │   └── tokens.py                         # HS256 JWT session tokens and cookie management
│   │   ├── services/                             # Core business logic (no FastAPI imports)
│   │   │   ├── audit.py                          # Atomic audit log recorder
│   │   │   ├── badge_service.py                  # 256-bit token generation, SHA-256 storage, segno SVG, and revocation
│   │   │   ├── checkin_service.py                # Gate scan processing, 5s deduplication, state transitions, roll call integration
│   │   │   ├── clock.py                          # Centralized timezone-aware UTC clock provider
│   │   │   ├── connectivity.py                   # Single source of truth for node and gateway online calculation
│   │   │   ├── flag_clearing.py                  # Manual flag clearance, note audit sanitization, manual 'out' event
│   │   │   ├── flag_service.py                   # Background shift evaluation past grace and idempotent check_flag creation
│   │   │   ├── gateway_keys.py                   # Cryptographic 256-bit API key generator and SHA-256 verifier
│   │   │   ├── hazard_classifier.py              # Pure function classifier matching firmware thresholds
│   │   │   ├── headcount.py                      # DISTINCT ON latest check event, live inside counts, scheduled counts
│   │   │   ├── incident_service.py               # Incident lifecycle, roll call snapshot, manual/gate accounting, auto-critical open
│   │   │   ├── ingest_service.py                 # Packet verification, deduplication, persistence, and downlink ack
│   │   │   ├── latest_readings.py                # PostgreSQL distinct_on query optimizer for telemetry readings
│   │   │   ├── mine_contacts_service.py          # Atomic emergency contacts replace and rank uniqueness validation
│   │   │   ├── mine_headcount_service.py         # Headcount by shift & tunnel with lookahead in constant queries
│   │   │   ├── mine_profile_service.py           # Superior 5-group assembly, trimmed worker-safe model, profile updates
│   │   │   ├── node_command_service.py           # Command queueing, sequence wrapping, line encoding, echo acknowledgment
│   │   │   ├── node_placement.py                 # Node placement completeness helpers and expressions
│   │   │   ├── node_serializer.py                # Telemetry dictionary serializer (superior & worker views)
│   │   │   ├── packet_codec.py                   # 38-byte struct parse and pack definition
│   │   │   ├── production_service.py             # Monthly production upsert, calendar month aggregation, independent sums
│   │   │   ├── report_service.py                 # Worker problem reports, rate limiting, and resolution lifecycle
│   │   │   ├── roll_call_service.py              # Evacuation roll call accounting and late arrival snapshot helpers
│   │   │   ├── shift_service.py                  # Shift creation, duration checks, overlap detection, exclusion constraint handling
│   │   │   ├── siren_service.py                  # Periodic background siren keepalive refresh cycle
│   │   │   ├── sos_service.py                    # Device SOS packet ingestion, 60s merge window, flood guard, and incident creation
│   │   │   ├── tunnel_scope.py                   # Worker current tunnel resolution and miner visible tunnel scoping
│   │   │   ├── watchdog_service.py               # Watchdog offline timeout evaluation and alert auto-resolution
│   │   │   ├── weekly_hours.py                   # Rolling week boundary splitting, hours summing, and warning generation
│   │   │   └── worker_queries.py                 # Scoped worker roster queries and worker shift detail history
│   │   └── static/                               # Web dashboard client files
│   │       ├── app.js                            # Leaflet and dashboard client logic
│   │       ├── checkin.js                        # Gate scan controller and scanner interface
│   │       ├── headcount_flags.js                # Badge modal, live headcount polling, and flag resolution UI
│   │       ├── incidents.js                      # Emergency incidents, roll call roster, and buzzer control UI
│   │       ├── index.html                        # Dashboard HTML layout
│   │       ├── mine_profile.js                   # Mine profile 5-card editor, contacts rank ordering, production SVG chart
│   │       ├── reports.js                        # Worker hazard reporting, superior inbox, and persistent SOS banner
│   │       └── style.css                         # Neo-Brutalist dashboard styles
│   └── tests/                                    # Pytest test suite (runs on test database)
│       ├── conftest.py                           # TEST_DATABASE_URL fixture, migrations & table truncation
│       ├── test_auth_and_roles.py                # RBAC matrix, tenant isolation, rate limit, worker leakage tests
│       ├── test_classifier.py                    # Spec matrix unit tests for hazard classifier
│       ├── test_dashboard_routes.py              # Dashboard API endpoints and placement workflow tests
│       ├── test_ingest.py                        # Packet length, gateway check, dedup, and classification tests
│       ├── test_seed.py                          # seed_manager idempotency and password reset tests
│       ├── test_step3_phase_b.py                 # Step 3 gateway keys, placement, audit, and manager journey tests
│       ├── test_step4_watchdog.py                # Step 4 watchdog, connectivity boundary, and scoping tests
│       ├── test_step5_policy.py                  # Step 5 policy enforcement, employee profile, redaction, and N+1 tests
│       ├── test_step5_shifts.py                  # Step 5 shifts, overlap rejection, exclusion constraint, scoping tests
│       ├── test_step6_badges_and_headcount.py    # Step 6 QR badges, gate scanning, headcount, flags, and concurrency tests
│       ├── test_step7_hardening.py               # Step 7.5 gateway scoping, siren cycle, auto-incidents, retention tests
│       ├── test_step7_incidents_and_commands.py  # Step 7 emergency incidents, roll call snapshot, commands, and siren tests
│       ├── test_step8_followup_sos.py            # Step 8 follow-up SOS incident idempotency, merge, and retry tests
│       ├── test_step8_reports_and_sos.py         # Step 8 worker reports, device SOS, merge window, and flood guard tests
│       └── test_step9_mine_profile_and_production.py # Step 9 profile 5 groups, worker view, contacts, production sums
└── tools/                                        # Developer, deployment, and operational tooling
    ├── backup_db.sh                              # Automated PostgreSQL custom-format backup and retention script
    ├── build_synopsis.py                         # Synopsis document generation tool
    ├── generate_presentation.py                  # Presentation slide generation tool
    ├── generate_route_table.py                   # Automated OpenAPI route table and policy matrix generator
    ├── render_pages.py                           # PDF page rendering utility
    └── restore_db.md                             # Comprehensive database restoration runbook and verification guide
```

---

## 3. Where to Look When Something Breaks

| Symptom | Location to Inspect |
|---|---|
| Wrong hazard level or color | `services/hazard_classifier.py` |
| Ingest packet rejected with HTTP 400 | `services/packet_codec.py` + `routers/ingest.py` |
| Duplicate or missing readings; reboot counter issues | `services/ingest_service.py` |
| Ingest packet rejected with HTTP 401 | `services/gateway_keys.py` + `routers/ingest.py` |
| Placement errors or same-mine violations | `services/node_placement.py` + `routers/placements.py` |
| Login, 403, or 404 access control problems | `security/dependencies.py` |
| Audit rows missing or detail discrepancy | `services/audit.py` |
| Database schema, constraint, or migration failure | `models/` + `migrations/versions/` |
| Environment variables not resolving or missing error | `config.py` |
| Password verification or hashing discrepancy | `security/passwords.py` |
| Dashboard map markers or inspector not updating | `static/app.js` + `routers/nodes.py` |
| Simulator fails to send packets or times out | `dev/simulator.py` |
| N+1 query slowdown on history or export | `routers/history.py` + `routers/export.py` |

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
| `OFFLINE_AFTER_S` | No | Legacy alias for `NODE_OFFLINE_AFTER_S` | `35` |
| `NODE_OFFLINE_AFTER_S` | No | Seconds of silence before a sensor node is marked offline (min 10, max 86400) | `35` |
| `GATEWAY_OFFLINE_AFTER_S` | No | Seconds of silence before a gateway is marked offline (min 5, max 86400, >= NODE_OFFLINE_AFTER_S) | `60` |
| `WATCHDOG_INTERVAL_S` | No | Evaluation interval for offline watchdog thread in seconds (min 1, max 3600) | `30` |
| `WATCHDOG_ENABLED` | No | Flag to enable/disable the background offline watchdog daemon thread | `true` |
| `ENABLE_DEMO_ROUTES` | No | Controls accessibility of `/api/demo/*` endpoints | `false` (code default), `true` (.env.example) |
| `JWT_SECRET` | Phase B | Secret key for HS256 JWT tokens (>= 32 chars) | (User secret) |
| `SESSION_HOURS` | Phase B | Session lifetime in hours | `8` |
| `COOKIE_SECURE` | Phase B | Enforce HTTPS Secure flag on session cookie | `false` |
| `CORS_ORIGINS` | No | Comma-separated list of allowed CORS origins | `""` (same-origin only) |
| `ALLOW_UNKEYED_INGEST` | No | Development-only compatibility flag. When `true`, allows active gateways to ingest without `X-Gateway-Key`. Logs startup and rate-limited warnings. | `false` |
| `DEMO_GATEWAY_KEY` | Dev/Tests | Shared 256-bit API key for simulator and demo seeding (min 32 chars, no `CHANGE_ME`, min 12 distinct chars) | (User secret) |
| `WEEKLY_HOURS_LIMIT` | No | Maximum recommended shift hours per worker per week before non-blocking warning | `48.0` |
| `MAX_SHIFT_HOURS` | No | Maximum allowed duration in hours for a single continuous shift assignment | `16.0` |
| `HOURS_TIMEZONE` | No | Valid IANA timezone for weekly shift hour boundary splitting (Monday 00:00 to Monday 00:00) | `Asia/Kolkata` |
| `CHECKOUT_GRACE_MIN` | Step 6 | Grace period in minutes after shift end before raising missing check-out flag | `15` |
| `CHECKIN_EARLY_MIN` | Step 6 | Maximum minutes before shift start that a check-in is associated with the shift | `120` |
| `CHECK_FLAG_OVERDUE_HOURS` | Step 6 | Hours elapsed since flag creation before marking a check flag overdue | `8` |
| `SCAN_RATE_PER_MIN` | Step 6 | Maximum permitted gate scan operations per minute per scanner user | `60` |
| `COMMAND_TTL_S` | Step 7 | Maximum pending node command lifetime in seconds before expiry (min 10, max 3600) | `120` |
| `SIREN_REFRESH_S` | Step 7 | Evaluation interval in seconds for background siren keepalive refresh cycle (min 5, max 40) | `30` |
| `COMMANDS_REQUIRE_HTTPS` | Step 7 | Enforce HTTPS scheme on ingest POST before returning downlink node commands | `true` |
| `TRUST_FORWARDED_PROTO` | Step 7 | Trust `X-Forwarded-Proto` header for HTTPS check behind TLS reverse proxy | `false` |
| `COMMAND_RETENTION_DAYS` | Step 7.5 | Retention period in days for acked or expired node commands before watchdog purge | `30` |
| `AUTO_INCIDENT_SIREN` | Step 7.5 | When `true`, automatically opened critical incidents enable sirens immediately. When `false`, incidents open silently to prevent false alarm sirens until verified. | `true` |
| `SOS_MERGE_WINDOW_S` | Step 8 | Time window in seconds during which repeated device SOS signals merge into existing report (min 10, max 300) | `60` |
| `SOS_MAX_NEW_PER_HOUR` | Step 8 | Maximum new device SOS reports created per node per hour before flood guard merges (min 1, max 60) | `6` |
| `SOS_INCIDENT_SIREN` | Step 8 | Whether automatically created device SOS incidents activate sirens immediately | `false` |
| `REPORT_RATE_PER_HOUR` | Step 8 | Maximum problem reports a single worker user can submit per hour before 429 rate limit (min 1, max 100) | `20` |
| `REPORT_MAX_AGE_DAYS_WORKER_VIEW` | Step 8 | Maximum lookback age in days for reports visible in worker self-service view (min 1, max 365) | `90` |
| `SHIFT_LOOKAHEAD_H` | Step 9 | Hours ahead to look for upcoming shift assignments in mine headcount breakdown (positive integer) | `12` |

---

## 5. Fresh-Clone Quick Start

Follow these exact steps to clone, configure, migrate, seed, run, simulate, test, and back up the system from scratch:

### 1. Clone Repository & Setup Environment
```bash
git clone https://github.com/nikhilgurnani/Mine-Subsidence-Monitoring.git
cd "Mine-Subsidence-Monitoring"

# Create and activate Python 3.11 virtual environment
python -m venv server/venv

# Linux / macOS:
source server/venv/bin/activate

# Windows (PowerShell):
.\server\venv\Scripts\Activate.ps1
```

### 2. Install Pinned Dependencies
```bash
pip install -r server/requirements-dev.txt
```

### 3. Configure Environment Variables
```bash
cp .env.example .env
```
Open `.env` and set your local PostgreSQL database credentials and security tokens:
- `DATABASE_URL=postgresql+psycopg://postgres:your_password@localhost:5432/mine_subsidence`
- `TEST_DATABASE_URL=postgresql+psycopg://postgres:your_password@localhost:5432/mine_subsidence_test`
- `JWT_SECRET=your_32_character_minimum_random_secret_string`

### 4. Create PostgreSQL Databases
```bash
# Create application and test databases
createdb -h localhost -U postgres mine_subsidence
createdb -h localhost -U postgres mine_subsidence_test
```

### 5. Apply Database Migrations
```bash
# Set PYTHONPATH to server directory
export PYTHONPATH="server"  # Windows PowerShell: $env:PYTHONPATH="server"

alembic -c server/alembic.ini upgrade head
```

### 6. Seed Initial Company & Manager
```bash
python -m app.cli.seed_manager --company "Bharat Coking Coal Limited" --username "manager" --password "ManagerPass@2026"
```

### 7. Seed Demo Hardware & Mine Inventory (Dev Only)
```bash
python -m app.dev.seed_demo_data
```

### 8. Start Development Server
```bash
uvicorn app.main:app --reload
```
Access the Neo-Brutalist dashboard at: `http://localhost:8000`.

### 9. Run Hardware Telemetry Simulator
In a separate terminal (with virtualenv active and `PYTHONPATH="server"`):
```bash
# Continuous packet broadcast:
python -m app.dev.simulator

# Or trigger a physical SOS emergency signal burst:
python -m app.dev.simulator --sos
```

### 10. Run Full Automated Test Suite
```bash
pytest -v
```
*(Executes all 161 automated test cases against `mine_subsidence_test` in complete isolation).*

### 11. Run Automated Database Backup
```bash
bash tools/backup_db.sh
```
*(Dumps `mine_subsidence` in custom compressed format to `backups/coalmine_YYYYmmdd_HHMMSS.dump` and retains newest 14 archives).*

---

## 6. Data Model

The schema is managed strictly via Alembic migrations. The database contains these 21 core tables:

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
- `evacuation_notes` (`Text`, nullable=True, check: `char_length(evacuation_notes) <= 2000`)
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
- `token_version` (`Integer`, nullable=False, server_default=`0`): Monotonic session version integer. Embedded as `tv` claim in JWT. Any password change, administrative password reset, or account deactivation increments this counter, immediately invalidating all previous session tokens across all devices.

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
- `action` (`String(64)`, nullable=False): Operation tag (e.g. `mine.create`, `tunnel.create`, `gateway.register`, `gateway.rotate_key`, `gateway.update`, `gateway.disable`, `node.place`, `node.unplace`, `user.create`, `user.update`, `user.deactivate`, `auth.password_change`, `alert.acknowledge`)
- `entity` (`String(64)`, nullable=False): Target entity type (`mine`, `tunnel`, `gateway`, `node`, `user`, `alert`)
- `entity_id` (`Integer`, nullable=True): Primary key of the affected entity
- `detail` (`JSONB`, nullable=False, server_default=`'{}'`)
- **Index**: `ix_audit_log_company_id_ts`: `(company_id, ts DESC)`
- **Immutability**: Audit logs record all administrative, security, and hardware mutations atomically with the change in the same transaction. Failed operations write nothing. Sensitive tokens, passwords, and API keys are strictly excluded from the `detail` payload.

### 10. `alerts`
- `id` (`BigInteger`, PK, autoincrement)
- `company_id` (`Integer`, FK -> `companies.id` ON DELETE CASCADE, nullable=False, index)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False, index)
- `gateway_id` (`Integer`, FK -> `gateways.id` ON DELETE CASCADE, nullable=False, index)
- `node_id` (`Integer`, FK -> `nodes.id` ON DELETE CASCADE, nullable=True, index)
- `kind` (`String(32)`, nullable=False, check constraint: `kind IN ('node_offline', 'gateway_offline')`)
- `title` (`String(255)`, nullable=False)
- `opened_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `resolved_at` (`TIMESTAMPTZ`, nullable=True)
- `acknowledged_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `acknowledged_at` (`TIMESTAMPTZ`, nullable=True)
- **Constraints**:
  - `ck_alerts_kind`: `kind IN ('node_offline', 'gateway_offline')`
  - `ck_alerts_kind_node`: `(kind = 'node_offline' AND node_id IS NOT NULL) OR (kind = 'gateway_offline' AND node_id IS NULL)`
- **Partial Unique Indexes**:
  - `uq_alerts_open_node`: `UNIQUE (node_id) WHERE kind = 'node_offline' AND resolved_at IS NULL`
  - `uq_alerts_open_gateway`: `UNIQUE (gateway_id) WHERE kind = 'gateway_offline' AND resolved_at IS NULL`
- **Index**:
  - `ix_alerts_mine_id_opened_at`: `(mine_id, opened_at DESC)`

> **Why Partial Unique Indexes on alerts?**  
> Partial unique indexes on PostgreSQL enforce that at most **one** open alert (`resolved_at IS NULL`) can exist concurrently for any given hardware entity (node or gateway). Even under extreme database concurrency across multiple worker threads or cluster instances, the DB engine rejects duplicate opens via `IntegrityError`, which the watchdog service catches and treats as already open. When a device recovers, `resolved_at` is stamped; if the device goes offline again later, a clean new alert row is created without overwriting historical incident records.

### 11. `worker_profiles`
- `user_id` (`Integer`, PK, FK -> `users.id` ON DELETE CASCADE)
- `company_id` (`Integer`, FK -> `companies.id` ON DELETE CASCADE, nullable=False, index)
- `employee_no` (`String(64)`, nullable=False)
- `phone` (`String(32)`, nullable=True)
- `emergency_contact_name` (`String(255)`, nullable=True)
- `emergency_contact_phone` (`String(32)`, nullable=True)
- `joined_on` (`Date`, nullable=True)
- **Constraint**: `UNIQUE (company_id, employee_no)` named `uq_worker_profiles_company_employee_no`

### 12. `assignments`
- `id` (`BigInteger`, PK, autoincrement)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE CASCADE, nullable=False, index)
- `tunnel_id` (`Integer`, FK -> `tunnels.id` ON DELETE RESTRICT, nullable=False, index)
- `starts_at` (`TIMESTAMPTZ`, nullable=False)
- `ends_at` (`TIMESTAMPTZ`, nullable=False)
- `created_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `created_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- **Constraints**:
  - `ck_assignments_range`: `CHECK (ends_at > starts_at)`
  - `ex_assignments_user_no_overlap`: `EXCLUDE USING gist (user_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)`
- **Indexes**:
  - `ix_assignments_user_id_starts_at`: `(user_id, starts_at DESC)`
  - `ix_assignments_tunnel_id_starts_at`: `(tunnel_id, starts_at)`

### 13. `qr_tokens`
- `id` (`BigInteger`, PK, autoincrement)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE CASCADE, nullable=False, index)
- `token_hash` (`String(64)`, nullable=False, unique): SHA-256 hex digest of random 256-bit token (`cmb_` + `secrets.token_urlsafe(32)`). Plaintext tokens are strictly shown once on issuance and never stored or logged.
- `issued_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `issued_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `revoked_at` (`TIMESTAMPTZ`, nullable=True)
- `revoked_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- **Partial Unique Index**:
  - `uq_qr_tokens_one_active`: `UNIQUE (user_id) WHERE revoked_at IS NULL` (strictly enforces at most one active badge per worker)

### 14. `check_events`
- `id` (`BigInteger`, PK, autoincrement)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE CASCADE, nullable=False)
- `tunnel_id` (`Integer`, FK -> `tunnels.id` ON DELETE RESTRICT, nullable=False)
- `direction` (`String(3)`, nullable=False, check constraint: `direction IN ('in', 'out')`)
- `method` (`String(8)`, nullable=False, check constraint: `method IN ('qr', 'manual')`)
- `scanned_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `scanned_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- **Indexes**:
  - `ix_check_events_user_id_scanned_at`: `(user_id, scanned_at DESC)`
  - `ix_check_events_tunnel_id_scanned_at`: `(tunnel_id, scanned_at DESC)`

### 15. `check_flags`
- `id` (`BigInteger`, PK, autoincrement)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE CASCADE, nullable=False, index)
- `assignment_id` (`BigInteger`, FK -> `assignments.id` ON DELETE CASCADE, nullable=False, unique)
- `raised_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `cleared_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `cleared_at` (`TIMESTAMPTZ`, nullable=True)
- `note` (`String(500)`, nullable=True)
- **Partial Index**:
  - `ix_check_flags_raised_at`: `(raised_at) WHERE cleared_at IS NULL`

### 16. `incidents`
- `id` (`BigInteger`, PK, autoincrement)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False, index)
- `tunnel_id` (`Integer`, FK -> `tunnels.id` ON DELETE RESTRICT, nullable=True, index)
- `source` (`String(8)`, nullable=False, check constraint: `source IN ('auto', 'manual', 'sos')`)
- `level` (`SmallInteger`, nullable=False, check constraint: `level BETWEEN 1 AND 3`)
- `title` (`String(120)`, nullable=False)
- `status` (`String(8)`, nullable=False, server_default=`'open'`, check constraint: `status IN ('open', 'closed')`)
- `siren_active` (`Boolean`, nullable=False, server_default=`false`)
- `opened_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `opened_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `closed_at` (`TIMESTAMPTZ`, nullable=True)
- `closed_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `close_note` (`String(500)`, nullable=True)
- **Constraints**:
  - `ck_incidents_closed_sync`: `CHECK ((status = 'closed') = (closed_at IS NOT NULL))`
- **Partial Unique Indexes**:
  - `uq_incidents_open_auto`: `UNIQUE (tunnel_id) WHERE status = 'open' AND source = 'auto'`
  - `uq_incidents_open_sos`: `UNIQUE (tunnel_id) WHERE status = 'open' AND source = 'sos'`
- **Index**:
  - `ix_incidents_mine_opened`: `(mine_id, opened_at DESC)`

### 17. `incident_roster`
- `incident_id` (`BigInteger`, FK -> `incidents.id` ON DELETE CASCADE, nullable=False)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE CASCADE, nullable=False)
- `PRIMARY KEY (incident_id, user_id)`
- `added_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `accounted_at` (`TIMESTAMPTZ`, nullable=True)
- `accounted_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- **Index**:
  - `ix_incident_roster_incident_unaccounted`: `(incident_id) WHERE accounted_at IS NULL`

### 18. `node_commands`
- `id` (`BigInteger`, PK, autoincrement)
- `node_id` (`Integer`, FK -> `nodes.id` ON DELETE CASCADE, nullable=False)
- `cmd` (`String(16)`, nullable=False, check constraint: `cmd IN ('buzzer_on', 'buzzer_off', 'resume')`)
- `seq` (`Integer`, nullable=False, check constraint: `seq BETWEEN 1 AND 65535`)
- `created_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `created_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `expires_at` (`TIMESTAMPTZ`, nullable=False)
- `acked_at` (`TIMESTAMPTZ`, nullable=True)
- **Partial Unique Index**:
  - `uq_node_commands_node_pending`: `UNIQUE (node_id) WHERE acked_at IS NULL`
- **Partial Index**:
  - `ix_node_commands_pending`: `(node_id, expires_at) WHERE acked_at IS NULL`

### 19. `worker_reports`
- `id` (`BigInteger`, PK, autoincrement)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False, index)
- `tunnel_id` (`Integer`, FK -> `tunnels.id` ON DELETE RESTRICT, nullable=True, index)
- `user_id` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True, index)
- `node_id` (`Integer`, FK -> `nodes.id` ON DELETE SET NULL, nullable=True, index)
- `source` (`String(6)`, nullable=False, check constraint: `source IN ('worker', 'device')`)
- `type` (`String(16)`, nullable=False, check constraint: `type IN ('injury', 'equipment_fault', 'gas_smell', 'need_help', 'sos')`)
- `severity` (`String(8)`, nullable=False, check constraint: `severity IN ('low', 'medium', 'high', 'critical')`)
- `description` (`String(1000)`, nullable=True)
- `status` (`String(12)`, nullable=False, server_default=`'open'`, check constraint: `status IN ('open', 'acknowledged', 'resolved')`)
- `created_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `last_signal_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `repeat_count` (`Integer`, nullable=False, server_default=`1`, check constraint: `repeat_count >= 1`)
- `acknowledged_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `acknowledged_at` (`TIMESTAMPTZ`, nullable=True)
- `resolved_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `resolved_at` (`TIMESTAMPTZ`, nullable=True)
- `resolution_note` (`String(500)`, nullable=True)
- `incident_id` (`BigInteger`, FK -> `incidents.id` ON DELETE SET NULL, nullable=True, index)
- **Check Constraints**:
  - `ck_reports_source`: `CHECK (source IN ('worker', 'device'))`
  - `ck_reports_type`: `CHECK (type IN ('injury', 'equipment_fault', 'gas_smell', 'need_help', 'sos'))`
  - `ck_reports_severity`: `CHECK (severity IN ('low', 'medium', 'high', 'critical'))`
  - `ck_reports_status`: `CHECK (status IN ('open', 'acknowledged', 'resolved'))`
  - `ck_reports_repeat_count`: `CHECK (repeat_count >= 1)`
  - `ck_reports_source_shape`: `CHECK ((source = 'device' AND node_id IS NOT NULL AND user_id IS NULL AND type = 'sos') OR (source = 'worker'))`
  - `ck_reports_status_fields`: `CHECK (((status = 'open') AND (acknowledged_at IS NULL) AND (resolved_at IS NULL)) OR ((status = 'acknowledged') AND (acknowledged_at IS NOT NULL) AND (resolved_at IS NULL)) OR ((status = 'resolved') AND (acknowledged_at IS NOT NULL) AND (resolved_at IS NOT NULL)))`
  - `ck_reports_sos_critical`: `CHECK (type <> 'sos' OR severity = 'critical')`
- **Indexes**:
  - `ix_worker_reports_mine_status_created`: `(mine_id, status, created_at DESC)`
  - `ix_worker_reports_user_created`: `(user_id, created_at DESC)`
  - `ix_worker_reports_node_signal`: `(node_id, last_signal_at DESC)`

### 20. `mine_contacts`
- `id` (`Integer`, PK, serial)
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False, index)
- `role` (`String(20)`, nullable=False, check constraint: `role IN ('shift_supervisor', 'safety_officer', 'rescue_station', 'other')`)
- `name` (`String(255)`, nullable=False)
- `phone` (`String(32)`, nullable=False)
- `rank` (`SmallInteger`, nullable=False, check constraint: `rank BETWEEN 1 AND 20`)
- **Constraint**: `UNIQUE (mine_id, rank)` named `uq_mine_contacts_rank`

### 21. `mine_production`
- `mine_id` (`Integer`, FK -> `mines.id` ON DELETE CASCADE, nullable=False)
- `day` (`Date`, nullable=False)
- `planned_t` (`Numeric(12,2)`, nullable=True, check constraint: `planned_t >= 0`)
- `actual_t` (`Numeric(12,2)`, nullable=True, check constraint: `actual_t >= 0`)
- `updated_by` (`Integer`, FK -> `users.id` ON DELETE SET NULL, nullable=True)
- `updated_at` (`TIMESTAMPTZ`, nullable=False, server_default=`now()`)
- `PRIMARY KEY (mine_id, day)`
- **Constraint**: `CHECK (planned_t IS NOT NULL OR actual_t IS NOT NULL)` named `ck_mine_production_has_metric`

> **Mine Profile and Output Tracking (`services/mine_profile_service.py`, `services/production_service.py`, `services/mine_headcount_service.py`, `services/mine_contacts_service.py`)**:  
> - **Five Profile Groups**: Identity & status (`id`, `name`, `status`, `lat`, `lon`, `evacuation_notes`), company & classification (`company_name`, `subsidiary`, `method`, `area_ha`, `max_depth_m`), live headcount (`totals` and per-tunnel breakdown with `SHIFT_LOOKAHEAD_H` lookahead), monthly production (`planned_total_t`, `actual_total_t`, `variance_t`, `achievement_pct`, `reported_days`), and emergency contacts list ordered by call hierarchy `rank`.
> - **Independent Production Sums**: Monthly totals (`planned_total_t` and `actual_total_t`) are summed independently across calendar days of the month in `HOURS_TIMEZONE`. A day with only planned or only actual contributes directly to its respective total without requiring matching pairs.
> - **Worker-Safe View & Emergency Contacts**: Workers receive a trimmed model (`id`, `name`, `status`, `evacuation_notes`, `contacts`, `current_tunnel`). Corporate metrics, production, headcount, depth, area, and lat/lon are omitted. Mine emergency contacts (with phone numbers) are visible to workers by design to enable rapid emergency escalation.
> - **Atomic Contacts Replacement**: Replacing mine contacts validates ranks 1..20 uniqueness, role enum, and phone format before atomically deleting and recreating records within a single transaction. Audit logs record contact count only, never names or phone numbers.

> **Worker Reports Lifecycle and Device SOS Path (`services/report_service.py` & `services/sos_service.py`)**:  
> - **Worker Hazard Reports**: Any logged-in user can submit problem reports (`injury`, `equipment_fault`, `gas_smell`, `need_help`, `sos`). Submitting `type='sos'` strictly forces `severity='critical'`. Rate limited per user to `REPORT_RATE_PER_HOUR` (default 20/hr, returns 429 with `Retry-After`).
> - **Superior Inbox & Status Lifecycle**: Reports move strictly `open -> acknowledged -> resolved`. Resolving directly from `open` records both acknowledgment and resolution simultaneously. Any backward or duplicate transition returns HTTP 409 `{"code": "invalid_transition"}`.
> - **Device SOS Ingestion & Merge Window**: A hardware node packet with `sos_active = 1` forwarded by a gateway merges into the node's latest device-SOS report if its `last_signal_at` is within `SOS_MERGE_WINDOW_S` (default 60s), updating `last_signal_at` and incrementing `repeat_count`. Serialized with `SELECT ... FOR UPDATE` row locking on `nodes`.
> - **Flood Guard**: At most `SOS_MAX_NEW_PER_HOUR` (default 6) new device SOS reports are created per node per hour. Subsequent signals merge into the latest report with a loud `WARNING` log.
> - **Incident & Roll Call Integration**: Placed node SOS automatically creates or reuses an open incident (`source = 'sos'`, level 3) and snapshots all workers currently inside the affected tunnel into `incident_roster`. Unplaced nodes create a report with `tunnel_id = NULL` and open no incident.
> - **503-Then-Retry Contract**: Ingest commits the telemetry reading to the database *before* running SOS handling. If SOS handling fails, HTTP 503 `{"detail": "SOS handling failed"}` is returned. On gateway retry, the reading is identified as a duplicate while still triggering SOS report creation/merging idempotently.
> - **Privacy and Confidentiality**: Worker view (`WorkerReportView`) strictly omits reporter names, user IDs, node numbers, and gateway data. Hardware gateway MAC addresses NEVER appear in reports API responses to any user.

> **Affected Nodes and Scope (Single Source of Truth - `services/incident_service.py`)**:  
> An emergency incident covers either a single tunnel (`tunnel_id` set) or an entire mine (`tunnel_id` is `NULL`). Its **AFFECTED nodes** are all **PLACED** nodes (`is_placed(node)`) of that tunnel or mine whose backhaul gateway is active (`active=True`).

> **Roll Call Snapshot and Evacuation Accounting Rules (`services/incident_service.py`)**:  
> - **Snapshot on Open**: When an incident is opened (manual broadcast or automatic critical hazard transition), a snapshot of all workers currently **INSIDE** the affected tunnel (or all tunnels of the mine) is atomically recorded in `incident_roster` with `accounted_at = NULL`.
> - **Automatic Gate Accounting**: When an unaccounted worker checks out (`direction = 'out'`) at a gate scanner of that tunnel/mine, the system automatically marks them accounted (`accounted_at = now, accounted_by = scanner`).
> - **Late Arrival Accounting**: If a worker checks in (`direction = 'in'`) after incident opening, they are dynamically appended to the incident roster as unaccounted.
> - **Supervisor Manual Accounting**: Superiors (and foremen for their own tunnel) can manually mark a worker accounted via `POST /api/incidents/{id}/roster/{user_id}/account`. Accounting on a closed incident returns HTTP 409 `{"code": "incident_closed", "detail": "Cannot account personnel on closed incident"}`.
> - **Safe Incident Closure & State Modification**: Modifying incident state (`PATCH /api/incidents/{id}`) accepts fields `siren: bool`, `close: bool`, `confirm_unaccounted: bool`, and `close_note: str`. Locks the incident row (`SELECT ... FOR UPDATE`). Closing an incident with unaccounted workers requires explicit confirmation (`confirm_unaccounted: true`); otherwise returns HTTP 409 `{"code": "unaccounted_workers", "detail": "...", "count": N}`. All 409 error responses across the incident APIs follow this normalized top-level structure (`code`, `detail`, and optional metadata like `count`). Closure automatically silences active sirens and audits the closure without leaking note text.

> **Downlink Node Commands & Echo Ack Protocol (`services/node_command_service.py` & `routers/ingest.py`)**:  
> - **Sequence Numbering**: Each command has an integer sequence number `seq` in `1..65535` per node (never 0), wrapping monotonically from 65535 back to 1.
> - **Single Pending Guarantee**: At most one pending command (`acked_at IS NULL` and `expires_at > now`) can exist per node at any time. Creating a new command locks the node row and atomically expires older pending ones.
> - **Delivery via Ingest Response**: When the gateway forwards telemetry via `POST /api/ingest`, the HTTP 200 response body delivers plain-text commands (`CMD,node_no,ACTION,seq\n`).
> - **Echo Acknowledgment**: When the node receives the command, its subsequent telemetry echoes `last_cmd_seq = seq`. Ingest marks the command acked (`acked_at = now`).
> - **Siren Keepalive Refresh Cycle (`services/siren_service.py`)**: While an incident's siren is active, the runner periodically refreshes `buzzer_on` commands every `SIREN_REFRESH_S` (45s), maintaining active audible alarms underground until silenced.

> **Worker "Inside Tunnel" Headcount Definition (Single Source of Truth - `services/headcount.py`)**:  
> A worker is defined as **INSIDE** a tunnel if and only if their latest check event (ordered by `scanned_at DESC, id DESC` using PostgreSQL `DISTINCT ON (user_id)`) has `direction = 'in'`. The tunnel they occupy is that event's `tunnel_id`. This query is calculated across all company tunnels in a single constant pass without per-worker queries.

> **Gate Scan Invariants and Duplicate Suppression (`services/checkin_service.py`)**:  
> - **Duplicate Window**: An identical scan for the same worker, tunnel, and direction within 5.0 seconds is treated as a duplicate scan, returning HTTP 200 with `duplicate: true` and skipping row insertion.
> - **Conflict Detection**: Direction `"in"` while already inside returns HTTP 409 `{"code": "already_checked_in", "tunnel_id"}`; direction `"out"` while outside returns HTTP 409 `{"code": "not_checked_in"}`.
> - **Shift Association Warning**: Checking into a tunnel without an active or imminent scheduled shift (within `CHECKIN_EARLY_MIN` = 120 minutes) returns HTTP 200 with non-blocking warning `{"code": "no_active_shift"}`.
> - **Late Clear on Scan Out**: A successful `"out"` scan automatically resolves any open `check_flags` for that worker with note `"scanned out late"`.
> - **Rate Limiting**: Scanners are rate-limited to `SCAN_RATE_PER_MIN` (default 60/min), and scanner accounts submitting 10 unknown badge tokens within 1 minute receive HTTP 429 with `Retry-After`.

> **Shift Check-Out Exception Flags & Resolution (`services/flag_service.py` & `services/flag_clearing.py`)**:  
> - The background flag cycle evaluates shifts that ended at least `CHECKOUT_GRACE_MIN` (15m) ago (up to 7 days lookback) where the worker checked in during the shift window and remains inside. Idempotently creates at most one `check_flags` entry per assignment.
> - Manual clearance via `PATCH /api/check-flags/{id}` requires a note (3-500 chars), records the clearing actor and timestamp, and automatically appends a manual `"out"` check event for that tunnel, immediately correcting live underground headcount.
> - Flags unclosed after `CHECK_FLAG_OVERDUE_HOURS` (8h) are flagged as `overdue: true` in the API and highlighted in red on the supervisor dashboard. Clear operations write audit entries recording the flag ID only, strictly omitting reviewer note text.

> **Worker "Current Tunnel" and Visible Tunnels Rule (Single Source of Truth - `services/tunnel_scope.py`)**:  
> A worker's current tunnel is determined chronologically:
> 1. The tunnel of the assignment that is active **now** (`starts_at <= now < ends_at`).
> 2. Otherwise, the next **upcoming** one (smallest `starts_at > now`).
> 3. Otherwise, the most recent **past** one (largest `ends_at <= now`).
> 4. Otherwise, `None`.  
> This implements our reading of the specification's "own tunnel" scoping rule. Miners receive `[current_tunnel]` (or `[]` if unassigned), restricting `/api/nodes`, `/api/placements`, and `/api/mines/{id}/tunnels` strictly to their assigned tunnel. Foremen and superiors receive `None` (unrestricted visibility across permitted mines).

### 6.1. Connectivity Rules and Watchdog Architecture

- **Single Source of Truth (`services/connectivity.py`)**:
  - `node_is_online(last_seen_at, now) -> bool`: Returns `True` iff `last_seen_at` is not None and `(now - last_seen_at) <= NODE_OFFLINE_AFTER_S` (default 35s).
  - `gateway_is_online(last_seen_at, now) -> bool`: Returns `True` iff `last_seen_at` is not None and `(now - last_seen_at) <= GATEWAY_OFFLINE_AFTER_S` (default 60s).
- **Core Watchdog Evaluation Order (`services/watchdog_service.py`)**:
  1. **Active and Seen Gateways Only**: Gateways with `active=False` or `last_seen_at IS NULL` (registered but not yet powered on) are ignored to avoid false alarms.
  2. **Dead Gateway Handling**: If a gateway is offline, exactly 1 `gateway_offline` alert is opened (if not already open). Crucially, the watchdog **skips** evaluating that gateway's child nodes (we cannot know if nodes are dead when their backhaul gateway is down) and **auto-resolves** any open `node_offline` alerts for those nodes to eliminate false node alarms.
  3. **Online Gateway Handling**: If an open `gateway_offline` alert exists, it is immediately resolved (`resolved_at = now`). Then, only **PLACED** nodes (`is_placed(node)`) are evaluated. Unplaced spare nodes in inventory never raise alerts. Offline placed nodes open 1 `node_offline` alert; online nodes auto-resolve existing open alerts.
- **Background Daemon Runner (`app/watchdog_runner.py`)**:
  - Executes as a daemon thread started and stopped via FastAPI `lifespan` in `app/main.py`.
  - **Single-Worker PostgreSQL Advisory Lock**: Acquires `pg_try_advisory_xact_lock(8392104)` per cycle. If another worker holds the lock, the cycle skips cleanly.
  - **Startup Grace Period**: Suppresses alert evaluation during the first `NODE_OFFLINE_AFTER_S` seconds following server boot, allowing hardware gateways to transmit their first telemetry before timeouts fire.

> **Why a Partial Unique Index on readings?**  
> While firmware transmits `boot_id = 0`, node transmission counters restart from 0 after each hardware reboot. A strict non-partial unique constraint on `(node_id, boot_id, counter)` would permanently reject valid post-reboot readings that reuse earlier counter values. Therefore, the unique index applies **only** where `boot_id <> 0`. For `boot_id = 0`, deduplication is performed at the application layer with a 4-second time window rule.

### 6.2. Database Backup and Recovery

Database backups and disaster recovery runbooks are managed via `tools/backup_db.sh` and `tools/restore_db.md` using PostgreSQL's native custom-format compressed archive (`pg_dump -Fc`).

- **Automated Backup Script (`tools/backup_db.sh`)**:
  - Reads connection credentials from `DATABASE_URL` (or `.env`), strips driver prefixes, and masks passwords in output.
  - Generates custom compressed dumps named `coalmine_YYYYmmdd_HHMMSS.dump` into `$BACKUP_DIR` (default `backups/`).
  - Automatically prunes older backups to keep the newest `$BACKUP_KEEP` archives (default 14).
  - Exits non-zero (`set -euo pipefail`) upon any error without leaking secrets.

- **Automated Cron Schedule Example**:
  To schedule automated daily database backups at 02:00 AM UTC with 14-day retention:
  ```bash
  # In system crontab (crontab -e):
  0 2 * * * cd /path/to/Mine-Subsidence-Monitoring && BACKUP_DIR=backups BACKUP_KEEP=14 bash tools/backup_db.sh >> /var/log/coalmine_backup.log 2>&1
  ```

- **Database Restoration Runbook (`tools/restore_db.md`)**:
  - Step-by-step restoration instructions using `pg_restore` with `--clean --if-exists` or clean database recreation with `dropdb` / `createdb`.
  - Immediate post-restore schema validation via `python -m alembic -c server/alembic.ini check` to verify zero migration drift.
  - Sanity queries to verify administrative accounts and telemetry records.

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

## 8. API Reference and Consolidated Access Policy Matrix

> **Automated Policy Matrix**: The table below is provably complete across all **71** API routes registered in FastAPI. It is generated by inspecting `app.openapi()` and the test suite's `POLICY_TABLE` (`tests/test_auth_and_roles.py`).  
> - **Roles**: **Manager** (full administrative scope), **Safety Officer** (geotechnical & evacuation authority), **Engineer** (hardware & shifts), **Foreman** (shift & own tunnel scanner), **Miner** (restricted worker-safe view), and **Anonymous** (unauthenticated).  
> - **Status Indicators**: `Allowed` (unrestricted within tenant), `Scoped` (permitted mines only), `Own Tunnel` (restricted to currently assigned tunnel), `403` (forbidden for role), `401` (unauthenticated).

| Method | Path | Policy | Manager | Safety Officer | Engineer | Foreman | Miner | Anon | Summary |
|---|---|---|---|---|---|---|---|---|---|
| `GET` | `/api/alerts` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns connectivity and hazard alerts scoped to permitted mines (superiors only). |
| `POST` | `/api/alerts/{alert_id}/ack` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Acknowledges an alert idempotently within the caller's permitted mines. |
| `GET` | `/api/assignments` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Queries shift assignments within caller's authorized mines, with default window and eager loading. |
| `POST` | `/api/assignments` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Schedules a worker to an underground tunnel shift (Superiors: Manager, Safety Officer, Engineer). |
| `DELETE` | `/api/assignments/{assignment_id}` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Deletes an unstarted future shift (Superiors: Manager, Safety Officer, Engineer). |
| `PATCH` | `/api/assignments/{assignment_id}` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Shortens an active or upcoming shift end time (Superiors: Manager, Safety Officer, Engineer). |
| `POST` | `/api/auth/change-password` | `any_authenticated` | Allowed | Allowed | Allowed | Allowed | Allowed | 401 | Changes password, satisfies force_pw_change gate, and reissues session token. |
| `POST` | `/api/auth/login` | `public` | Allowed | Allowed | Allowed | Allowed | Allowed | Allowed | Authenticates username/password credentials and issues an HttpOnly session cookie. |
| `POST` | `/api/auth/logout` | `public` | Allowed | Allowed | Allowed | Allowed | Allowed | Allowed | Terminates session and clears the HttpOnly cookie. Always returns 200. |
| `GET` | `/api/auth/me` | `any_authenticated` | Allowed | Allowed | Allowed | Allowed | Allowed | 401 | Returns the authenticated user profile and permitted mine scopes. |
| `GET` | `/api/check-flags` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Lists shift check-out exception flags scoped to caller's permitted tunnels. |
| `PATCH` | `/api/check-flags/{flag_id}` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Clears an open check flag with a manual review note and corrects tunnel headcount. |
| `GET` | `/api/checkins` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Lists recent check-in and check-out events scoped to permitted tunnels. |
| `POST` | `/api/checkins` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Records a QR badge scan event at the tunnel portal. |
| `POST` | `/api/checkins/manual` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Manually records a check-in or check-out event with an audit entry. |
| `POST` | `/api/demo/seed` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Seeds demo coal mine dataset for local development (Manager only). |
| `GET` | `/api/demo/status` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Returns current live simulation loop state (Manager only). |
| `POST` | `/api/demo/step` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Triggers a single simulation telemetry step (Manager only). |
| `POST` | `/api/demo/toggle-live` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Toggles periodic live packet generation (Manager only). |
| `POST` | `/api/demo/trigger-hazard` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Injects a hazard override on a specific node (Manager only). |
| `GET` | `/api/export.xlsx` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Excel workbook export with eager relationship loading (superiors only, mine scoped). |
| `GET` | `/api/gateways` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns the gateway hierarchy and node inventory scoped to permitted mines (superiors only). |
| `DELETE` | `/api/gateways/{gateway_id}` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Disables a hardware gateway (Manager only; sets active=false). |
| `PATCH` | `/api/gateways/{gateway_id}` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Updates gateway name or active status (Manager only, within company and mine scope). |
| `POST` | `/api/gateways/{gateway_id}/rotate-key` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Generates a new API key for the gateway, invalidating previous keys (Manager only). |
| `GET` | `/api/headcount` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Returns real-time underground tunnel headcount and active scheduled worker counts. |
| `GET` | `/api/health` | `public` | Allowed | Allowed | Allowed | Allowed | Allowed | Allowed | Returns application health and subsystem statuses. |
| `GET` | `/api/history` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns telemetry points ordered by time for Chart.js inspector (superiors only, mine scoped). |
| `GET` | `/api/incidents` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Lists incidents filtered by status and mine, scoped by role (with limit and offset). |
| `POST` | `/api/incidents` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Superiors only: opens a new emergency or evacuation incident. |
| `GET` | `/api/incidents/{id}` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Retrieves an incident by ID with live roll call metrics. |
| `PATCH` | `/api/incidents/{id}` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Superiors only: closes an incident or modifies siren state (locks incident FOR UPDATE). |
| `GET` | `/api/incidents/{id}/roster` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Lists the snapshot roll call personnel for an incident. |
| `POST` | `/api/incidents/{id}/roster/{user_id}/account` | `scanners` | Scoped | Scoped | Scoped | Own Tunnel | 403 | 401 | Marks a worker accounted for in the incident roll call (locks incident FOR UPDATE). |
| `POST` | `/api/ingest` | `public` | Allowed | Allowed | Allowed | Allowed | Allowed | Allowed | Ingests 38-byte raw binary telemetry packets from authenticated ESP32 gateways. |
| `GET` | `/api/me/profile` | `any_authenticated` | Allowed | Allowed | Allowed | Allowed | Allowed | 401 | Returns caller's own worker profile as a flat object if worker (foreman/miner), otherwise an empty object {}. |
| `GET` | `/api/me/shifts` | `any_authenticated` | Allowed | Allowed | Allowed | Allowed | Allowed | 401 | Returns caller's assigned shifts in a worker-safe view (omitting created_by). |
| `GET` | `/api/mines` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Returns mines permitted to the caller (all roles, scoped). |
| `POST` | `/api/mines` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Creates a new coal mine site for the manager's company and assigns creator. |
| `PUT` | `/api/mines/{id}/contacts` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Replaces all emergency key contacts for a mine (superiors only). |
| `GET` | `/api/mines/{id}/headcount` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns live headcount and shift lookahead for a mine (superiors only). |
| `GET` | `/api/mines/{id}/production` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns daily production records and monthly summary (superiors only). |
| `PUT` | `/api/mines/{id}/production` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Upserts daily production entries (superiors only). |
| `GET` | `/api/mines/{id}/profile` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Returns full profile for superiors or trimmed worker-safe model for workers. |
| `PATCH` | `/api/mines/{id}/profile` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Updates mine metadata with PATCH semantics (superiors only). |
| `GET` | `/api/mines/{mine_id}/gateways` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Lists hardware gateways bound to a mine (Manager only; excludes key/hash). |
| `POST` | `/api/mines/{mine_id}/gateways` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Registers a hardware gateway and displays its API key once (Manager only). |
| `GET` | `/api/mines/{mine_id}/tunnels` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Returns underground tunnels for a mine (all roles, scoped). |
| `POST` | `/api/mines/{mine_id}/tunnels` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Adds a new underground tunnel to a mine (Manager only). |
| `GET` | `/api/nodes` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Returns latest telemetry and placement info scoped to permitted mines. |
| `GET` | `/api/nodes/{node_id}/commands` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Superiors only: lists recent command history and delivery status for a sensor node. |
| `POST` | `/api/nodes/{node_id}/commands` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Superiors only: creates a downlink command for a sensor node. |
| `DELETE` | `/api/nodes/{node_id}/placement` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Removes a node from surface placement back to unplaced status (superiors only). |
| `PATCH` | `/api/nodes/{node_id}/placement` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Assigns tunnel and geospatial coordinates to a node (superiors only, mine scoped). |
| `GET` | `/api/notifications/config` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns stored notification channel settings (superiors only). |
| `POST` | `/api/notifications/config` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Saves notification channel settings (superiors only). |
| `POST` | `/api/notifications/test` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Superiors-only stub for testing alert delivery channels. |
| `GET` | `/api/placements` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Returns placed nodes and their coordinates scoped to permitted mines (any role). |
| `GET` | `/api/reports` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Lists reports scoped by user role. Superiors get full view; workers get own worker-safe view. |
| `POST` | `/api/reports` | `any_role_scoped` | Scoped | Scoped | Scoped | Scoped | Scoped | 401 | Any logged-in user can raise a worker report. Returns worker-safe view. |
| `PATCH` | `/api/reports/{id}` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Superiors only: advances report status to acknowledged or resolved. |
| `GET` | `/api/unplaced` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns unplaced nodes reporting data in permitted mines (superiors only). |
| `GET` | `/api/users` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Returns users belonging to the Manager's company. |
| `POST` | `/api/users` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Creates a new subordinate user within the Manager's company. |
| `DELETE` | `/api/users/{user_id}` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Soft deactivates a user (active=False). Never hard deletes. |
| `PATCH` | `/api/users/{user_id}` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Updates user attributes, permitted mines, or triggers password reset. |
| `GET` | `/api/workers` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Lists mine workers (foremen and miners) within caller's authorized scope. |
| `GET` | `/api/workers/{worker_id}` | `superiors_scoped` | Scoped | Scoped | Scoped | 403 | 403 | 401 | Returns detailed worker profile and shift history within caller's authorized scope. |
| `DELETE` | `/api/workers/{worker_id}/badge` | `manager_or_foreman` | Allowed | 403 | 403 | Allowed | 403 | 401 | Revokes active QR badges for a worker (Manager, or Foreman for their current tunnel). |
| `POST` | `/api/workers/{worker_id}/badge` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Issues a new cryptographic QR badge for an active foreman or miner (Manager only). |
| `PUT` | `/api/workers/{worker_id}/profile` | `manager_only` | Allowed | 403 | 403 | 403 | 403 | 401 | Creates or updates a worker's profile (Manager only; audited without contact leaks). |

### Planned Routes (Future Steps)
- Outbound alert notification delivery and logging (*Planned - Step 10*)

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
| **Step 4** | Watchdog and offline detection | **Done** | Pure watchdog cycle and background runner with Postgres advisory lock, alerts table with partial unique open-alert guarantees, offline threshold evaluations, gateway failure suppression, alert ack lifecycle with audit logging, and token_version session invalidation. | 2026-11-22 |
| **Step 5** | Workers and shifts | **Done** | Manager or Safety Officer assigns worker to a tunnel for a shift; second overlapping shift for the same worker is rejected with 409 naming the clashing shift; the worker sees both upcoming and past shifts under "My hours"; a miner sees only the nodes of their own tunnel (a foreman keeps mine-level view); and the weekly-hours warning appears when the limit is exceeded. | 2026-11-22 |
| **Step 6** | QR check-in and headcount | **Done** | Badges, scanner page, check events, check flags, headcount panel. Scanning in and out changes headcount; missed check-out raises flag. | 2026-11-22 |
| **Step 6.5** | Hardening and Phase A Fixes | **Done** | Flag clearing state-lock, back-to-back shift chaining, headcount & flag Distinct ON constant-time optimization, badge 409 conflict handling, company isolation, and centralized UTC clock. | 2026-11-23 |
| **Step 7** | Incidents and roll call | **Done** | Manual and auto emergency incidents, roll call snapshot, automatic gate-scan accounting, downlink node commands delivery over HTTPS ingest, sequence echoing ack, and siren keepalive refresh cycle. | 2026-11-23 |
| **Step 7.5** | Hardening and Phase A Fixes | **Done** | Gateway-scoped node commands, siren reliability (extend pending, refresh acked > 30s), cross-tunnel scan-out roll call accounting in open incidents, auto-incident config & ingest matrix, incident row locking FOR UPDATE, uniform 409 body shape, command status superseded, 30-day command retention cleanup. | 2026-11-23 |
| **Step 8** | Worker reports and SOS | **Done** | Report form, inbox, status flow, node SOS button and SOS packet. Report moves through workflow; button press shows as SOS report within seconds. | 2026-11-23 |
| **Step 8.5** | Step 8 Follow-up & SOS Hardening | **Done** | SOS incident idempotency on `uq_incidents_open_sos`, resolved report guard, 503 retry contract without reading loss, duplicate reading SOS trigger, and comprehensive follow-up test suite. | 2026-11-24 |
| **Step 9** | Mine profile and output tracking | **Done** | Profile fields, contacts hierarchy, plan vs actual output. Mine page shows all five profile groups. Superiors edit within scope; workers see trimmed worker-safe model. | 2026-11-24 |
| **Step 10** | Outbound notifications and extras | *Not started* | Telegram/WhatsApp outbound dispatch, PDF export, WebSocket updates. | — |

---

## 11. Known Limitations and Deviations

1. **Pre-Step-3 Dev Gateways Key Invalidation**:
   Gateways seeded in earlier steps with `placeholder_dev_api_key_hash` cannot ingest telemetry until their API key is rotated via `POST /api/gateways/{id}/rotate-key` or re-seeded with `DEMO_GATEWAY_KEY`.
2. **Tunnel Scoping (Resolved in Step 5)**:
   Miner visibility is now strictly tunnel-scoped via the `assignments` table, active shift evaluation, and GIST exclusion constraint (`services/tunnel_scope.py`). Foremen retain mine-level visibility.
3. **In-Memory Rate Limiting**:
   Failed login attempts are rate limited in-memory (5 failures per username + IP sliding window over 15 minutes returning HTTP 429 with `Retry-After`). In a multi-worker production deployment, this will be backed by Redis.
4. **Downlink Commands Delivered via Ingest Polling**:
   Node commands (buzzer activation/silencing) are delivered downstream through the gateway's HTTP ingest response body (`CMD,node_no,ACTION,seq\n`). Commands are delivered when the gateway forwards the next telemetry packet. When nodes transmit at 5-10s intervals, command delivery latency is bounded by the node transmission cadence.
5. **Siren Keepalive Refresh Cycle**:
   When an incident's siren is active, the background runner periodically re-queues unacked `buzzer_on` commands every `SIREN_REFRESH_S` (45s), ensuring the command remains pending until the node receives it.
6. **Buzzer Auto-Silence Safety Timer**:
   Node commands have a `COMMAND_TTL_S` (120s) lifespan. On the firmware side, nodes implement an auto-silence safety timer (e.g. 60-120s) so an unacked or lost buzzer command cannot sound indefinitely without keepalive pulses.
7. **HTTPS Enforcement for Node Commands**:
   When `COMMANDS_REQUIRE_HTTPS=true` (the default), downlink command delivery is strictly withheld over plaintext HTTP ingest requests to prevent tampering with buzzer commands across untrusted networks. A rate-limited warning is logged once per hour per gateway. Setting `COMMANDS_REQUIRE_HTTPS=false` or configuring `TRUST_FORWARDED_PROTO=true` behind a TLS-terminating reverse proxy allows local development or behind-proxy operation.
8. **In-App Alerting Scope**:
   In Step 4 and Step 7, alerts and incidents are presented in-dashboard and logged in database tables. Outbound dispatches (Telegram, WhatsApp, cellular SMS, audio broadcasts) are scheduled for Step 10.
9. **No Gateway Heartbeat in Frozen Firmware**:
   Because gateway firmware packets without nodes are not implemented in the frozen firmware struct, a gateway's `last_seen_at` reflects the arrival of its latest forwarded node packet. On quiet tunnels with no active placed nodes transmitting, a gateway could be marked offline unless periodic node transmissions occur or firmware sends status packets.

### 11.1. Firmware TODO (Owner)
The following firmware features from Steps 4, 7, and 8 require modifying hardware logic, packet types, or fields, which are frozen by design in this repository:
- **Node Heartbeat Timing**: Keep node transmission interval <= 10s (or <= 30s) so the offline threshold has at least 3 missed packets margin.
- **Gateway Heartbeats**: ESP32 gateway must transmit a periodic keepalive status packet over HTTP even when no child nodes are transmitting, preventing false gateway-offline timeouts on idle tunnels.
- **ESP Task Watchdog & Sensor-Fault Flag**: Requires additional bit flags in the node telemetry packet payload.
- **X-Gateway-Key Header**: Ensure physical ESP32 gateway firmware transmits the secret provisioned `X-Gateway-Key` HTTP header with all payload ingest requests.
- **Node Physical SOS Button**: Hardware should require a 2s press-and-hold to prevent accidental trigger shock false alarms.
- **Transmission Bursts**: Node should transmit a burst of 3 packets 100ms apart upon SOS button trigger, and continue transmitting telemetry with `sos_active=1` bit set until hardware reset or timeout.
- **Gateway Retry Contract**: On HTTP 503 responses to SOS packets (`{"detail": "SOS handling failed"}`), gateway should retry posting the SOS packet after the specified `Retry-After` interval (2s) without dropping sensor readings.
- **Downlink Buzzer Command Dispatch**: Gateway firmware must parse HTTP 200 plain-text response bodies (`CMD,node_no,ACTION,seq\n`) and forward 10-byte binary command packets over ESP-NOW to target nodes.
- **Command Echo Acknowledgment**: Sensor nodes must update internal state upon receiving downlink radio commands and echo the received command `seq` in subsequent 38-byte telemetry packets via the `last_cmd_seq` field.
- **Acoustic Buzzer Safety Silence Timer**: Hardware sensor nodes should implement an onboard auto-silence timer (60-120s) so unacked or abandoned buzzer alarms do not sound indefinitely during radio link interruptions.
- **SOS_ACK Out of Scope**: Downlink radio acknowledgment (`SOS_ACK`) back to the node is out of scope (requires firmware modification and new radio packet definitions).

### 11.2. Device SOS Path, Privacy, and Ingest Retries (Step 8)
- **End-to-End Device SOS Path**:
  - Telemetry packet arrives with `sos_active = 1` -> handled in `services/sos_service.py` -> 60s merge window per node (`SOS_MERGE_WINDOW_S`) updates `last_signal_at` and increments `repeat_count` without opening duplicate reports.
  - **Flood Guard**: Caps new reports at `SOS_MAX_NEW_PER_HOUR` (default 6) per node per hour; bursts beyond this merge into the latest report with a rate-limited WARNING log.
  - **Placed vs Unplaced Nodes**: An unplaced node creates a device SOS report naming the node and gateway in its description without opening an incident (no tunnel exists). A placed node atomically checks for an existing open SOS incident on that tunnel (protected by partial unique index `uq_incidents_open_sos`) or opens a Level 3 (CRITICAL) SOS incident with an immediate evacuation roll-call snapshot, linking `WorkerReport.incident_id`.
  - **Emergency Banner**: A persistent 3-second polled red SOS banner (`#sosAlertBanner`) in the superior dashboard links directly to the roll call and reports inbox, and remains active until a superior acknowledges or resolves the report.
- **503-Then-Retry Gateway Ingest Contract**:
  - The node sensor reading is **always** committed to the database first.
  - If downstream SOS report or incident creation fails due to a database error or transient condition, the reading remains committed, the error is logged with `exc_info=True`, and the endpoint returns HTTP 503 `{"detail": "SOS handling failed"}` with `Retry-After: 2` header, instructing the ESP32 gateway to retry forwarding the SOS packet.
- **Privacy Rules & Information Isolation**:
  - **Worker View (`WorkerReportView`)**: Minors and foremen see only reports they personally filed (`user_id == current_user.id`), with sensitive metadata (`source`, `user_id`, `node_id`, `node_number`, `gateway_name`, `reporter`, `repeat_count`) strictly omitted.
  - **Superior View (`SuperiorReportView`)**: Superiors see all reports across their assigned mines with telemetry context and full actor attribution.
  - **Gateway MAC Confidentiality**: Physical gateway MAC addresses are internal backhaul identifiers and are never exposed in any report API response.
  - **Audit Privacy**: Audit logs (`report.create`, `report.acknowledge`, `report.resolve`, `sos.device`) record entity IDs and timestamps only, strictly omitting free-text descriptions and resolution notes.

### 11.3. Mine Profile and Production Tracking (Step 9)
1. **No Production History Edits Audit Beyond Counts**:
   Audit logs (`mine.production_update`) record the number of days modified and the list of affected calendar months, not cell-level old vs new tonne values.
2. **Planned and Actual Summed Independently**:
   Monthly totals (`planned_total_t` and `actual_total_t`) are calculated by independently summing daily values across the calendar month in `HOURS_TIMEZONE`. Days with only planned or only actual contribute to their respective total without requiring matching pairs.
3. **No Shift Templates**:
   Underground shift assignments are created with explicit start and end timestamps rather than recurring shift schedule templates.
4. **No CSV Import**:
   Daily planned and actual output is logged via JSON API or through the dashboard mine profile modal; bulk file import (e.g. CSV or Excel) is not implemented.

---

## 12. Changelog

### 2026-11-24 - Phase C: Final Hardening, Operational Tooling, and Architecture Verification
- **Database Backup & Recovery Tooling**:
  - `tools/backup_db.sh`: Automated backup script using `pg_dump -Fc`, connection string parsing, password masking, non-zero error exits, and rolling 14-day retention pruning.
  - `tools/restore_db.md`: Comprehensive restoration runbook covering clean restore (`pg_restore --clean --if-exists`), fresh database drop/create, post-restore `alembic check` drift verification, and sanity SQL queries.
- **Migration Round-Trip Verification**:
  - Successfully executed throwaway DB test (`alembic downgrade base` -> `alembic upgrade head` -> `alembic check` clean with zero schema drift).
- **Security & Secrets Hygiene**:
  - `git ls-files` scan for secrets/keys/credentials completely clean (0 matches).
  - `git grep` credential scan across non-test code clean (0 matches).
  - `.gitignore` updated to cover `backups/` and `*.dump`.
- **Dependency Pinning & Integrity**:
  - Fully pinned all production and development dependencies in `server/requirements.txt` and `server/requirements-dev.txt` using `==`.
  - Verified `pip check` reports `No broken requirements found.`
- **Dead Code Cleanup & Pyflakes Verification**:
  - Executed `pyflakes server/app`: resolved unused imports, resolved unused variables, and fixed undefined reference in `services/flag_service.py` (`now = clock.utcnow()`).
  - Total pyflakes warnings across application package: 0.
- **Automated Route Matrix Generation**:
  - Created `tools/generate_route_table.py` which inspects `app.openapi()` and `POLICY_TABLE`, guaranteeing 100% coverage across all 71 API routes and 6 user roles.
- **Test Suite Regression**:
  - All 161 automated test cases passing in pytest (0 failures).

### 2026-11-24 - Step 9: Mine Profile, Emergency Contacts Hierarchy, and Output Tracking
- **Five Profile Groups**:
  - Identity and status (`id`, `name`, `status`, `lat`, `lon`, `evacuation_notes`).
  - Company and classification (`company_name`, `subsidiary`, `method`, `area_ha`, `max_depth_m`).
  - Headcount breakdown by shift and tunnel with `SHIFT_LOOKAHEAD_H` lookahead (totals + per-tunnel inside, scheduled now, upcoming).
  - Monthly coal production summary (`planned_total_t`, `actual_total_t`, `variance_t`, `achievement_pct`, `reported_days`).
  - Key emergency contacts hierarchy ordered by `rank` (1..20).
- **Worker-Safe View & Privacy**:
  - Foremen and miners receive a trimmed worker-safe model (`id`, `name`, `status`, `evacuation_notes`, `contacts`, `current_tunnel`).
  - Corporate classification, production, headcount, depth, area, and lat/lon are strictly omitted.
  - Mine emergency contacts (with phone numbers) are visible to workers by design for rapid emergency escalation.
- **Production Tracking Domain & Validations**:
  - `mine_production` composite primary key `(mine_id, day)`, checks `planned_t >= 0`, `actual_t >= 0`, and at least one metric not null.
  - Future `actual_t` rejected in `HOURS_TIMEZONE`. Maximum lookback of 5 years. Upsert up to 62 entries per call.
- **Automated Verification**:
  - Added `tests/test_step9_mine_profile_and_production.py` with 11 comprehensive tests covering all five groups, worker view redaction, scoping, atomic contacts replacement, production upsert/summary, direct check constraints, query scalability, and migration downgrade/upgrade round-trip. Total suite: 161 tests passing.

### 2026-11-24 - Step 8 Follow-up: SOS Resilience, Idempotency, and Merge Life Cycle
- **SOS Incident Idempotency & Conflict Handling**:
  - `services/sos_service.py` ensures an open SOS incident on EVERY signal (new reports, window merges, and flood guard merges).
  - Explicitly catches `IntegrityError` only for constraint `uq_incidents_open_sos`, looking up and reusing the conflicting open SOS incident. Any other integrity or database error is logged with `logger.exception` and re-raised.
  - Strict company isolation: removed fallback to company 0; gateway's company must resolve or raises `ValueError`.
- **Report Lifecycle & Flood Guard Guarding**:
  - Merge window (60s) and flood guard (6 new/hour) only merge into an `open` or `acknowledged` report. A `resolved` report never absorbs a new signal, ensuring subsequent signals spawn fresh open reports.
- **Ingest Pipeline & 503 Retry Contract**:
  - `services/ingest_service.py` commits sensor telemetry readings before invoking SOS handling.
  - Runs SOS processing for duplicate packets as well as new packets.
  - When SOS handling fails, rolls back SOS transaction while preserving committed reading, and raises `SOSHandlingError` which returns HTTP 503 `{"detail": "SOS handling failed"}` with `Retry-After: 2` header for gateway retry.
- **Automated Verification**:
  - Added `tests/test_step8_followup_sos.py` with 9 comprehensive tests:
    1. `test_signal_merged_into_acknowledged_report`
    2. `test_signal_after_report_resolved_creates_new_report`
    3. `test_flood_guard_merges_only_unresolved`
    4. `test_open_incident_failure_contract_and_retry`
    5. `test_merged_signal_creates_missing_incident`
    6. `test_unique_index_race_reuses_existing_incident`
    7. `test_sos_on_duplicate_reading_creates_or_merges`
    8. `test_concurrent_signals_barrier`
    9. `test_unplaced_node_report_no_incident`
  - Total test suite: **150 passed in pytest (0 failures)**.
  - `alembic check` clean with zero schema drift.

### 2026-11-23 - Step 8: Worker Reports and Device SOS Path (Phase B)
- **Database Migration (`5d042c33b47c`)**:
  - Created `worker_reports` table with `id` (bigserial PK), `mine_id` (FK mines CASCADE), `tunnel_id` (FK tunnels RESTRICT, nullable), `user_id` (FK users SET NULL, nullable), `node_id` (FK nodes SET NULL, nullable), `source` (`CHECK IN ('worker', 'device')`), `type` (`CHECK IN ('injury', 'equipment_fault', 'gas_smell', 'need_help', 'sos')`), `severity` (`CHECK IN ('low', 'medium', 'high', 'critical')`), `description` (varchar(1000)), `status` (`CHECK IN ('open', 'acknowledged', 'resolved')`, default `'open'`), `created_at` (timestamptz default now()), `last_signal_at` (timestamptz default now()), `repeat_count` (integer default 1, CHECK >= 1), `acknowledged_by` (FK users SET NULL), `acknowledged_at` (timestamptz), `resolved_by` (FK users SET NULL), `resolved_at` (timestamptz), `resolution_note` (varchar(500)), `incident_id` (FK incidents SET NULL).
  - Enforced source shape constraint `ck_reports_source_shape`: `worker` source requires `user_id IS NOT NULL` and `node_id IS NULL`; `device` source requires `user_id IS NULL` and `node_id IS NOT NULL`.
  - Enforced status lifecycle constraints `ck_reports_status_fields` ensuring acknowledged and resolved records carry their respective actor and timestamp.
  - Enforced `ck_reports_sos_critical`: reports of type `sos` strictly require `severity = 'critical'`.
  - Added partial UNIQUE index `uq_incidents_open_sos` on `incidents(tunnel_id) WHERE status = 'open' AND source = 'sos'` (strictly at most one open SOS incident per tunnel).
  - Added compound indexes: `ix_worker_reports_mine_status_created` on `(mine_id, status, created_at DESC)`, `ix_worker_reports_user_created` on `(user_id, created_at DESC)`, and `ix_worker_reports_node_signal` on `(node_id, last_signal_at DESC)`.
  - Verified bidirectional migration integrity with `alembic downgrade -1` and `alembic upgrade head`, and confirmed zero schema drift with `alembic check`.
- **Worker Reports Domain Service (`services/report_service.py`)**:
  - `create_worker_report`: Validates permitted mine/tunnel scope, forces severity `critical` for `type='sos'`, enforces sliding in-memory rate limit (20 reports/hour per user returning HTTP 429 with `Retry-After`). Records atomic audit `report.create` strictly omitting description text.
  - `acknowledge_report`: Locks report row (`SELECT ... FOR UPDATE`), transitions `open -> acknowledged`, records actor and timestamp, logs audit `report.acknowledge`.
  - `resolve_report`: Locks report row (`SELECT ... FOR UPDATE`), transitions `open|acknowledged -> resolved` (recording acknowledgement automatically if resolving directly from open), records note (max 500 chars), logs audit `report.resolve` strictly omitting note text.
  - Rejects backwards or repeated transitions with HTTP 409 `{"code": "invalid_transition"}`.
- **Device SOS Ingest & Merging Service (`services/sos_service.py`)**:
  - Triggered in `services/ingest_service.py` whenever telemetry packet carries `sos_active == 1` (even for deduplicated telemetry packets).
  - Concurrency serialization via node row lock (`SELECT ... FOR UPDATE on nodes`).
  - 60-second merge window (`SOS_MERGE_WINDOW_S`): Packets from the same node within 60s increment `repeat_count` and refresh `last_signal_at` without duplicating report rows.
  - Hourly flood guard (`SOS_MAX_NEW_PER_HOUR=6`): Caps new reports at 6 per node per hour; subsequent packets merge into the latest report with a rate-limited WARNING log.
  - Placed nodes open an SOS incident with immediate roll call snapshot (linking `WorkerReport.incident_id`), respecting `SOS_INCIDENT_SIREN` (default false).
  - Unplaced nodes create device reports without incidents, recording gateway name and node number in description.
  - HTTP 503 retry contract: Sensor reading is always committed; if SOS handling raises an exception, commits reading, logs traceback with `exc_info=True`, and returns HTTP 503 `{"detail": "SOS handling failed"}` with `Retry-After: 2`.
- **REST Endpoints & Access Control (`routers/reports.py`)**:
  - `POST /api/reports`: Any authenticated user; rate limited; returns `WorkerReportView`.
  - `GET /api/reports`: Filter by `status`, `type`, `mine_id`, `tunnel_id`, `limit` (default 100, max 500), `offset`. Superiors receive `SuperiorReportView` (full telemetry, actors, repeat counts); workers receive `WorkerReportView` (own reports only, stripped of sensitive fields). Gateway MAC address is never returned.
  - `PATCH /api/reports/{id}`: Superiors only; transition status; returns `SuperiorReportView`.
  - Synchronized `POLICY_TABLE` across all 3 routes in `tests/test_auth_and_roles.py`.
- **Simulator & Hardware SOS Tooling (`dev/simulator.py`)**:
  - Added `--sos` CLI argument to `app.dev.simulator` allowing on-demand simulation of physical node SOS button presses.
  - Implemented `send_sos()` function transmitting telemetry packets with `sos_active = 1`.
- **Frontend Dashboard Integration (`static/reports.js`, `index.html`)**:
  - Added Worker Reporting Modal: Hazard type dropdown, tunnel selector, description input, prominent red SOS button with confirm dialog, and "My reports" table with status badges.
  - Added Superior Reports Inbox: Status and type filters, acknowledge and resolve actions with resolution note modal, device SOS metadata (node number, gateway name, tunnel, repeat count), and roll call links for SOS incidents.
  - Added Persistent Red SOS Banner (`#sosAlertBanner`): Polled every 3 seconds, loudly alerts superiors to open SOS reports across their mines until acknowledged or resolved.
- **Automated Verification**:
  - Added `tests/test_step8_reports_and_sos.py` with 10 comprehensive tests covering worker report creation & scoping, worker-safe view vs superior view field leakage, rate limiting (21st in 1h -> 429), status transitions & RBAC, database check constraints reject invalid rows, device SOS ingest flow (placed vs unplaced, burst merge, merge window, flood guard, deduped reading SOS trigger, 503-then-retry contract, MAC confidentiality), device SOS concurrency serialization via row lock, simulator SOS packet verification, Done-When full lifecycle journey with frozen clock, and Alembic downgrade/upgrade roundtrip.
  - Full test suite: **141 passed in pytest (0 failures)**.
  - `alembic check` clean with zero schema drift.

### 2026-11-23 - Step 7.5: Hardening (Phase A)
- **Security & Gateway-Scoped Node Commands (A1)**:
  - Scoped node commands strictly through node backhaul gateway (`node -> gateway -> gateway.mine -> mine.company_id` and `gateway.mine_id in allowed_mines`).
  - Access attempts on out-of-scope nodes return HTTP 404 identical to not found for both placed and unplaced nodes across POST and GET.
  - `POST /api/nodes/{id}/commands` requires node to be placed (`is_placed(node)`) and its gateway active (`gateway.active`), otherwise returning HTTP 404 `{"detail": "Node not found"}`. GET `/api/nodes/{id}/commands` requires company scope only.
- **Siren Reliability & Retention Purge (A2)**:
  - Updated `SIREN_REFRESH_S` default to 30 (validated $5 \le \text{value} \le 40$).
  - Watchdog runner executes siren keepalive job every `min(10, SIREN_REFRESH_S / 3)` seconds, decoupled from `WATCHDOG_INTERVAL_S`.
  - In `services/siren_service.py`: `run_siren_cycle` extends `expires_at` for pending (unacked, unexpired) `buzzer_on` commands instead of duplicating rows; creates a new command with new sequence number only when the previous command was acked and older than `SIREN_REFRESH_S`.
  - Nodes manually silenced via `buzzer_off` after the incident's last siren-on event are skipped by the keepalive cycle until the incident siren is toggled on again.
  - Retention purge: watchdog background cycle deletes node commands that are acked or expired and older than `COMMAND_RETENTION_DAYS` (default 30 days).
- **Cross-Tunnel Scan-Out Roll Call Accounting (A3)**:
  - Created `services/roll_call_service.py` to house evacuation roll-call functions and maintain file size compliance under ~300 lines.
  - Gate check-out (`direction = 'out'`) accounts the worker in every open incident of the company whose roster contains them, regardless of the tunnel where the scan occurred.
  - Scanner path writes zero audit rows (manual supervisor accounting via `/account` retains `incident.account` audit logging).
- **Auto-Incident Ingest Matrix & Failure Isolation (A4, A5)**:
  - Added `AUTO_INCIDENT_SIREN` configuration setting (default `true`) allowing operational disabling of automatic sirens on critical sensor readings.
  - Telemetry ingest matrix verified: level $0 \to 3$ critical transition on placed node opens exactly one auto incident; repeated critical packet skips duplicate opening; warning level 2 opens none; unplaced node critical reading opens none.
  - Failures in auto-incident creation log tracebacks with `exc_info=True` and never disrupt the HTTP 200 telemetry ingest response or reading persistence.
  - Unkeyed ingest (`ALLOW_UNKEYED_INGEST=true`) strictly returns an empty HTTP body `""`, withholding downlink command lines.
- **Incident API Hardening & Normalized 409 Error Shapes (A6)**:
  - Attempted roll call accounting on a closed incident returns HTTP 409 `{"code": "incident_closed", "detail": "Cannot account personnel on closed incident"}`.
  - `PATCH /api/incidents/{id}` and roster accounting lock incident rows (`SELECT ... FOR UPDATE OF incidents`) to serialize concurrent mutations.
  - Added `limit` (default 100, max 500) and `offset` pagination to `GET /api/incidents`.
  - Added `late_arrival` (`added_at > incident.opened_at`) boolean field to `IncidentRosterItem`.
  - Normalized all 409 error response bodies to have `code`, `detail`, and optional metadata (`count`) at the top level.
- **Command Status Lifecycle (A7)**:
  - `POST /api/nodes/{id}/commands` returns `"status": "pending"`.
  - `GET /api/nodes/{id}/commands` computes `"superseded"` when a command is unacked and its `expires_at` is earlier than `created_at + COMMAND_TTL_S`.
- **Automated Verification (A8)**:
  - Added comprehensive test suite in `tests/test_step7_hardening.py` covering gateway scoping, unplaced dispatch rejection, inactive gateway handling, runner session persistence, manual silencing immunity, pending expiration extension, 30-day watchdog retention, cross-tunnel roll-call accounting, auto-incident matrix, closed incident conflict, `X-Forwarded-Proto` trust, unkeyed command withholding, gateway command isolation, and invalid seq acknowledgment.
  - Full test suite: **131 passed in pytest (0 failures)**.
  - `alembic check` clean with zero schema drift.

### 2026-11-23 - Step 7: Emergency Incidents, Roll Call Snapshot, Downlink Node Commands, and Siren Keepalive
- **Database Migration (`a69df82fa18b`)**:
  - Created `incidents` table with `id` (bigserial PK), `mine_id` (FK mines CASCADE), `tunnel_id` (FK tunnels RESTRICT, nullable), `source` (`CHECK IN ('auto', 'manual', 'sos')`), `level` (`CHECK BETWEEN 1 AND 3`), `title` (varchar(120)), `status` (`CHECK IN ('open', 'closed')`, default `'open'`), `siren_active` (boolean, default false), `opened_by` (FK users SET NULL), `opened_at` (timestamptz default now()), `closed_at`, `closed_by`, `close_note`.
  - Added CHECK constraint `ck_incidents_closed_sync` enforcing `(status = 'closed') = (closed_at IS NOT NULL)`.
  - Added PostgreSQL partial UNIQUE index `uq_incidents_open_auto` on `(tunnel_id) WHERE status = 'open' AND source = 'auto'` (strictly one open auto incident per tunnel).
  - Added index `ix_incidents_mine_opened` on `(mine_id, opened_at DESC)`.
  - Created `incident_roster` table with `incident_id` (FK incidents CASCADE), `user_id` (FK users CASCADE), composite PRIMARY KEY `(incident_id, user_id)`, `added_at` (timestamptz default now()), `accounted_at`, `accounted_by` (FK users SET NULL). Partial index `ix_incident_roster_incident_unaccounted` on `(incident_id) WHERE accounted_at IS NULL`.
  - Created `node_commands` table with `id` (bigserial PK), `node_id` (FK nodes CASCADE), `cmd` (`CHECK IN ('buzzer_on', 'buzzer_off', 'resume')`), `seq` (`CHECK BETWEEN 1 AND 65535`), `created_by` (FK users SET NULL), `created_at` (timestamptz default now()), `expires_at` (timestamptz), `acked_at`.
  - Added PostgreSQL partial UNIQUE index `uq_node_commands_node_pending` on `(node_id) WHERE acked_at IS NULL` (guarantees at most one pending command per node) and partial index `ix_node_commands_pending` on `(node_id, expires_at) WHERE acked_at IS NULL`.
  - Verified bidirectional migration integrity with `alembic downgrade -1` and `alembic upgrade head`, and confirmed zero schema drift with `alembic check`.
- **Incident Domain Service & Roll Call Accounting (`services/incident_service.py`)**:
  - `open_incident`: Takes snapshot of all workers currently inside the affected tunnel(s) via `services/headcount.py` into `incident_roster`. If `siren=True`, queues `buzzer_on` commands for all affected placed nodes under active gateways. Records atomic audit log `incident.open`.
  - `auto_open_for_critical`: Detects transition to hazard level 3 (CRITICAL) for placed nodes during ingest; wrapped in atomic savepoint `db.begin_nested()`, catches duplicate auto-incident race condition without ever failing telemetry ingestion.
  - Automatic gate scan accounting: Hooked into `services/checkin_service.py` `record_scan` to automatically mark unaccounted workers accounted upon gate check-out (`direction = 'out'`).
  - Late arrival tracking: Automatically appends workers scanning into an incident tunnel (`direction = 'in'`) after open time to the incident roll call as unaccounted.
  - `mark_accounted`: Supervisor manual roll call accounting with optional note. Foremen scoped to own active tunnel. Audits `incident.account` with user ID.
  - `close_incident`: Validates that all workers are accounted for; if unaccounted workers remain, requires `confirm_unaccounted=True` or rejects with HTTP 409 `{"code": "unaccounted_workers", "count"}`. Atomically silences active sirens with `buzzer_off` commands and records `incident.close` audit row strictly omitting note text.
  - `set_siren`: Toggles incident siren on/off, generating corresponding `buzzer_on` or `buzzer_off` node commands.
- **Node Command Queueing & Downlink Delivery (`services/node_command_service.py` & `routers/ingest.py`)**:
  - `create_command`: Locks target node row (`SELECT ... FOR UPDATE`), atomically expires any existing pending command for that node, allocates next sequence number `1..65535` wrapping monotonically from 65535 back to 1 (never 0), and creates pending command with TTL = `COMMAND_TTL_S` (120s). Records audit log `node.command`.
  - Dual-thread concurrency test proves zero duplicate sequence numbers under simultaneous queueing.
  - `pending_lines_for_gateway`: Collects newest unexpired pending commands for placed nodes under the gateway, formatted strictly as `CMD,<node_no>,<ACTION>,<seq>\n`.
  - Downlink delivery in ingest response: Handled in `routers/ingest.py` as `text/plain` response to gateway telemetry POST.
  - Ingest HTTPS enforcement: When `COMMANDS_REQUIRE_HTTPS=true` (default), commands are withheld over plaintext HTTP with an hourly rate-limited warning log per gateway. Supports `TRUST_FORWARDED_PROTO=true` behind reverse proxies.
  - `mark_acked`: When node echoes `last_cmd_seq` in subsequent telemetry packets, matches pending command and records `acked_at = now`.
- **Background Siren Keepalive Refresh Daemon (`services/siren_service.py` & `watchdog_runner.py`)**:
  - Background loop acquires PostgreSQL transaction advisory lock `SIREN_LOCK_KEY` (8392106).
  - Evaluates open incidents with `siren_active=True`, finding placed nodes without pending commands or whose newest unexpired command was created $> `SIREN_REFRESH_S` (45s) ago, re-queueing `buzzer_on` commands to ensure audible alerts remain active underground.
- **REST APIs & Role Matrix**:
  - `POST /api/incidents`: Superior broadcast / open incident.
  - `GET /api/incidents`: Filter by `status=open|all` and `mine_id`. Workers see open incidents for their tunnel.
  - `GET /api/incidents/{id}/roster`: Roll call list with accounted status and timestamps. Foremen scoped to own tunnel.
  - `POST /api/incidents/{id}/roster/{user_id}/account`: Mark worker accounted.
  - `PATCH /api/incidents/{id}/close`: Close incident (requires confirmation if unaccounted).
  - `PATCH /api/incidents/{id}/siren`: Toggle siren active state.
  - `POST /api/nodes/{id}/commands`: Manual node command dispatch (superiors and foremen for own tunnel).
  - Synchronized `POLICY_TABLE` across all 8 new routes in `tests/test_auth_and_roles.py`.
- **Frontend Dashboard Enhancements (`static/incidents.js`, `index.html`)**:
  - Added Incidents console modal (`#incidentsModal`) with open incident banner, mine/tunnel selectors, severity buttons, siren toggle, and active incidents table.
  - Added Roll Call modal (`#rollCallModal`) showing accounted vs unaccounted summary, highlighted unaccounted badges, gate check-out times, and "Mark Accounted" supervisor buttons.
  - Added Close Incident confirmation modal (`#closeIncidentModal`) warning about unaccounted miners and requiring explicit confirmation checkbox.
  - Added Node Buzzer controls in node inspector drawer with live buzzer state indicator and "Sound Buzzer" / "Silence" buttons.
  - Added prominent red emergency alert bar (`#incidentAlertBanner`) displaying active incident title with direct "View Roll Call" action.
- **Automated Verification**:
  - Added `tests/test_step7_incidents_and_commands.py` with 14 comprehensive test cases covering manual open and roster snapshot, gate scan accounting and late arrival, manual roll call accounting with foreman scoping, close incident unaccounted conflict and confirm, auto-incident open on critical reading, node commands ingest delivery and echoed ack, command expiry and replacement, sequence number 65535 wrapping, HTTPS withholding, siren keepalive refresh cycle, role matrix and tenant scoping isolation, dual-thread concurrent command queueing, Alembic downgrade/upgrade, and the full Done-When journey.
  - Full test suite: 118 passed in pytest (0 failures).
  - `alembic check` clean with zero schema drift.

### 2026-11-23 - Step 6.5: Hardening
- **Flag Clearance State Verification & Headcount Protection (A1)**:
  - In `services/flag_clearing.py`: `clear_flag` locks the worker row (`SELECT ... FOR UPDATE on users`) matching the scan path.
  - Inserts manual `"out"` check event ONLY if the worker's latest check event is `"in"` on the flagged assignment's tunnel and not on a later shift.
  - If worker already checked out, is inside a different tunnel, or is on a later shift, clears the flag with note without inserting an out event or touching live headcount.
- **Back-to-Back Shift Chaining (A2)**:
  - In `services/flag_service.py`: Consecutive shifts for the same worker in the same tunnel with gap $\le$ `CHECKOUT_GRACE_MIN` are evaluated as a single shift chain.
  - Evaluates flag against the last shift of the chain: raises flag only when the last shift's `ends_at + CHECKOUT_GRACE_MIN <= now`, worker's latest event is `"in"` on that tunnel, and event's `scanned_at` is within `[chain_first_starts_at - CHECKIN_EARLY_MIN, last_ends_at)`.
  - Shifts are never flagged while the worker has an active shift in that tunnel.
- **Headcount Query Optimization & Pydantic Response Model (A3)**:
  - In `services/headcount.py`: Restricts evaluation strictly to workers belonging to the requested tunnels' mines with a single `DISTINCT ON` query and zero per-user queries (constant-time query count verified with tests for 10 vs 100 workers).
  - Updated `GET /api/headcount` response model to `HeadcountResponse(tunnels=[TunnelHeadcountItem(...)])` with inside counts, scheduled counts, and workers list.
  - Scoped to foreman's active tunnel (others 403/404); updated frontend `static/headcount_flags.js` to consume the new response schema.
- **Flag Cycle Pre-Filtering (A4)**:
  - In `services/flag_service.py`: Candidate workers' latest check event queried via a single `DISTINCT ON` subquery; pre-filters candidate shifts in SQL where the worker's latest event is `"in"`, ensuring flag evaluation cost does not grow with normally closed shifts.
- **Scan Validation, UnknownBadge Handling & Schema Cleanup (A5)**:
  - In `services/checkin_service.py`: Worker must belong (`user_mines`) to the scanned tunnel's mine (raising 404 otherwise).
  - Introduced dedicated `UnknownBadge(CheckInError)` exception subclass; rate limiting catches `UnknownBadge` directly without fragile string matching.
  - Removed unused `note` field from `ManualScanRequest` with `extra="forbid"`.
- **Badge Concurrency & Revocation Scoping (A6)**:
  - In `services/badge_service.py`: `issue_badge` acquires row lock on `User`, flushes old token revocation before inserting new token, and catches `IntegrityError` on `uq_qr_tokens_one_active` returning HTTP 409 `{"code": "badge_busy"}`.
  - `revoke_badge` strictly enforces company isolation inside the domain service (404 otherwise).
- **Check Flags Validation & Pagination (A7)**:
  - In `routers/check_flags.py`: Validated `status` with regex pattern `^(open|all)$`, added `limit` (default 200, max 500) and `offset` (default 0).
  - Documented flat profile object in `routers/me.py`.
- **Centralized Timezone-Aware UTC Clock (A8)**:
  - Created `services/clock.py` exporting `utcnow()` providing timezone-aware UTC datetime.
  - Replaced all direct `datetime.datetime.now(...)` invocations across routers, services, and security.
  - Added `freeze_clock` test fixture monkeypatching `app.services.clock.utcnow`; removed all patching of the stdlib `datetime` module.
- **Step 6 Hardening Tests & Invariant Verification (A9)**:
  - Added tests for identical 404 responses across revoked, deactivated, and unknown badges (`test_revoked_and_deactivated_badges_identical_404`).
  - Added scanner mine scoping and foreman cross-tunnel 403 tests (`test_scanner_mine_scoping_and_foreman_restrictions`).
  - Added warning verification for scans outside scheduled shifts (`test_no_active_shift_warning`).
  - Added early check-in grace (`CHECKIN_EARLY_MIN`) and overdue boundary (`CHECK_FLAG_OVERDUE_HOURS`) tests (`test_early_checkin_grace_and_flag_boundaries`).
  - Added late scan-out auto-clearing test (`test_late_scan_out_autoclears_flag`).
  - Added foreman own-tunnel flag clearance restriction, audit row note sanitization, and `cmb_` string leakage checks (`test_flag_clearing_scoping_and_audit_privacy`).
  - Added all three flag-clearing state cases without manual out events (`test_flag_clearing_three_cases`).
  - Added back-to-back shift chaining test (`test_back_to_back_shifts_chaining`).
  - Added concurrent badge issuance test (`test_concurrent_badge_issuance`).
  - Added constant query count test for 10 vs 100 workers (`test_headcount_constant_query_count`).
  - Added foreman headcount scope and company isolation test (`test_foreman_headcount_scope_and_company_isolation`).
  - Added check flags status validation and pagination test (`test_check_flags_validation_and_pagination`).
- **Alembic Downgrade/Upgrade Invariant (A10)**:
  - Wrapped `alembic downgrade -1` in a `try...finally` block ensuring `command.upgrade(cfg, "head")` executes even on assertion failures, protecting the shared test database.
- **Automated Verification**:
  - Full test suite: 104 passed in pytest (0 failures).
  - `alembic check` clean with zero schema drift.

### 2026-11-22 - Step 6: QR Badges, Gate Check-In/Out, Live Headcount, and Check Flags
- **Database Migration (`e2b8b74826ca`)**:
  - Dropped redundant single-column indexes `ix_assignments_user_id` and `ix_assignments_tunnel_id` (covered by compound indexes `ix_assignments_user_id_starts_at` and `ix_assignments_tunnel_id_starts_at`).
  - Created `qr_tokens` table with `id` (bigserial PK), `user_id` (FK users CASCADE), `token_hash` (varchar(64) UNIQUE), `issued_at`, `issued_by`, `revoked_at`, `revoked_by`. Partial UNIQUE index `uq_qr_tokens_one_active` on `(user_id) WHERE revoked_at IS NULL` strictly enforces at most one active badge per worker.
  - Created `check_events` table with `id` (bigserial PK), `user_id` (FK users CASCADE), `tunnel_id` (FK tunnels RESTRICT), `direction` (CHECK IN `'in', 'out'`), `method` (CHECK IN `'qr', 'manual'`), `scanned_at`, `scanned_by`. Compound indexes on `(user_id, scanned_at DESC)` and `(tunnel_id, scanned_at DESC)`.
  - Created `check_flags` table with `id` (bigserial PK), `user_id` (FK users CASCADE), `assignment_id` (FK assignments CASCADE UNIQUE), `raised_at`, `cleared_by`, `cleared_at`, `note` (varchar(500)). Partial index `ix_check_flags_raised_at` on `(raised_at) WHERE cleared_at IS NULL`.
  - Tested migration downgrade `-1` and upgrade `head` cleanly with zero drift on `alembic check`.
- **Cryptographic Badge Lifecycle (`services/badge_service.py`)**:
  - Token generation: `cmb_` + `secrets.token_urlsafe(32)` (256-bit entropy).
  - SHA-256 digest stored in database; plaintext token returned once on issuance and never persisted or logged.
  - Inline QR SVG rendered via `segno` (error correction level 'M', scale 6).
  - Atomic revocation of prior active badges upon re-issuance.
  - Audit logging (`badge.issue`, `badge.revoke`) strictly omitting secret tokens or hashes.
- **Gate Check-In/Out Engine (`services/checkin_service.py`)**:
  - Atomic row locking (`SELECT id FROM users WHERE id = :worker_id FOR UPDATE`) prevents concurrent scan race conditions.
  - 5-second duplicate suppression window returns HTTP 200 with `duplicate: true`.
  - State transitions strictly validated: check-in while inside returns HTTP 409 `{"code": "already_checked_in", "tunnel_id"}`; check-out while outside returns HTTP 409 `{"code": "not_checked_in"}`.
  - Imminent shift check emits non-blocking warning `{"code": "no_active_shift"}` when checking in without a scheduled shift within `CHECKIN_EARLY_MIN` (120 min).
  - Check-out scan automatically resolves open check flags with note `"scanned out late"`.
  - Rate limiting via sliding in-memory window (`SCAN_RATE_PER_MIN` = 60/min and 10 unknown badge attempts/min returning HTTP 429 with `Retry-After`).
- **Live Headcount & Location Tracking (`services/headcount.py`)**:
  - Single source of truth: Worker is underground if and only if their latest check event (by `scanned_at DESC, id DESC` using PostgreSQL `DISTINCT ON`) has `direction = 'in'`.
  - Constant-time single query calculates live inside workers and active scheduled workers across all tunnels.
- **Shift Check-Out Flags & Daemon Integration (`services/flag_service.py`, `watchdog_runner.py`)**:
  - Background runner evaluates shifts ended past `CHECKOUT_GRACE_MIN` (15m) without check-out, raising idempotent `check_flags`.
  - Runner acquires PostgreSQL advisory lock `FLAG_LOCK_KEY` (8392105), running independently from connectivity watchdog.
  - Manual flag clearance (`services/flag_clearing.py`) validates note (3-500 chars), records clearance timestamp and actor, and atomically appends a manual `"out"` check event that corrects live underground headcount.
  - Audits flag clearance recording flag ID only (strictly omitting review note text).
- **REST Endpoints & Access Control**:
  - `POST /api/workers/{id}/badge`: Manager only; `Cache-Control: no-store`.
  - `DELETE /api/workers/{id}/badge`: Manager, or Foreman for their current tunnel (others 404).
  - `POST /api/checkins`: Scanners (`manager`, `safety_officer`, `engineer`, `foreman`; miners get 403).
  - `POST /api/checkins/manual`: Scanners; rate limited and audited.
  - `GET /api/checkins`: Scanners; query `tunnel_id` and `limit` (default 20, max 100); scoped.
  - `GET /api/headcount`: Scanners; query `mine_id`; foreman gets own tunnel only, superiors get all allowed tunnels.
  - `GET /api/check-flags`: Scanners; query `status=open|all`, `mine_id`, `tunnel_id`; marks `overdue` when > 8h.
  - `PATCH /api/check-flags/{id}`: Scanners; requires note (3-500 chars).
- **Frontend Dashboard Enhancements (`index.html`, `static/checkin.js`)**:
  - Worker detail pane: "Issue badge" shows QR code modal (render `badge_svg`, name, employee_no), Print button with print CSS, "This badge is shown only once" warning. Modal cleaned from DOM and never stored in `localStorage`. "Revoke badge" button with confirm dialog.
  - Gate scanner modal: IN/OUT direction toggle, tunnel selector (locked for foreman), USB scanner auto-focus/Enter key support, green success / red error banner, and last 20 scans table.
  - Live Headcount & Flags console: Real-time tunnel headcount (polled every 10s), open check-out flags with overdue highlight, and clear flag resolution modal.
- **Automated Verification**:
  - Added `tests/test_step6_badges_and_headcount.py` with 10 comprehensive tests covering badge lifecycle, SHA-256 storage, partial unique active index, cache headers, scan deduplication, state transition 409 conflicts, rate limiting (429), headcount math, flag background cycle past grace, manual clearance and headcount correction, dual-thread barrier race test, watchdog runner isolation, Alembic downgrade/upgrade, and the full Done-When journey.
  - Updated `POLICY_TABLE` in `tests/test_auth_and_roles.py`.
  - Full test suite: 92 passed in pytest (0 failures).

### 2026-11-22 - Step 5.5: Hardening
- **Scheduling RBAC Expansion (A1)**: Expanded write roles for `POST/PATCH/DELETE /api/assignments` to all `SUPERIOR_ROLES` (`manager`, `safety_officer`, `engineer`), matching the v2 design document matrix. Updated `POLICY_TABLE`, route dependencies, and matrix tests.
- **Worker Profile Integrity & Scoping (A2)**:
  - Enforced strict role validation in `PUT /api/workers/{id}/profile`: target user belonging to company but not having `foreman` or `miner` role returns HTTP 400 `{"detail": "Worker profile is only allowed for foreman or miner roles"}`; target in another company or missing returns HTTP 404.
  - Added duplicate `employee_no` pre-check query and caught PostgreSQL unique violation on `uq_worker_profiles_company_employee_no` to consistently return HTTP 409 `{"detail": "Employee number already in use"}`.
  - Cleaned up unused imports in `routers/workers.py` and `routers/assignments.py`.
- **N+1 Query Elimination & Default Assignment Window (A3)**:
  - Added default query window `now - 7d .. now + 30d` for `GET /api/assignments` when neither `from` nor `to` is supplied.
  - Added validated `limit` (default 500, max 1000) and `offset` (default 0) pagination parameters.
  - Eager-loaded `user`, `user.worker_profile`, `tunnel`, and `tunnel.mine` via `joinedload` across assignments queries and `services/worker_queries.py`.
  - Added unit test asserting SQL query count for 50 shifts executes in a constant $\le 4$ queries without N+1 growth.
- **Savepoint Isolation & Shift Clashing Redaction (A4)**:
  - Restructured `create_shift` in `services/shift_service.py` to wrap the insert in an atomic SAVEPOINT (`db.begin_nested()`) preventing session invalidation on constraint conflicts.
  - Isolated catch to PostgreSQL exclusion violation `23P01` (`ex_assignments_user_no_overlap`), re-raising any other `IntegrityError` as generic 400.
  - Redacted clashing shift metadata when the conflicting shift belongs to a mine outside the caller's authorized scope (returns `{"starts_at", "ends_at"}` without exposing internal `id`, `tunnel_id`, or `tunnel_name`).
  - Extracted rolling weekly schedule calculations to `services/weekly_hours.py`, maintaining `shift_service.py` under ~300 lines.
- **Test Hardening & Determinism (A5)**:
  - Eliminated all time-of-week dependencies by fixing Done-When test dates to a deterministic Monday-aligned calendar schedule.
  - Replaced single-session exclusion test with a true dual-thread concurrent race test synchronized via `threading.Barrier`, verifying exactly one thread succeeds and the other raises `ShiftOverlapError`.
  - Added comprehensive `tests/test_step5_policy.py` covering engineer scheduling, foreman/miner 403 blocks, Safety Officer cross-mine 404 isolation, duplicate employee numbers, bad phone validation, manager profile rejection, cross-company assignment mutation 404s, out-of-scope mine clash redaction, and `/api/me/profile` isolation.
  - Disabled background watchdog runner in test environment (`WATCHDOG_ENABLED=false` in `conftest.py`).
  - Full test suite: 82 passed in pytest.

### 2026-11-22 - Step 5: Workers, Assignments, and Shifts
- **Database Migration (`c1506e641433`)**:
  - Created `worker_profiles` table with `user_id` (PK, FK users CASCADE), `company_id` (FK companies CASCADE), `employee_no`, `phone`, `emergency_contact_name`, `emergency_contact_phone`, `joined_on`, and unique constraint `uq_worker_profiles_company_employee_no` on `(company_id, employee_no)`.
  - Created `assignments` table with `id` (bigserial PK), `user_id` (FK users CASCADE), `tunnel_id` (FK tunnels RESTRICT), `starts_at` (timestamptz), `ends_at` (timestamptz), `created_by` (FK users SET NULL), `created_at` (timestamptz).
  - Added range constraint `ck_assignments_range` (`ends_at > starts_at`).
  - Added PostgreSQL GIST exclusion constraint `ex_assignments_user_no_overlap` on `(user_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)`.
  - Added indexes `ix_assignments_user_id_starts_at` on `(user_id, starts_at DESC)` and `ix_assignments_tunnel_id_starts_at` on `(tunnel_id, starts_at)`.
  - Verified bidirectional migration integrity with `alembic downgrade -1` and `alembic upgrade head`, and confirmed zero schema drift with `alembic check`.
- **Shift Domain Service (`services/shift_service.py`)**:
  - Built `create_shift` with validations: worker existence, company match, active foreman/miner role, worker assigned to tunnel's mine via `user_mines`, tunnel permitted to actor, non-closed tunnel, `ends_at > starts_at`, and duration <= `MAX_SHIFT_HOURS`.
  - Pre-check query + PostgreSQL GIST exclusion constraint catch raising `ShiftOverlapError` carrying clashing shift details (`id`, `tunnel_name`, `starts_at`, `ends_at`).
  - Built `update_end` allowing shift shortening only (`starts_at < new_end <= current_end`).
  - Built `delete_shift` allowing deletion of unstarted future shifts only (`starts_at > now`), rejecting started shifts with HTTP 409 `{"code": "shift_started"}`.
  - Built `weekly_hours_warnings`: splits shifts across Monday 00:00 boundary in `HOURS_TIMEZONE`, sums total weekly hours, and emits non-blocking warning when exceeding `WEEKLY_HOURS_LIMIT`.
- **Worker Scoping & Profile Services**:
  - Implemented `services/tunnel_scope.py` single source of truth for worker "current tunnel" (active assignment now -> next upcoming -> most recent past -> None).
  - Miner sees only nodes, placements, and tunnels of their current tunnel; unassigned miner receives empty lists; foremen and superiors retain permitted mine visibility.
  - Implemented `services/worker_queries.py`: role-scoped listing of workers with search filter `q` (name, username, employee_no) and mine/tunnel filters.
- **REST Endpoints & Audit Logging**:
  - Added superior routes: `GET /api/workers`, `GET /api/workers/{id}`, `PUT /api/workers/{id}/profile` (Manager only; audited with `changed_fields` only, never phone numbers or contact names).
  - Added scheduler routes: `POST /api/assignments`, `PATCH /api/assignments/{id}`, `DELETE /api/assignments/{id}`.
  - Added range query route: `GET /api/assignments`.
  - Added worker self-service routes: `GET /api/me/shifts` (worker-safe view omitting `created_by`), `GET /api/me/profile`.
- **Frontend Dashboard Updates (`index.html`, `app.js`)**:
  - Superior console: Added Workers panel modal with worker search, mine/tunnel filters, worker profile inspection, manager profile editing, shift assignment form with clash warning and yellow weekly hours warning.
  - Worker console: Added "My hours" panel modal displaying worker profile, weekly hours monitor with progress bar vs limit, active shift indicator, upcoming shifts, and past shifts.
  - Enforced strict DOM escaping on all user-controlled text via `escapeHtml`.
- **Automated Verification**:
  - Added `tests/test_step5_shifts.py` with 7 comprehensive tests covering overlap rejection, concurrent GIST constraint violations, validation matrix, night shift boundary splitting, miner vs foreman vs superior scoping, privacy/security leakage prevention, and the complete Done-When end-to-end journey.
  - Updated `POLICY_TABLE` and data-driven matrix tests in `tests/test_auth_and_roles.py`.
  - Full test suite: 73 passed in pytest.

### 2026-11-22 - Step 4.5: Hardening
- **Stale Alert Resolution (A1)**: Watchdog cycle automatically resolves open alerts whose subject is no longer eligible: inactive gateways (`active=False`) and unplaced nodes (`is_placed=False`) in a single pass per cycle.
- **Unseen Placed Node Protection (A2)**: Placed nodes with `last_seen_at = NULL` are skipped by the watchdog and never raise alerts.
- **Alert Scoping & Role Hardening (A3)**: Removed non-existent `admin` role branches in `GET /api/alerts` and `POST /api/alerts/{id}/ack`. Out-of-scope `mine_id` query returns HTTP 404 `{"detail": "Mine not found"}`.
- **Gateway & Mine Route Scoping (A4)**: `PATCH /api/gateways/{id}`, `POST /api/gateways/{id}/rotate-key`, `DELETE /api/gateways/{id}`, `GET/POST /api/mines/{id}/gateways`, and `POST /api/mines/{id}/tunnels` enforce that the gateway's or mine's ID is within `get_allowed_mine_ids(db, user)`, returning HTTP 404 otherwise.
- **Single Source of Truth for Online Status (A5)**: Removed `offline_after_s` parameter from `serialize_node` and `serialize_worker_node`; removed `OFFLINE_AFTER_S` import from routers; verified with unit tests that `/api/nodes` online calculation agrees with the watchdog at the exact threshold boundary.
- **Watchdog Query Optimization and Title Formatting (A6)**: Replaced per-node alert queries with a single bulk query per cycle (`open_alerts` dictionary lookup); formatted alert titles using node number and gateway name (`Node {node.node_no} offline ({gw.name} / {mine.name} / {tunnel.name})`); added non-sensitive INFO logging for alert open and resolution events.
- **Login Timing Hardening & Session Version Cleanup (A7)**: Always execute PBKDF2 `verify_password` against a constant dummy hash for unknown OR inactive users, mitigating timing attacks on deactivated accounts. Removed `getattr`/`hasattr` fallbacks for `token_version` column. Added comprehensive tests for session invalidation on password change, administrative reset, and user deactivation.
- **Documentation & Test Verification (A8)**: Corrected Section 6 table count to 10 tables, updated directory map from real filesystem tree, corrected `placements.py` endpoint documentation, and verified test suite execution. All 66 tests passing.

### 2026-11-22 - Step 4: Offline Detection, Watchdog Runner, Alerts, and Token Invalidation
- **Database Migration (`e252124b61df`)**:
  - Added `users.token_version` (`Integer`, nullable=False, server_default=`0`) for instantaneous cryptographic session invalidation.
  - Created `alerts` table with `id`, `company_id`, `mine_id`, `gateway_id`, `node_id`, `kind`, `title`, `opened_at`, `resolved_at`, `acknowledged_by`, `acknowledged_at`.
  - Added CHECK constraints: `ck_alerts_kind` (`node_offline`, `gateway_offline`) and `ck_alerts_kind_node`.
  - Added PostgreSQL partial unique indexes: `uq_alerts_open_node` on `(node_id) WHERE kind = 'node_offline' AND resolved_at IS NULL` and `uq_alerts_open_gateway` on `(gateway_id) WHERE kind = 'gateway_offline' AND resolved_at IS NULL`.
  - Added compound index `ix_alerts_mine_id_opened_at` on `(mine_id, opened_at DESC)`.
  - Verified `alembic downgrade -1` and `alembic upgrade head` succeed with zero drift on `alembic check`.
- **Session Invalidation (Phase A Fix)**:
  - Added `tv` claim to JWT session tokens matching `users.token_version`.
  - `get_current_user` rejects (`401 Unauthorized`) any token whose `tv` does not match the user's current database `token_version`.
  - Increments `token_version` on password changes (`POST /api/auth/change-password`), administrative password resets (`PATCH /api/users/{id}`), and user deactivations (`DELETE /api/users/{id}`).
  - Password change reissues a fresh cookie with the incremented `tv` so caller remains smoothly authenticated.
- **Connectivity Single Source of Truth**:
  - Created `services/connectivity.py` with `node_is_online` and `gateway_is_online`.
  - Unified all online/offline state checks across serializers and routers (`node_serializer.py`, `routers/gateways.py`, `routers/mines.py`).
  - Added validated configuration settings in `app/config.py`: `NODE_OFFLINE_AFTER_S` (default 35s), `GATEWAY_OFFLINE_AFTER_S` (default 60s), `WATCHDOG_INTERVAL_S` (default 30s), `WATCHDOG_ENABLED` (default true).
- **Watchdog Engine & Advisory Lock Runner**:
  - Built pure cycle function `run_watchdog_cycle(db, now)` in `services/watchdog_service.py` taking a database session and timestamp.
  - Evaluates active, seen gateways only; dead gateway suppresses node checks and auto-resolves open child node alerts; online gateway resolves gateway alert and evaluates only placed nodes (`is_placed(node)`).
  - Handles concurrent alert insertion races gracefully by catching `IntegrityError` on savepoint flushes.
  - Built `WatchdogRunner` in `app/watchdog_runner.py` with daemon thread, PostgreSQL transaction advisory lock `pg_try_advisory_xact_lock(8392104)`, and startup grace period delay (`NODE_OFFLINE_AFTER_S`). Wired into FastAPI lifespan hooks in `app/main.py`.
- **Alert Endpoints & Superior Console**:
  - Added `GET /api/alerts?status=open|all&mine_id=` scoped to user's permitted mines (superiors only, workers get 403).
  - Added `POST /api/alerts/{id}/ack` for idempotent acknowledgment recording audit action `"alert.acknowledge"`.
  - Frontend (`app.js`): Superiors poll `/api/alerts` every 10s. Displays top banner `⚠️ X active alerts [View]` when unacknowledged alerts exist. Offline nodes greyed out on map and inventory chips. Workers see no alert UI.
- **Automated Verification**:
  - Added `tests/test_step4_watchdog.py` with 14 comprehensive test cases covering connectivity boundary conditions, deduplication, auto-resolution, unplaced node immunity, gateway offline suppression, partial unique index concurrency, startup grace period, scoping, ack audit logs, session revocation, and the full end-to-end Done-When journey. All 59 tests passing.

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
  - Built `services/audit.py` recording audit rows atomically for all state mutations (`mine.create`, `tunnel.create`, `gateway.register`, `gateway.update`, `gateway.rotate_key`, `gateway.disable`, `node.place`, `node.unplace`, `user.create`, `user.update`, `user.deactivate`, `auth.password_change`).
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
