"""Integration tests for telemetry ingestion endpoint and gateway key authentication.

Owns validating packet length enforcement, gateway verification, deduplication, and node auto-discovery.
Must NOT target non-test database environments.
"""
import datetime
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.reading import Reading
from app.services.gateway_keys import generate_key, hash_key
from app.services.packet_codec import pack_gateway_packet


def setup_gateway(db, mac="A4:C1:38:90:12:01"):
    """Helper to seed prerequisite company, mine, and authenticated gateway."""
    comp = Company(name="Test Coal Corp", name_normalized="test coal corp")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Test Mine 1", status="active", method="underground")
    db.add(mine)
    db.flush()

    raw_key = generate_key()
    gw = Gateway(
        mac=mac,
        mine_id=mine.id,
        name="Test Ingest Gateway",
        api_key_hash=hash_key(raw_key),
        active=True,
    )
    db.add(gw)
    db.commit()
    return gw, raw_key


def test_payload_length_enforcement(client):
    """Verifies that non-38-byte payloads are strictly rejected with HTTP 400."""
    for bad_len in (32, 37, 39):
        resp = client.post(
            "/api/ingest",
            content=b"\x00" * bad_len,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 400
        assert f"Expected body of exactly 38 bytes, got {bad_len}" in resp.json()["detail"]


def test_node_id_zero_rejected(client, db):
    """F7: Ingest returns 400 when node_id == 0 (valid range is 1..255)."""
    gw, key = setup_gateway(db)
    pkt = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=0,
        counter=1,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=100.0,
    )
    resp = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": key},
    )
    assert resp.status_code == 400
    assert "Invalid node_id 0" in resp.json()["detail"]


def test_unknown_gateway_rejected(client):
    """B2: Unregistered gateway MAC addresses fail authentication with HTTP 401."""
    pkt = pack_gateway_packet(
        gateway_mac="00:11:22:33:44:55",
        node_id=1,
        counter=1,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
    )
    resp = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwk_dummytestkey12345678901234567890"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Gateway authentication failed"


def test_valid_packet_node_autocreation_and_independent_classification(client, db):
    """Verifies node auto-creation and server independent hazard classification."""
    gw, key = setup_gateway(db)
    test_node_no = 42

    # Send packet claiming danger_level=0, but tilt is 2.3° (Critical!)
    pkt = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=test_node_no,
        counter=500,
        tilt_x=2.3,
        tilt_y=0.1,
        vib_rms=0.02,
        shock=False,
        batt_v=4.1,
        batt_pct=88.0,
        danger_level=0,
        last_cmd_seq=7,
    )
    resp = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": key},
    )
    assert resp.status_code == 200
    assert resp.content == b""

    # Verify node was auto-created as unplaced
    node = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == test_node_no).first()
    assert node is not None
    assert node.tunnel_id is None
    assert node.lat is None
    assert node.lon is None
    assert node.status == "critical"
    assert node.battery_pct == 88.0
    assert node.last_cmd_seq == 7

    reading = db.query(Reading).filter(Reading.node_id == node.id).first()
    assert reading is not None
    assert reading.level == 3
    assert reading.node_level == 0


def test_deduplication_and_post_reboot_handling(client, db):
    """Verifies 4-second deduplication window and >4s post-reboot reading acceptance."""
    gw, key = setup_gateway(db)
    test_node_no = 15

    pkt = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=test_node_no,
        counter=101,
        tilt_x=0.2,
        tilt_y=0.1,
        vib_rms=0.02,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
    )

    # 1. First arrival
    r1 = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": key},
    )
    assert r1.status_code == 200

    node = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == test_node_no).first()
    assert db.query(Reading).filter(Reading.node_id == node.id).count() == 1

    # 2. Duplicate arrival within 4 seconds: must be skipped
    r2 = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": key},
    )
    assert r2.status_code == 200
    assert r2.content == b""
    assert db.query(Reading).filter(Reading.node_id == node.id).count() == 1

    # 3. Simulate reboot: set existing reading timestamp 10s in the past
    reading = db.query(Reading).filter(Reading.node_id == node.id).first()
    reading.ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=10)
    db.commit()

    # 4. Same counter packet arrives >4s later: must be inserted as post-reboot
    r3 = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": key},
    )
    assert r3.status_code == 200
    assert db.query(Reading).filter(Reading.node_id == node.id).count() == 2
