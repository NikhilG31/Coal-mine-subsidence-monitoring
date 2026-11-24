#!/usr/bin/env bash
# Coal Mine Subsidence Monitoring - Database Backup Utility
# Owns automated PostgreSQL backup creation via pg_dump and rotation retention.
# Must NOT log credentials/passwords, perform unauthenticated commands, or modify application state.

set -euo pipefail

# Configuration defaults
BACKUP_DIR="${BACKUP_DIR:-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

# Resolve repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Load DATABASE_URL from .env if not explicitly set
if [ -z "${DATABASE_URL:-}" ]; then
    if [ -f "${REPO_ROOT}/.env" ]; then
        # Extract DATABASE_URL without leaking passwords in output
        DATABASE_URL="$(grep -v '^#' "${REPO_ROOT}/.env" | grep -E '^DATABASE_URL=' | head -n 1 | cut -d '=' -f 2- | tr -d '"' | tr -d "'")"
    fi
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL is not set and could not be loaded from .env" >&2
    exit 1
fi

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
BACKUP_FILE="${BACKUP_DIR}/coalmine_${TIMESTAMP}.dump"

# Strip SQLAlchemy driver prefix if present (e.g., postgresql+psycopg:// -> postgresql://)
CLEAN_DB_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql:\/\/}"

# Mask password for safe logging
SAFE_URL="$(echo "${CLEAN_DB_URL}" | sed -E 's/:([^@/]+)@/:***@/')"
echo "Starting PostgreSQL backup to: ${BACKUP_FILE} (source: ${SAFE_URL})"

# Execute pg_dump using custom compressed format (-Fc)
# pg_dump accepts connection URI directly.
pg_dump -Fc --dbname="${CLEAN_DB_URL}" --file="${BACKUP_FILE}"

echo "Backup created successfully: ${BACKUP_FILE}"

# Retention management: keep newest BACKUP_KEEP files and remove older ones
BACKUP_COUNT=$(find "${BACKUP_DIR}" -maxdepth 1 -name "coalmine_*.dump" -type f | wc -l)
if [ "${BACKUP_COUNT}" -gt "${BACKUP_KEEP}" ]; then
    REMOVE_COUNT=$((BACKUP_COUNT - BACKUP_KEEP))
    echo "Pruning ${REMOVE_COUNT} older backup(s) to retain newest ${BACKUP_KEEP}..."
    find "${BACKUP_DIR}" -maxdepth 1 -name "coalmine_*.dump" -type f | sort | head -n "${REMOVE_COUNT}" | while read -r old_dump; do
        rm -f "${old_dump}"
        echo "Removed stale backup: ${old_dump}"
    done
fi

echo "Backup and rotation completed successfully."
