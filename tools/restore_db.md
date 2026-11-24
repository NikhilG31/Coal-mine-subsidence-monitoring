# PostgreSQL Database Restore Procedure

Owns step-by-step instructions for restoring a PostgreSQL database dump created by `tools/backup_db.sh`.
Must NOT contain hardcoded secrets, cleartext passwords, or unverified restore shortcuts.

---

## 1. Prerequisites

- PostgreSQL client binaries (`pg_restore`, `dropdb`, `createdb`, `psql`) installed and available in `PATH`.
- PostgreSQL superuser or database owner access.
- Valid custom-format (`-Fc`) database dump file (e.g., `backups/coalmine_20261124_120000.dump`).
- Python virtual environment with project dependencies installed.

---

## 2. Restore Steps

### Option A: Clean Restore into an Existing Database

Use `pg_restore` with `--clean` and `--if-exists` to drop and recreate database objects in place:

```bash
# Set connection parameters (avoid cleartext passwords in command history)
export PGHOST="localhost"
export PGPORT="5432"
export PGUSER="postgres"
export PGDATABASE="mine_subsidence"

# Run restore from custom-format dump
pg_restore \
  --host="${PGHOST}" \
  --port="${PGPORT}" \
  --username="${PGUSER}" \
  --dbname="${PGDATABASE}" \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --verbose \
  backups/coalmine_YYYYmmdd_HHMMSS.dump
```

*(Note: Initial errors regarding dropped objects that do not yet exist are normal when using `--if-exists`).*

---

### Option B: Fresh Database Recreation (Recommended for Clean State)

To guarantee zero residual state, drop and recreate the target database before restoring:

```bash
# 1. Terminate active backend connections to the target database
psql -h localhost -U postgres -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'mine_subsidence' AND pid <> pg_backend_pid();"

# 2. Drop and recreate database
dropdb -h localhost -U postgres --if-exists mine_subsidence
createdb -h localhost -U postgres mine_subsidence

# 3. Restore all objects and table data
pg_restore \
  -h localhost \
  -U postgres \
  -d mine_subsidence \
  --no-owner \
  --no-privileges \
  --verbose \
  backups/coalmine_YYYYmmdd_HHMMSS.dump
```

---

## 3. Post-Restore Schema and Migration Verification

After restoring the dump, immediately verify that the database schema exactly matches the application models and Alembic revision head:

```bash
# Navigate to repository root or server directory
cd /path/to/repository

# Ensure virtualenv is active and PYTHONPATH includes server/
export PYTHONPATH="server"

# Run Alembic check against restored database
python -m alembic -c server/alembic.ini check
```

Expected output:
```text
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
No new upgrade operations detected.
```

---

## 4. Sanity Verification Queries

Verify that the core tables and initial administrative records are populated:

```bash
# Check company and user accounts
psql -h localhost -U postgres -d mine_subsidence -c \
  "SELECT id, name, created_at FROM companies;"

psql -h localhost -U postgres -d mine_subsidence -c \
  "SELECT id, username, role, active FROM users;"

# Check mine sites and registered sensor nodes
psql -h localhost -U postgres -d mine_subsidence -c \
  "SELECT id, name, status, method FROM mines;"

psql -h localhost -U postgres -d mine_subsidence -c \
  "SELECT count(*) AS total_readings FROM readings;"
```

---

## 5. Troubleshooting

| Error | Root Cause | Resolution |
|---|---|---|
| `database "mine_subsidence" is being accessed by other users` | Open connections or server daemon running | Run `pg_terminate_backend` query from Step Option B and stop running Uvicorn instances. |
| `role "postgres" does not exist` | Different PostgreSQL admin user on host | Pass `-U <admin_user>` or use connection URI format. |
| `Alembic check reports drift after restore` | Dump was taken from an older migration revision | Run `alembic upgrade head` to apply any missing revisions. |
