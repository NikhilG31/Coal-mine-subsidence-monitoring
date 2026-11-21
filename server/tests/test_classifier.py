"""Unit tests for subsidence hazard classification logic.

Owns validating pure threshold evaluation and DGMS compliance rules.
Must NOT require database access or issue network requests.
"""
import pytest
from app.services.hazard_classifier import (
    classify_hazard,
    get_hazard_details,
)


@pytest.mark.parametrize(
    "tilt_x,tilt_y,vib_rms,expected_level",
    [
        (0.4, 0.4, 0.01, 0),
        (0.5, 0.0, 0.0, 1),
        (0.7, 0.7, 0.01, 1),
        (1.0, 0.0, 0.0, 2),
        (1.5, 1.5, 0.01, 2),
        (2.0, 0.0, 0.0, 3),
        (-2.5, 0.0, 0.0, 3),
        (0.0, 0.0, 0.049, 0),
        (0.0, 0.0, 0.05, 1),
        (0.0, 0.0, 0.15, 2),
        (0.0, 0.0, 0.30, 3),
        (0.6, 0.0, 0.20, 2),
    ],
)
def test_hazard_classifier_spec_matrix(tilt_x, tilt_y, vib_rms, expected_level):
    """Verifies all exact specification threshold matrix cases."""
    level = classify_hazard(tilt_x, tilt_y, vib_rms)
    assert level == expected_level, (
        f"classify_hazard({tilt_x}, {tilt_y}, {vib_rms}) returned {level}, expected {expected_level}"
    )


def test_shock_flag_does_not_change_level():
    """Verifies that SW160 shock flag is recorded but does NOT elevate danger level."""
    # (0.1, 0.1, 0.01 with shock=True) -> level 0
    level = classify_hazard(0.1, 0.1, 0.01)
    assert level == 0

    details = get_hazard_details(0.1, 0.1, 0.01, shock=True)
    assert details["level"] == 0
    assert details["status"] == "normal"
    assert any("shock" in r.lower() for r in details["reasons"])
