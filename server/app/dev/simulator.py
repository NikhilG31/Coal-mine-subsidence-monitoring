"""Telemetry hardware simulation engine for local development.

Owns generating synthetic 38-byte ESP32 telemetry packets and posting them to /api/ingest.
Must NOT directly insert rows into the database or bypass HTTP ingest verification.
"""
import argparse
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from app.config import validate_demo_gateway_key
from app.services.hazard_classifier import classify_hazard
from app.services.packet_codec import norm_mac, pack_gateway_packet

DEMO_GATEWAYS: List[Dict] = [
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
                "baseline_tilt": (0.68, 0.32),
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
                "baseline_tilt": (1.45, -0.92),
                "baseline_vib": 0.185,
                "batt_v": 3.82,
                "batt_pct": 58.0,
            },
            {
                "id": 4,
                "name": "Surface Railway Siding Node",
                "lat": None,
                "lng": None,
                "placed": False,
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
                "baseline_tilt": (2.35, 1.82),
                "baseline_vib": 0.340,
                "vib_shock": True,
                "batt_v": 3.32,
                "batt_pct": 14.0,
            },
            {
                "id": 3,
                "name": "Overburden Spoil Heap #2",
                "lat": None,
                "lng": None,
                "placed": False,
                "baseline_tilt": (0.22, -0.15),
                "baseline_vib": 0.025,
                "batt_v": 3.90,
                "batt_pct": 68.0,
            },
        ],
    },
]


class Simulator:
    """Orchestrates realistic multi-node telemetry generation and HTTP delivery."""

    def __init__(
        self,
        target_url: str = "http://127.0.0.1:8000/api/ingest",
        gateway_key: Optional[str] = None,
    ):
        self.target_url = target_url
        if gateway_key:
            self.gateway_key = gateway_key
        else:
            try:
                self.gateway_key = validate_demo_gateway_key()
            except ValueError as e:
                print(f"[simulator] ERROR: {e}", file=sys.stderr)
                sys.exit(1)
        self.counters: Dict[str, int] = {}
        self.hazard_overrides: Dict[str, str] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_loop(self, interval_s: float = 5.0) -> None:
        if self.is_running:
            return
        self._stop_event.clear()

        def _worker():
            while not self._stop_event.is_set():
                self.step()
                self._stop_event.wait(interval_s)

        self._thread = threading.Thread(target=_worker, daemon=True)
        self._thread.start()

    def stop_loop(self) -> None:
        if self._thread:
            self._stop_event.set()
            self._thread.join(timeout=2.0)
            self._thread = None

    def trigger_hazard(self, gateway_mac: str, node_id: int, level: str) -> None:
        key = f"{norm_mac(gateway_mac)}:{int(node_id)}"
        self.hazard_overrides[key] = str(level).lower()

    def step(self) -> int:
        """Executes one simulation step across all nodes and sends packets."""
        sent_count = 0
        for gw in DEMO_GATEWAYS:
            gw_mac = norm_mac(gw["mac"])
            for node in gw["nodes"]:
                node_id = int(node["id"])
                key = f"{gw_mac}:{node_id}"
                cnt = self.counters.get(key, 100) + 1
                self.counters[key] = cnt

                override = self.hazard_overrides.get(key)
                if override == "critical":
                    tx = 2.5 + random.uniform(-0.1, 0.3)
                    ty = 2.1 + random.uniform(-0.1, 0.3)
                    vm = 0.35 + random.uniform(0.01, 0.08)
                    shock = True
                elif override == "warning":
                    tx = 1.4 + random.uniform(-0.1, 0.2)
                    ty = 0.8 + random.uniform(-0.1, 0.2)
                    vm = 0.18 + random.uniform(0.01, 0.04)
                    shock = True
                elif override == "watch":
                    tx = 0.65 + random.uniform(-0.05, 0.1)
                    ty = 0.3 + random.uniform(-0.05, 0.1)
                    vm = 0.08 + random.uniform(0.005, 0.02)
                    shock = False
                elif override == "normal":
                    tx = 0.1 + random.uniform(-0.02, 0.02)
                    ty = 0.05 + random.uniform(-0.02, 0.02)
                    vm = 0.015 + random.uniform(0.001, 0.005)
                    shock = False
                else:
                    b_tx, b_ty = node.get("baseline_tilt", (0.1, 0.05))
                    tx = b_tx + random.uniform(-0.02, 0.02)
                    ty = b_ty + random.uniform(-0.02, 0.02)
                    vm = node.get("baseline_vib", 0.018) + random.uniform(0.0, 0.005)
                    shock = bool(node.get("vib_shock", False))

                danger_level = classify_hazard(tx, ty, vm)
                pkt = pack_gateway_packet(
                    gateway_mac=gw_mac,
                    node_id=node_id,
                    counter=cnt,
                    tilt_x=tx,
                    tilt_y=ty,
                    vib_rms=vm,
                    shock=shock,
                    batt_v=node.get("batt_v", 4.0),
                    batt_pct=node.get("batt_pct", 90.0),
                    sos_active=False,
                    danger_level=danger_level,
                    fast_mode=danger_level > 0,
                    buzzer_on=False,
                    last_cmd_seq=0,
                )

                req = urllib.request.Request(
                    self.target_url,
                    data=pkt,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Gateway-Key": self.gateway_key,
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=2.0) as resp:
                        if resp.status == 200:
                            sent_count += 1
                except Exception:
                    pass
        return sent_count

    def send_sos(self, node_id: int = 1) -> int:
        """Sends a burst of 3 SOS packets 100 ms apart, then next regular packet with sos_active=1."""
        sent = 0
        gw = DEMO_GATEWAYS[0]
        gw_mac = norm_mac(gw["mac"])
        target_node = next((n for n in gw["nodes"] if n["id"] == node_id), gw["nodes"][0])

        bx, by = target_node["baseline_tilt"]
        bv = target_node["baseline_vib"]
        danger_level = classify_hazard(bx, by, bv)

        # Burst of 3 SOS packets 100 ms apart
        for _ in range(3):
            self.counters[node_id] = self.counters.get(node_id, 0) + 1
            cnt = self.counters[node_id]
            pkt = pack_gateway_packet(
                gateway_mac=gw_mac,
                node_id=node_id,
                counter=cnt,
                tilt_x=bx,
                tilt_y=by,
                vib_rms=bv,
                shock=False,
                batt_v=target_node.get("batt_v", 4.0),
                batt_pct=target_node.get("batt_pct", 90.0),
                sos_active=True,
                danger_level=danger_level,
                fast_mode=True,
                buzzer_on=False,
                last_cmd_seq=0,
            )
            req = urllib.request.Request(
                self.target_url,
                data=pkt,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Gateway-Key": self.gateway_key,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if resp.status == 200:
                        sent += 1
            except Exception:
                pass
            time.sleep(0.1)

        # Next regular packet with sos_active = 1
        self.counters[node_id] = self.counters.get(node_id, 0) + 1
        cnt = self.counters[node_id]
        pkt = pack_gateway_packet(
            gateway_mac=gw_mac,
            node_id=node_id,
            counter=cnt,
            tilt_x=bx,
            tilt_y=by,
            vib_rms=bv,
            shock=False,
            batt_v=target_node.get("batt_v", 4.0),
            batt_pct=target_node.get("batt_pct", 90.0),
            sos_active=True,
            danger_level=danger_level,
            fast_mode=True,
            buzzer_on=False,
            last_cmd_seq=0,
        )
        req = urllib.request.Request(
            self.target_url,
            data=pkt,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Gateway-Key": self.gateway_key,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status == 200:
                    sent += 1
        except Exception:
            pass

        return sent


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Telemetry Packet Simulator")
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/ingest", help="Target ingest endpoint")
    parser.add_argument("--once", action="store_true", help="Send a single burst of telemetry packets")
    parser.add_argument("--sos", action="store_true", help="Send 3 SOS burst packets 100ms apart plus follow-up SOS packet")
    parser.add_argument("--interval", type=float, default=5.0, help="Interval in seconds between simulation steps")
    args = parser.parse_args()

    sim = Simulator(target_url=args.url)
    if args.sos:
        count = sim.send_sos()
        print(f"[simulator] Sent {count} SOS packets in burst.")
    elif args.once:
        count = sim.step()
        print(f"[simulator] Sent {count} packets in single step.")
    else:
        print(f"[simulator] Starting simulator feed towards {args.url} every {args.interval}s...")
        sim.start_loop(interval_s=args.interval)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\n[simulator] Stopping simulation...")
            sim.stop_loop()


if __name__ == "__main__":
    main()
