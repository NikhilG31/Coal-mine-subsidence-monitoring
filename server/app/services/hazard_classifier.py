"""Subsidence hazard classification engine.

Owns DGMS-aligned threshold evaluations for tilt and vibration telemetry.
Must NOT load external ML models, access the database, or import FastAPI.
"""
from typing import Any, Dict, List, Optional, Tuple

# Hazard status definitions
LEVEL_NORMAL = "normal"
LEVEL_WATCH = "watch"
LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"

LEVEL_INT_TO_STR: Dict[int, str] = {
    0: LEVEL_NORMAL,
    1: LEVEL_WATCH,
    2: LEVEL_WARNING,
    3: LEVEL_CRITICAL,
}
LEVEL_STR_TO_INT: Dict[str, int] = {v: k for k, v in LEVEL_INT_TO_STR.items()}

LEVEL_COLORS: Dict[str, str] = {
    LEVEL_NORMAL: "#1f5a2e",   # Green
    LEVEL_WATCH: "#c9a227",    # Yellow / Gold
    LEVEL_WARNING: "#e0651e",  # Orange / Saffron
    LEVEL_CRITICAL: "#8b1a1a", # Red / Blood
}

# Thresholds per specification:
# Tilt: 0.5 (watch), 1.0 (warning), 2.0 (critical) degrees
# Vibration RMS: 0.05 (watch), 0.15 (warning), 0.30 (critical) g
TILT_THRESHOLDS: List[Tuple[float, int]] = [(2.0, 3), (1.0, 2), (0.5, 1)]
VIB_THRESHOLDS: List[Tuple[float, int]] = [(0.30, 3), (0.15, 2), (0.05, 1)]


def classify_hazard(tilt_x: float, tilt_y: float, vib_rms: float) -> int:
    """Pure rule-based hazard classifier returning severity level 0..3.

    Rules:
    - tilt = max(|tilt_x|, |tilt_y|). (No resultant)
    - Per metric, level = highest threshold value is >= (inclusive at all thresholds).
    - level = max(tilt_level, vib_level).
    - SW160 shock flag does NOT change level.
    - No hysteresis.
    """
    tilt_val = max(abs(float(tilt_x)), abs(float(tilt_y)))
    vib_val = float(vib_rms)

    tilt_level = 0
    for thresh, lvl in TILT_THRESHOLDS:
        if tilt_val >= thresh:
            tilt_level = lvl
            break

    vib_level = 0
    for thresh, lvl in VIB_THRESHOLDS:
        if vib_val >= thresh:
            vib_level = lvl
            break

    return max(tilt_level, vib_level)


def get_hazard_details(
    tilt_x: float,
    tilt_y: float,
    vib_rms: float,
    shock: bool = False,
    batt_v: Optional[float] = None,
    batt_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Computes full diagnostic details (status, score, reasons, color) from the single classifier."""
    tx = float(tilt_x)
    ty = float(tilt_y)
    tilt_val = max(abs(tx), abs(ty))
    vm = float(vib_rms)

    level = classify_hazard(tx, ty, vm)
    status_str = LEVEL_INT_TO_STR[level]

    # Normalized risk score 0.0 to 1.0
    tilt_norm = min(1.0, tilt_val / 2.5)
    vib_norm = min(1.0, vm / 0.4)
    score = round(max(tilt_norm, vib_norm), 3)

    reasons: List[str] = []
    # Tilt diagnostics
    if tilt_val >= 2.0:
        reasons.append(f"Critical tilt change: {tilt_val:.2f}° (>= 2.0°)")
    elif tilt_val >= 1.0:
        reasons.append(f"Warning tilt change: {tilt_val:.2f}° (1.0° - 2.0°)")
    elif tilt_val >= 0.5:
        reasons.append(f"Watch tilt change: {tilt_val:.2f}° (0.5° - 1.0°)")

    # Vibration diagnostics
    if vm >= 0.30:
        reasons.append(f"Critical vibration RMS: {vm:.3f}g (>= 0.30g)")
    elif vm >= 0.15:
        reasons.append(f"Warning vibration RMS: {vm:.3f}g (0.15g - 0.30g)")
    elif vm >= 0.05:
        reasons.append(f"Watch vibration RMS: {vm:.3f}g (0.05g - 0.15g)")

    if shock:
        reasons.append("SW160 shock sensor triggered")

    if not reasons:
        reasons.append("All subsidence metrics within baseline thresholds")

    # Battery diagnostics
    bp = batt_pct if batt_pct is not None else 100.0
    bv = batt_v if batt_v is not None else 4.0
    low_battery = bp <= 20.0 or (bv > 0 and bv <= 3.4)
    if low_battery:
        reasons.append(f"Low battery warning: {bp:.0f}% ({bv:.2f}V)")

    return {
        "level": level,
        "status": status_str,
        "score": score,
        "reasons": reasons,
        "color": LEVEL_COLORS.get(status_str, LEVEL_COLORS[LEVEL_NORMAL]),
        "low_battery": low_battery,
        "effective_tilt": round(tilt_val, 3),
    }
