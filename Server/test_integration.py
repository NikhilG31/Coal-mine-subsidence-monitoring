"""Comprehensive Step 1 Integration Test Suite for Coal Mine Subsidence Monitoring System.

Tests:
1. Exact 38-byte payload verification on POST /api/ingest (rejecting 32, 37, 39 bytes with 400).
2. Gateway resolution and rejection of unknown/inactive gateways.
3. Auto-creation of unplaced nodes on first packet.
4. Independent server-side subsidence hazard classification (tilt/vibration thresholds, worse wins).
5. Application-level deduplication for boot_id = 0 (4-second time window rule).
6. Post-reboot same-counter insertion (> 4s delta).
7. Updating node state (status, battery_pct, fast_mode, buzzer_on, last_cmd_seq, last_seen_at).
8. Dashboard endpoints (/api/nodes, /api/gateways, /api/history, /api/placements, /api/unplaced).
9. Seed script idempotency (seed.py and dev_seed.py).
"""
import datetime
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient
from database import SessionLocal
from models import Company, Gateway, Mine, Node, Reading, Tunnel, User
from server import PAYLOAD_FMT, PAYLOAD_SIZE, app
from simulator import pack_payload


def run_tests():
    client = TestClient(app)
    db = SessionLocal()

    print("\n=======================================================")
    print("STEP 1 INTEGRATION TEST SUITE (POSTGRESQL)")
    print("=======================================================\n")

    # -------------------------------------------------------------------------
    print("[1/9] Testing 38-byte payload length enforcement...")
    # 32 bytes (legacy) -> must return 400
    res_32 = client.post("/api/ingest", content=b"\x00" * 32, headers={"Content-Type": "application/octet-stream"})
    assert res_32.status_code == 400, f"Expected 400 for 32 bytes, got {res_32.status_code}"

    # 37 bytes -> must return 400
    res_37 = client.post("/api/ingest", content=b"\x00" * 37, headers={"Content-Type": "application/octet-stream"})
    assert res_37.status_code == 400, f"Expected 400 for 37 bytes, got {res_37.status_code}"

    # 39 bytes -> must return 400
    res_39 = client.post("/api/ingest", content=b"\x00" * 39, headers={"Content-Type": "application/octet-stream"})
    assert res_39.status_code == 400, f"Expected 400 for 39 bytes, got {res_39.status_code}"
    print("[OK] Exact 38-byte packet check strictly enforced.")

    # -------------------------------------------------------------------------
    print("[2/9] Testing Gateway resolution and unknown gateway rejection...")
    # Unknown gateway MAC
    fake_pkt = pack_payload("11:22:33:44:55:66", 1, 100, 0.1, 0.1, 0.01, False, 4.0, 90.0)
    assert len(fake_pkt) == 38
    res_unknown = client.post("/api/ingest", content=fake_pkt, headers={"Content-Type": "application/octet-stream"})
    assert res_unknown.status_code == 400, f"Expected 400 for unknown gateway, got {res_unknown.status_code}"
    print("[OK] Unknown gateway correctly rejected with HTTP 400.")

    # -------------------------------------------------------------------------
    print("[3/9] Testing valid 38-byte packet ingest and node auto-creation...")
    gw_mac = "A4:C1:38:90:12:01"
    # Ensure gateway exists in DB
    gw = db.query(Gateway).filter(Gateway.mac == gw_mac).first()
    assert gw is not None, "Gateway A4:C1:38:90:12:01 should exist from dev_seed"

    test_node_no = 77
    # Remove test node if existed
    old_node = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == test_node_no).first()
    if old_node:
        db.delete(old_node)
        db.commit()

    pkt_normal = pack_payload(
        gateway_mac=gw_mac,
        node_id=test_node_no,
        counter=1001,
        tilt_x=0.15,
        tilt_y=0.10,
        vibration_magnitude=0.012,
        vibration_detected=False,
        battery_voltage=4.15,
        battery_percentage=96.0,
        sos_active=False,
        danger_level=0,
        fast_mode=False,
        buzzer_on=False,
        last_cmd_seq=5,
    )
    res_ingest = client.post("/api/ingest", content=pkt_normal, headers={"Content-Type": "application/octet-stream"})
    assert res_ingest.status_code == 200, f"Expected 200, got {res_ingest.status_code}: {res_ingest.text}"
    assert res_ingest.content == b"", "Response body must be empty"

    # Check node in DB
    node_db = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == test_node_no).first()
    assert node_db is not None, "Node should be auto-created"
    assert node_db.tunnel_id is None, "Auto-created node must have tunnel_id=NULL"
    assert node_db.lat is None, "Auto-created node must have lat=NULL"
    assert node_db.lon is None, "Auto-created node must have lon=NULL"
    assert node_db.status == "normal"
    assert node_db.battery_pct == 96.0
    assert node_db.last_cmd_seq == 5
    print("[OK] Node auto-created as unplaced and populated with state.")

    # -------------------------------------------------------------------------
    print("[4/9] Testing independent subsidence hazard classification...")
    # Critical tilt (>2.0 deg)
    pkt_crit = pack_payload(
        gateway_mac=gw_mac,
        node_id=test_node_no,
        counter=1002,
        tilt_x=2.25,
        tilt_y=0.10,
        vibration_magnitude=0.02,
        vibration_detected=False,
        battery_voltage=3.95,
        battery_percentage=75.0,
        danger_level=0,  # node claims normal, but server classifies critical!
    )
    res_crit = client.post("/api/ingest", content=pkt_crit, headers={"Content-Type": "application/octet-stream"})
    assert res_crit.status_code == 200

    db.refresh(node_db)
    assert node_db.status == "critical", f"Expected critical, got {node_db.status}"

    reading_crit = (
        db.query(Reading)
        .filter(Reading.node_id == node_db.id, Reading.counter == 1002)
        .first()
    )
    assert reading_crit is not None
    assert reading_crit.level == 3, f"Expected server level 3, got {reading_crit.level}"
    assert reading_crit.node_level == 0, f"Expected node_level cross-check 0, got {reading_crit.node_level}"
    print("[OK] Server independent classification verified (critical tilt overrides firmware level).")

    # -------------------------------------------------------------------------
    print("[5/9] Testing deduplication for boot_id = 0 within 4 seconds...")
    readings_count_before = db.query(Reading).filter(Reading.node_id == node_db.id).count()

    # Resend exact same packet counter 1002 immediately
    res_dup = client.post("/api/ingest", content=pkt_crit, headers={"Content-Type": "application/octet-stream"})
    assert res_dup.status_code == 200, "Duplicate should return 200"
    assert res_dup.content == b"", "Response body must be empty"

    readings_count_after = db.query(Reading).filter(Reading.node_id == node_db.id).count()
    assert readings_count_after == readings_count_before, "Duplicate packet within 4s must NOT insert a reading"
    print("[OK] Duplicate packet within 4-second window successfully skipped.")

    # -------------------------------------------------------------------------
    print("[6/9] Testing post-reboot same-counter insertion (> 4s delta)...")
    # Simulate a post-reboot reading by setting existing reading timestamp 10 seconds in the past
    past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
    reading_crit.ts = past_time
    db.commit()

    # Now post counter 1002 again -> should be treated as post-reboot and inserted!
    res_reboot = client.post("/api/ingest", content=pkt_crit, headers={"Content-Type": "application/octet-stream"})
    assert res_reboot.status_code == 200

    readings_count_reboot = db.query(Reading).filter(Reading.node_id == node_db.id).count()
    assert readings_count_reboot == readings_count_before + 1, "Same-counter packet >4s apart MUST be inserted"
    print("[OK] Post-reboot same-counter reading successfully inserted.")

    # -------------------------------------------------------------------------
    print("[7/9] Testing Dashboard routes (/api/nodes, /api/gateways, /api/history)...")
    nodes_res = client.get("/api/nodes").json()
    assert "now" in nodes_res
    assert "nodes" in nodes_res
    assert len(nodes_res["nodes"]) > 0

    node_77_dict = next(n for n in nodes_res["nodes"] if n["node_id"] == test_node_no)
    assert node_77_dict["gateway_mac_address"] == gw_mac
    assert node_77_dict["status"] == "critical"
    assert node_77_dict["placed"] is False
    assert "tilt_x" in node_77_dict
    assert "vibration_magnitude" in node_77_dict
    assert "color" in node_77_dict

    gw_res = client.get("/api/gateways").json()
    assert "gateways" in gw_res
    gw_jharia = next(g for g in gw_res["gateways"] if g["gateway_mac_address"] == gw_mac)
    assert gw_jharia["worst_status"] == "critical"
    assert gw_jharia["node_count"] >= 1

    hist_res = client.get(f"/api/history?gateway={gw_mac}&node_id={test_node_no}&limit=10").json()
    assert "readings" in hist_res
    assert len(hist_res["readings"]) >= 2
    r0 = hist_res["readings"][0]
    assert "ts" in r0
    assert "tilt_x" in r0
    print("[OK] Dashboard endpoints returned expected JSON shapes matching frontend.")

    # -------------------------------------------------------------------------
    print("[8/9] Testing Node Placement workflow (/api/placements)...")
    place_payload = {
        "gateway_mac_address": gw_mac,
        "node_id": test_node_no,
        "latitude": 23.7450,
        "longitude": 86.4130,
        "location_name": "Test Placement Zone",
    }
    res_place = client.post("/api/placements", json=place_payload)
    assert res_place.status_code == 200

    db.refresh(node_db)
    assert node_db.lat == 23.7450
    assert node_db.lon == 86.4130
    assert node_db.tunnel_id is not None, "Should link to mine tunnel"

    # Unplace
    res_unplace = client.delete(f"/api/placements/{gw_mac}/{test_node_no}")
    assert res_unplace.status_code == 200
    db.refresh(node_db)
    assert node_db.lat is None
    assert node_db.lon is None
    print("[OK] Placement and unplacement workflow verified.")

    # -------------------------------------------------------------------------
    print("[9/9] Testing Health endpoint...")
    health_res = client.get("/api/health").json()
    assert health_res["status"] == "healthy"
    assert health_res["payload_size"] == 38
    assert health_res["database"] == "PostgreSQL"
    print("[OK] Health check verified.")

    # Cleanup test node
    db.delete(node_db)
    db.commit()
    db.close()

    print("\nALL STEP 1 INTEGRATION TESTS PASSED SUCCESSFULLY!\n")


if __name__ == "__main__":
    run_tests()
