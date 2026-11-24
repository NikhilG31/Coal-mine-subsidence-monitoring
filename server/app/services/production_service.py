"""Coal mine production tracking, daily upsert, and monthly aggregation service.

Owns daily tonnage upsert, validation, calendar month summation, and achievement percentage math.
Must NOT handle HTTP requests, import FastAPI components, or bypass mine scoping.
"""
import datetime
import re
from typing import Any, Dict, List, Optional
import zoneinfo
from decimal import Decimal
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import HOURS_TIMEZONE
from app.models.mine import Mine
from app.models.mine_production import MineProduction
from app.services.audit import record_audit
from app.services import clock

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validate_month_str(month: str) -> None:
    """Validates that month is a valid YYYY-MM string."""
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError(f"Invalid month format '{month}'. Expected YYYY-MM.")


def _get_month_bounds(month: str) -> tuple[datetime.date, datetime.date]:
    """Returns (start_date, next_month_start_date) for a given YYYY-MM string."""
    validate_month_str(month)
    year = int(month[:4])
    m_num = int(month[5:7])
    start_date = datetime.date(year, m_num, 1)
    if m_num == 12:
        end_date = datetime.date(year + 1, 1, 1)
    else:
        end_date = datetime.date(year, m_num + 1, 1)
    return start_date, end_date


def month_summary(
    db: Session,
    mine_id: int,
    month: str,
) -> Dict[str, Any]:
    """Calculates monthly production summary per B0.

    Monthly figures are SUMS over the days of that month:
    - planned_total_t: sum of planned values (0.0 if empty)
    - actual_total_t: sum of actual values (0.0 if empty)
    - variance_t = actual_total_t - planned_total_t
    - achievement_pct = actual_total / planned_total * 100 rounded to 1 decimal,
      NULL when planned_total is 0
    - reported_days: days with an actual value
    """
    start_date, end_date = _get_month_bounds(month)

    rows = (
        db.query(MineProduction)
        .filter(
            MineProduction.mine_id == mine_id,
            MineProduction.day >= start_date,
            MineProduction.day < end_date,
        )
        .all()
    )

    planned_sum = Decimal("0.00")
    actual_sum = Decimal("0.00")
    reported_days = 0

    for r in rows:
        if r.planned_t is not None:
            planned_sum += Decimal(str(r.planned_t))
        if r.actual_t is not None:
            actual_sum += Decimal(str(r.actual_t))
            reported_days += 1

    planned_total_t = float(round(planned_sum, 2))
    actual_total_t = float(round(actual_sum, 2))
    variance_t = float(round(actual_total_t - planned_total_t, 2))

    if planned_total_t == 0.0:
        achievement_pct = None
    else:
        achievement_pct = round((actual_total_t / planned_total_t) * 100.0, 1)

    return {
        "month": month,
        "planned_total_t": planned_total_t,
        "actual_total_t": actual_total_t,
        "variance_t": variance_t,
        "achievement_pct": achievement_pct,
        "reported_days": reported_days,
    }


def list_days(
    db: Session,
    mine_id: int,
    month: str,
) -> List[Dict[str, Any]]:
    """Returns list of daily production records for a mine in a given month."""
    start_date, end_date = _get_month_bounds(month)

    rows = (
        db.query(MineProduction)
        .filter(
            MineProduction.mine_id == mine_id,
            MineProduction.day >= start_date,
            MineProduction.day < end_date,
        )
        .order_by(MineProduction.day.asc())
        .all()
    )

    return [
        {
            "day": r.day.isoformat(),
            "planned_t": float(r.planned_t) if r.planned_t is not None else None,
            "actual_t": float(r.actual_t) if r.actual_t is not None else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]


def upsert_production(
    db: Session,
    actor: Any,
    mine: Mine,
    entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Upserts daily production entries (1-62) with field-level ON CONFLICT DO UPDATE.

    Enforces:
    - 1-62 entries per call
    - No duplicate days in one call
    - Day not earlier than 5 years ago
    - actual_t not allowed for days after TODAY in HOURS_TIMEZONE (planned_t may be future)
    - planned_t and actual_t >= 0
    - Both planned_t and actual_t null is rejected
    - Audit mine.production_update with {days: n, months: [YYYY-MM...]}
    """
    if not entries or len(entries) < 1 or len(entries) > 62:
        raise ValueError("Number of production entries must be between 1 and 62")

    tz = zoneinfo.ZoneInfo(HOURS_TIMEZONE)
    now_utc = clock.utcnow()
    now_tz = now_utc.astimezone(tz)
    today_tz = now_tz.date()

    try:
        five_years_ago = today_tz.replace(year=today_tz.year - 5)
    except ValueError:
        five_years_ago = today_tz.replace(year=today_tz.year - 5, day=28)

    days_seen = set()
    validated_entries = []

    for item in entries:
        day_raw = item.get("day")
        if isinstance(day_raw, str):
            try:
                day = datetime.date.fromisoformat(day_raw)
            except ValueError:
                raise ValueError(f"Invalid date format for day '{day_raw}'. Expected YYYY-MM-DD.")
        elif isinstance(day_raw, datetime.date):
            day = day_raw
        else:
            raise ValueError("Entry 'day' is required and must be YYYY-MM-DD.")

        if day in days_seen:
            raise ValueError(f"Duplicate day '{day.isoformat()}' in entries payload.")
        days_seen.add(day)

        if day < five_years_ago:
            raise ValueError(f"Day {day.isoformat()} cannot be earlier than 5 years ago.")

        planned_t = item.get("planned_t")
        actual_t = item.get("actual_t")

        if planned_t is None and actual_t is None:
            raise ValueError(f"Entry for {day.isoformat()} must provide at least one of planned_t or actual_t.")

        if planned_t is not None:
            try:
                planned_val = float(planned_t)
            except (ValueError, TypeError):
                raise ValueError(f"planned_t must be numeric for day {day.isoformat()}.")
            if planned_val < 0:
                raise ValueError(f"planned_t must be non-negative for day {day.isoformat()}.")
        else:
            planned_val = None

        if actual_t is not None:
            try:
                actual_val = float(actual_t)
            except (ValueError, TypeError):
                raise ValueError(f"actual_t must be numeric for day {day.isoformat()}.")
            if actual_val < 0:
                raise ValueError(f"actual_t must be non-negative for day {day.isoformat()}.")
            if day > today_tz:
                raise ValueError(f"actual_t is not allowed for future day {day.isoformat()} (today in {HOURS_TIMEZONE} is {today_tz.isoformat()}).")
        else:
            actual_val = None

        validated_entries.append({
            "day": day,
            "planned_t": planned_val,
            "actual_t": actual_val,
        })

    # Execute atomic upserts
    affected_months = sorted(list({entry["day"].strftime("%Y-%m") for entry in validated_entries}))

    for entry in validated_entries:
        day = entry["day"]
        planned_val = entry["planned_t"]
        actual_val = entry["actual_t"]

        insert_values: Dict[str, Any] = {
            "mine_id": mine.id,
            "day": day,
            "updated_by": actor.id,
            "updated_at": now_utc,
        }
        update_values: Dict[str, Any] = {
            "updated_by": actor.id,
            "updated_at": now_utc,
        }

        if planned_val is not None:
            insert_values["planned_t"] = planned_val
            update_values["planned_t"] = planned_val

        if actual_val is not None:
            insert_values["actual_t"] = actual_val
            update_values["actual_t"] = actual_val

        stmt = (
            pg_insert(MineProduction)
            .values(**insert_values)
            .on_conflict_do_update(
                index_elements=["mine_id", "day"],
                set_=update_values,
            )
        )
        db.execute(stmt)

    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="mine.production_update",
        entity="mine",
        entity_id=mine.id,
        detail={"days": len(validated_entries), "months": affected_months},
        company_id=mine.company_id,
    )
    db.commit()

    summaries = [month_summary(db, mine.id, m) for m in affected_months]

    return {
        "updated": len(validated_entries),
        "summary": summaries[0] if len(summaries) == 1 else summaries,
        "summaries": summaries,
    }
