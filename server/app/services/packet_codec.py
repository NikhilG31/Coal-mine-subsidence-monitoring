"""Binary packet codec for ESP32 gateway telemetry.

Owns the 38-byte packed struct format definition, parsing, validation, and serialization.
Must NOT interact with database sessions or import FastAPI components.
"""
import math
import struct
from typing import Any, Dict, Optional, Tuple

# Wire format: <6sBIfffBffBBBBH (38 bytes, little-endian, no padding)
# gateway_mac (6s), node_id (B), counter (I), tiltX (f), tiltY (f),
# vibration_magnitude (f), vibration_detected (B), battery_voltage (f),
# battery_percentage (f), sos_active (B), danger_level (B), fast_mode (B),
# buzzer_on (B), last_cmd_seq (H)
PAYLOAD_FMT: str = "<6sBIfffBffBBBBH"
PAYLOAD_SIZE: int = struct.calcsize(PAYLOAD_FMT)
assert PAYLOAD_SIZE == 38, f"PAYLOAD_FMT must be 38 bytes, got {PAYLOAD_SIZE}"


def norm_mac(value: Optional[str]) -> str:
    """Formats a MAC string into canonical 'AA:BB:CC:DD:EE:FF' representation."""
    h = "".join(c for c in str(value or "").upper() if c in "0123456789ABCDEF")
    if len(h) != 12:
        return "UNKNOWN"
    return ":".join(h[i : i + 2] for i in range(0, 12, 2))


def parse_gateway_packet(body: bytes) -> Dict[str, Any]:
    """Parses and validates a 38-byte gateway telemetry packet.

    Raises:
        ValueError: If packet length is not 38 bytes, node_id is out of range 1..255,
                    or any numeric float metric is NaN or infinite.
    """
    if len(body) != PAYLOAD_SIZE:
        raise ValueError(f"Expected body of exactly {PAYLOAD_SIZE} bytes, got {len(body)}")

    (
        mac_bytes,
        node_id,
        counter,
        tilt_x,
        tilt_y,
        vib_magnitude,
        vib_detected,
        batt_v,
        batt_pct,
        sos_active,
        node_danger_level,
        fast_mode,
        buzzer_on,
        last_cmd_seq,
    ) = struct.unpack(PAYLOAD_FMT, body)

    # Valid node_id range is 1..255 (node_id == 0 is strictly rejected)
    if node_id == 0 or node_id > 255:
        raise ValueError(f"Invalid node_id {node_id}: valid range is 1..255")

    # Validate finite float values
    metrics = {
        "tilt_x": tilt_x,
        "tilt_y": tilt_y,
        "vib_rms": vib_magnitude,
        "batt_v": batt_v,
        "batt_pct": batt_pct,
    }
    for name, val in metrics.items():
        if val is None or not math.isfinite(val):
            raise ValueError(f"Metric '{name}' is NaN or infinite")

    gateway_mac = norm_mac(":".join(f"{b:02X}" for b in mac_bytes))

    return {
        "gateway_mac": gateway_mac,
        "node_id": node_id,
        "counter": counter,
        "tilt_x": float(tilt_x),
        "tilt_y": float(tilt_y),
        "vib_rms": float(vib_magnitude),
        "shock": bool(vib_detected),
        "batt_v": float(batt_v),
        "batt_pct": float(batt_pct),
        "sos_active": bool(sos_active),
        "node_danger_level": int(node_danger_level),
        "fast_mode": bool(fast_mode),
        "buzzer_on": bool(buzzer_on),
        "last_cmd_seq": int(last_cmd_seq),
    }


def pack_gateway_packet(
    gateway_mac: str,
    node_id: int,
    counter: int,
    tilt_x: float,
    tilt_y: float,
    vib_rms: float,
    shock: bool,
    batt_v: float,
    batt_pct: float,
    sos_active: bool = False,
    danger_level: int = 0,
    fast_mode: bool = False,
    buzzer_on: bool = False,
    last_cmd_seq: int = 0,
) -> bytes:
    """Packs telemetry fields into the standard 38-byte gateway-to-server struct."""
    clean_mac = norm_mac(gateway_mac).replace(":", "")
    if len(clean_mac) != 12:
        mac_bytes = b"\x00" * 6
    else:
        mac_bytes = bytes.fromhex(clean_mac)

    return struct.pack(
        PAYLOAD_FMT,
        mac_bytes,
        int(node_id),
        int(counter),
        float(tilt_x),
        float(tilt_y),
        float(vib_rms),
        1 if shock else 0,
        float(batt_v),
        float(batt_pct),
        1 if sos_active else 0,
        int(danger_level),
        1 if fast_mode else 0,
        1 if buzzer_on else 0,
        int(last_cmd_seq),
    )
