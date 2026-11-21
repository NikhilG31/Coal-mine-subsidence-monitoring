"""Real-Time Telemetry Simulator for Coal Mine Subsidence Monitoring.

Generates realistic 38-byte ESP32 gateway telemetry packets and POSTs them
over HTTP to FastAPI (/api/ingest).

Format:
  <6sBIfffBffBBBBH (38 bytes, little-endian, no padding)
"""
import argparse
import math
import random
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

PAYLOAD_FMT = "<6sBIfffBffBBBBH"
assert struct.calcsize(PAYLOAD_FMT) == 38, f"Format must be 38 bytes, got {struct.calcsize(PAYLOAD_FMT)}"

# Demo Gateways & Nodes
DEMO_GATEWAYS = [
    {
        "mac": "A4:C1:38:90:12:01",
        "name": "Jharia Seam #11 Gateway (Dhanbad)",
        "nodes": [
            {
                "id": 1,
                "name": "North Wall Incline Crest",
                "lat": 23.7465,
                "lng": 86.4140,
                "placed": True,
                "baseline_tilt": (0.12, -0.08),
                "baseline_vib": 0.018,
                "batt_v": 4.12,
                "batt_pct": 92.0,
            },
            {
                "id": 2,
                "name": "Shaft Pillar #3 Surface",
                "lat": 23.7420,
                "lng": 86.4095,
                "placed": True,
                "baseline_tilt": (0.68, 0.32),  # Watch level
                "baseline_vib": 0.085,
                "batt_v": 3.95,
                "batt_pct": 74.0,
            },
            {
                "id": 3,
                "name": "Goaf Boundary Caved Zone",
                "lat": 23.7395,
                "lng": 86.4162,
                "placed": True,
                "baseline_tilt": (1.45, -0.92),  # Warning level
                "baseline_vib": 0.185,
                "batt_v": 3.82,
                "batt_pct": 58.0,
            },
            {
                "id": 4,
                "name": "Surface Railway Siding Node",
                "lat": None,
                "lng": None,
                "placed": False,  # UNPLACED
                "baseline_tilt": (0.10, 0.05),
                "baseline_vib": 0.020,
                "batt_v": 4.05,
                "batt_pct": 86.0,
            },
        ],
    },
    {
        "mac": "BC:FF:4D:11:88:99",
        "name": "Raniganj East Colliery Gateway (Asansol)",
        "nodes": [
            {
                "id": 1,
                "name": "Sripur Colliery Headframe",
                "lat": 23.6240,
                "lng": 87.0880,
                "placed": True,
                "baseline_tilt": (0.09, 0.04),
                "baseline_vib": 0.015,
                "batt_v": 4.18,
                "batt_pct": 95.0,
            },
            {
                "id": 2,
                "name": "Depillared Surface Crown",
                "lat": 23.6190,
                "lng": 87.0820,
                "placed": True,
                "baseline_tilt": (2.35, 1.82),  # Critical level
                "baseline_vib": 0.340,
                "vib_shock": True,
                "batt_v": 3.32,  # Low battery (<20%)
                "batt_pct": 14.0,
            },
            {
                "id": 3,
                "name": "Overburden Spoil Heap #2",
                "lat": None,
                "lng": None,
                "placed": False,  # UNPLACED
                "baseline_tilt": (0.22, -0.15),
                "baseline_vib": 0.025,
                "batt_v": 3.90,
                "batt_pct": 68.0,
            },
        ],
    },
]


def classify_node_danger(effective_tilt: float, vib_rms: float, vib_detected: bool) -> int:
    """Computes node danger level: 0 normal, 1 watch, 2 warning, 3 critical."""
    if effective_tilt > 2.0:
        tilt_lvl = 3
    elif effective_tilt >= 1.0:
        tilt_lvl = 2
    elif effective_tilt >= 0.5:
        tilt_lvl = 1
    else:
        tilt_lvl = 0

    if vib_rms > 0.30:
        vib_lvl = 3
    elif vib_rms >= 0.15:
        vib_lvl = 2
    elif vib_rms >= 0.05 or vib_detected:
        vib_lvl = 1
    else:
        vib_lvl = 0

    return max(tilt_lvl, vib_lvl)


def pack_payload(
    gateway_mac: str,
    node_id: int,
    counter: int,
    tilt_x: float,
    tilt_y: float,
    vibration_magnitude: float,
    vibration_detected: bool,
    battery_voltage: float,
    battery_percentage: float,
    sos_active: bool = False,
    danger_level: int = 0,
    fast_mode: bool = False,
    buzzer_on: bool = False,
    last_cmd_seq: int = 0,
) -> bytes:
    """Packs exactly 38 bytes matching the ESP32 gateway payload_t."""
    clean_mac = gateway_mac.replace(":", "").replace("-", "")
    mac_bytes = bytes.fromhex(clean_mac)
    return struct.pack(
        PAYLOAD_FMT,
        mac_bytes,
        int(node_id),
        int(counter),
        float(tilt_x),
        float(tilt_y),
        float(vibration_magnitude),
        int(bool(vibration_detected)),
        float(battery_voltage),
        float(battery_percentage),
        int(bool(sos_active)),
        int(danger_level),
        int(bool(fast_mode)),
        int(bool(buzzer_on)),
        int(last_cmd_seq),
    )


class Simulator:
    """Simulates live 5-second ESP sensor readings and posts them over HTTP."""

    def __init__(self, target_url: str = "http://127.0.0.1:8000/api/ingest"):
        self.target_url = target_url
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.counter = 100
        self.lock = threading.Lock()
        self.hazard_overrides: Dict[str, Dict] = {}

    def trigger_hazard(self, gateway_mac: str, node_id: int, level: str = "critical"):
        """Spikes node values to simulate subsidence events."""
        key = f"{gateway_mac}_{node_id}"
        if level == "critical":
            self.hazard_overrides[key] = {
                "tilt_x": 2.75,
                "tilt_y": 1.95,
                "vibration_magnitude": 0.42,
                "vibration_detected": True,
            }
        elif level == "warning":
            self.hazard_overrides[key] = {
                "tilt_x": 1.65,
                "tilt_y": 1.15,
                "vibration_magnitude": 0.22,
                "vibration_detected": True,
            }
        elif level == "watch":
            self.hazard_overrides[key] = {
                "tilt_x": 0.85,
                "tilt_y": 0.45,
                "vibration_magnitude": 0.09,
                "vibration_detected": False,
            }
        else:
            self.hazard_overrides.pop(key, None)

    def generate_packets(self) -> List[tuple]:
        """Generates raw 38-byte packets and metadata for all demo nodes."""
        with self.lock:
            self.counter += 1
            cnt = self.counter

        packets = []
        for gw in DEMO_GATEWAYS:
            for n in gw["nodes"]:
                key = f"{gw['mac']}_{n['id']}"
                hazard = self.hazard_overrides.get(key)

                if hazard:
                    tx = hazard.get("tilt_x", n["baseline_tilt"][0])
                    ty = hazard.get("tilt_y", n["baseline_tilt"][1])
                    vm = hazard.get("vibration_magnitude", n["baseline_vib"])
                    vd = hazard.get("vibration_detected", True)
                else:
                    jitter_x = (random.random() - 0.5) * 0.04
                    jitter_y = (random.random() - 0.5) * 0.04
                    vib_jitter = (random.random() - 0.5) * 0.006

                    tx = n["baseline_tilt"][0] + jitter_x
                    ty = n["baseline_tilt"][1] + jitter_y
                    vm = max(0.008, n["baseline_vib"] + vib_jitter)
                    vd = n.get("vib_shock", False)

                # Slow battery discharge simulation
                batt_v = max(3.0, n["batt_v"] - 0.0001)
                batt_pct = max(1.0, n["batt_pct"] - 0.005)

                eff_tilt = max(math.sqrt(tx * tx + ty * ty), max(abs(tx), abs(ty)))
                danger_lvl = classify_node_danger(eff_tilt, vm, vd)
                fast_mode = danger_lvl >= 1

                raw = pack_payload(
                    gateway_mac=gw["mac"],
                    node_id=n["id"],
                    counter=cnt,
                    tilt_x=tx,
                    tilt_y=ty,
                    vibration_magnitude=vm,
                    vibration_detected=vd,
                    battery_voltage=batt_v,
                    battery_percentage=batt_pct,
                    sos_active=False,
                    danger_level=danger_lvl,
                    fast_mode=fast_mode,
                    buzzer_on=False,
                    last_cmd_seq=0,
                )
                packets.append((gw["mac"], n["id"], cnt, danger_lvl, raw))

        return packets

    def post_packet_http(self, raw_bytes: bytes, gateway_mac: str, node_id: int, cnt: int, danger_lvl: int) -> bool:
        """Sends a 38-byte packet via HTTP POST to /api/ingest."""
        req = urllib.request.Request(
            self.target_url,
            data=raw_bytes,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Gateway-Id": "gw0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                status = resp.status
                level_names = {0: "NORMAL", 1: "WATCH", 2: "WARNING", 3: "CRITICAL"}
                print(
                    f"[simulator] POST {self.target_url} -> GW {gateway_mac} Node #{node_id} "
                    f"Cnt={cnt} Lvl={level_names.get(danger_lvl, danger_lvl)}: HTTP {status}"
                )
                return status == 200
        except Exception as e:
            print(f"[simulator] Failed to post packet (GW {gateway_mac} Node #{node_id}): {e}")
            return False

    def step(self) -> int:
        """Generates and posts one round of packets for all nodes."""
        packets = self.generate_packets()
        success_count = 0
        for mac, nid, cnt, danger_lvl, raw in packets:
            if self.post_packet_http(raw, mac, nid, cnt, danger_lvl):
                success_count += 1
        return success_count

    def _loop(self, interval: float = 5.0):
        while self.is_running:
            self.step()
            time.sleep(interval)

    def start_loop(self, interval: float = 5.0):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._loop, args=(interval,), daemon=True)
        self.thread.start()
        print(f"[simulator] Live packet feed started (every {interval}s to {self.target_url})")

    def stop_loop(self):
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        self.thread = None
        print("[simulator] Live packet feed stopped")


def main():
    parser = argparse.ArgumentParser(description="Coal Mine Subsidence Telemetry Packet Simulator")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/ingest", help="Ingest endpoint URL")
    parser.add_argument("--interval", type=float, default=5.0, help="Packet interval in seconds (default: 5.0)")
    parser.add_argument("--once", action="store_true", help="Send exactly one burst of packets and exit")
    args = parser.parse_args()

    sim = Simulator(target_url=args.url)
    print(f"=== Starting Mine Subsidence Simulator (Target: {args.url}) ===")
    print("Sending 38-byte binary packets (<6sBIfffBffBBBBH) every 5 seconds...")

    if args.once:
        count = sim.step()
        print(f"Completed single step: {count} packets delivered.")
        return

    sim.start_loop(interval=args.interval)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping simulator...")
        sim.stop_loop()


if __name__ == "__main__":
    main()
