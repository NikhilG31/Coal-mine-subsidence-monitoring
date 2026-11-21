"""Decision layer: Rule-based early warning system & Mock ML interface.

Classification Rules:
Level       Tilt change        Vibration RMS      Colour
Normal      < 0.5°             < 0.05 g           Green   (#1f5a2e / #2d7a45)
Watch       0.5° - 1.0°        0.05 - 0.15 g      Yellow  (#c9a227)
Warning     1.0° - 2.0°        0.15 - 0.30 g      Orange  (#e0651e)
Critical    > 2.0°             > 0.30 g           Red     (#8b1a1a)

Hardware baseline note:
TiltX and TiltY represent deviation (pitch and roll) relative to the startup baseline
set during field calibration. The resultant tilt change is max(|tiltX|, |tiltY|)
or resultant sqrt(tiltX^2 + tiltY^2).

If a trained scikit-learn model file (model.joblib) exists, it can be loaded
seamlessly; otherwise, this rule-based fake ML engine operates reliably.
"""
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

MODEL_PATH = Path(os.getenv("MODEL_PATH", Path(__file__).parent / "model.joblib"))

# Danger level definitions
LEVEL_NORMAL = "normal"
LEVEL_WATCH = "watch"
LEVEL_WARNING = "warning"
LEVEL_CRITICAL = "critical"

LEVEL_RANKS = {
    LEVEL_NORMAL: 0,
    LEVEL_WATCH: 1,
    LEVEL_WARNING: 2,
    LEVEL_CRITICAL: 3,
}

LEVEL_COLORS = {
    LEVEL_NORMAL: "#1f5a2e",   # Green (var(--green))
    LEVEL_WATCH: "#c9a227",    # Yellow / Gold (var(--gold))
    LEVEL_WARNING: "#e0651e",  # Orange / Saffron (var(--saffron))
    LEVEL_CRITICAL: "#8b1a1a", # Red / Blood (var(--blood))
}

# Rule thresholds
TILT_WATCH = 0.5    # deg
TILT_WARN = 1.0     # deg
TILT_CRIT = 2.0     # deg

VIB_WATCH = 0.05    # g
VIB_WARN = 0.15     # g
VIB_CRIT = 0.30     # g

BATT_LOW_PCT = 20.0 # % (Low battery warning flag)
BATT_CRIT_PCT = 10.0 # % (Critical battery)


class Analyzer:
    """Predicts danger levels for coal mine subsidence nodes.
    
    Acts as the Mock ML / Rule-based classifier required by the system,
    with pluggable hooks for trained ML models.
    """

    def __init__(self):
        self.model = None
        if MODEL_PATH.exists():
            try:
                import joblib
                self.model = joblib.load(MODEL_PATH)
            except Exception as e:
                print(f"[analyzer] Warning: Failed to load {MODEL_PATH}: {e}")

    @property
    def source(self) -> str:
        return "trained_ml" if self.model is not None else "rule_based_ml"

    def _classify_tilt(self, tilt_deg: float) -> Tuple[str, str]:
        """Classifies tilt magnitude."""
        if tilt_deg > TILT_CRIT:
            return LEVEL_CRITICAL, f"Critical tilt change: {tilt_deg:.2f}° (> 2.0°)"
        elif tilt_deg >= TILT_WARN:
            return LEVEL_WARNING, f"Warning tilt change: {tilt_deg:.2f}° (1.0° - 2.0°)"
        elif tilt_deg >= TILT_WATCH:
            return LEVEL_WATCH, f"Watch tilt change: {tilt_deg:.2f}° (0.5° - 1.0°)"
        return LEVEL_NORMAL, f"Normal tilt: {tilt_deg:.2f}° (< 0.5°)"

    def _classify_vibration(self, vib_rms_g: float, vib_detected: bool) -> Tuple[str, str]:
        """Classifies vibration RMS and SW160 digital shock sensor."""
        if vib_rms_g > VIB_CRIT:
            return LEVEL_CRITICAL, f"Critical vibration RMS: {vib_rms_g:.3f}g (> 0.30g)"
        elif vib_rms_g >= VIB_WARN:
            return LEVEL_WARNING, f"Warning vibration RMS: {vib_rms_g:.3f}g (0.15g - 0.30g)"
        elif vib_rms_g >= VIB_WATCH:
            return LEVEL_WATCH, f"Watch vibration RMS: {vib_rms_g:.3f}g (0.05g - 0.15g)"
        
        # If SW160 digital vibration triggered even if RMS was briefly lower
        if vib_detected:
            return LEVEL_WATCH, f"Digital shock detected by SW160 (vib RMS: {vib_rms_g:.3f}g)"
            
        return LEVEL_NORMAL, f"Normal vibration: {vib_rms_g:.3f}g (< 0.05g)"

    def _rules(self, r: Dict[str, Any]) -> Tuple[str, float, List[str]]:
        """Evaluates sensor readings against coal mine subsidence rules."""
        tx = float(r.get("tilt_x", 0.0))
        ty = float(r.get("tilt_y", 0.0))
        # Resultant tilt angle deviation from calibrated zero point
        tilt_magnitude = math.sqrt(tx * tx + ty * ty)
        # Also check maximum directional component
        max_component = max(abs(tx), abs(ty))
        effective_tilt = max(tilt_magnitude, max_component)

        vib_rms = float(r.get("vibration_magnitude", 0.0))
        vib_detected = bool(r.get("vibration_detected", False))

        tilt_level, tilt_reason = self._classify_tilt(effective_tilt)
        vib_level, vib_reason = self._classify_vibration(vib_rms, vib_detected)

        # The overall danger level is the maximum of tilt and vibration danger
        tilt_rank = LEVEL_RANKS[tilt_level]
        vib_rank = LEVEL_RANKS[vib_level]

        overall_rank = max(tilt_rank, vib_rank)
        for name, rank in LEVEL_RANKS.items():
            if rank == overall_rank:
                status = name
                break
        else:
            status = LEVEL_NORMAL

        reasons = []
        if overall_rank > 0:
            if tilt_rank == overall_rank:
                reasons.append(tilt_reason)
            if vib_rank == overall_rank:
                reasons.append(vib_reason)
        else:
            reasons.append("All subsidence metrics within baseline thresholds")

        # Normalized risk score 0.0 to 1.0
        tilt_norm = min(1.0, effective_tilt / 2.5)
        vib_norm = min(1.0, vib_rms / 0.4)
        score = max(tilt_norm, vib_norm)
        if vib_detected and score < 0.3:
            score = 0.35

        return status, score, reasons

    def analyze(self, r: Dict[str, Any]) -> Dict[str, Any]:
        """Analyzes a reading packet and produces status, score, reasons, and battery alert."""
        reasons = []
        
        # Check if external trained model is loaded
        if self.model is not None:
            try:
                import numpy as np
                feats = [
                    float(r.get("tilt_x", 0.0)),
                    float(r.get("tilt_y", 0.0)),
                    float(r.get("vibration_magnitude", 0.0)),
                    float(r.get("vibration_detected", False)),
                    float(r.get("battery_voltage", 0.0)),
                    float(r.get("battery_percentage", 0.0))
                ]
                proba = self.model.predict_proba(np.array([feats], dtype=float))[0]
                classes = list(self.model.classes_)
                p = {c: proba[i] for i, c in enumerate(classes)}
                pred_class = max(p, key=p.get)
                
                # Map integer classes if needed
                class_mapping = {0: LEVEL_NORMAL, 1: LEVEL_WATCH, 2: LEVEL_WARNING, 3: LEVEL_CRITICAL}
                status = class_mapping.get(pred_class, str(pred_class).lower())
                score = float(1.0 - p.get(0, 0.0))
                reasons.append(f"ML Model Prediction: {status.upper()} (confidence: {p[pred_class]:.0%})")
            except Exception as e:
                # Fallback to rules on error
                status, score, reasons = self._rules(r)
        else:
            status, score, reasons = self._rules(r)

        # Battery analysis (independent diagnostic flag)
        batt_pct = float(r.get("battery_percentage", 100.0))
        batt_v = float(r.get("battery_voltage", 4.2))
        low_battery = batt_pct <= BATT_LOW_PCT or (batt_v > 0 and batt_v <= 3.4)
        crit_battery = batt_pct <= BATT_CRIT_PCT or (batt_v > 0 and batt_v <= 3.2)

        if crit_battery:
            reasons.append(f"CRITICAL BATTERY: {batt_pct:.0f}% ({batt_v:.2f}V)")
        elif low_battery:
            reasons.append(f"Low battery warning: {batt_pct:.0f}% ({batt_v:.2f}V)")

        return {
            "status": status,
            "danger_level": status, # synonym for clarity
            "score": round(score, 3),
            "reasons": reasons,
            "color": LEVEL_COLORS.get(status, LEVEL_COLORS[LEVEL_NORMAL]),
            "low_battery": low_battery,
            "crit_battery": crit_battery,
            "source": self.source,
        }
