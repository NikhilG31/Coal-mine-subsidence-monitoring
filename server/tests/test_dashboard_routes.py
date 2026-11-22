"""Integration tests for web dashboard data endpoints.

Owns validating telemetry inspection, node listing, map placement, export, and health check.
Must NOT target production or development databases.
"""
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.models.user import User
from app.security.passwords import hash_password
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.gateway_keys import generate_key, hash_key
from app.services.packet_codec import pack_gateway_packet


def setup_dashboard_data(db, client):
    """Sets up company, mine, tunnel, gateway, and ingests telemetry for test nodes."""
    comp = Company(name="Eastern Coalfields Limited", name_normalized="eastern coalfields limited")
    db.add(comp)
    db.flush()

    user = User(
        company_id=comp.id,
        username="dash_mgr",
        password_hash=hash_password("ValidPassword123!"),
        full_name="Dashboard Manager",
        role="manager",
        active=True,
        force_pw_change=False,
    )
    db.add(user)
    db.flush()

    mine = Mine(company_id=comp.id, name="Raniganj Incline", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Main Trunk Gallery", code="TRUNK-1", status="active")
    db.add(tunnel)
    db.flush()

    gw_mac = "BC:FF:4D:11:88:99"
    raw_key = generate_key()
    gw = Gateway(
        mac=gw_mac,
        mine_id=mine.id,
        name="Raniganj East Gateway",
        api_key_hash=hash_key(raw_key),
        active=True,
    )
    db.add(gw)
    db.commit()

    token = create_session_token(user.id)
    client.cookies.set(COOKIE_NAME, token)

    # Ingest telemetry for Node 1
    pkt = pack_gateway_packet(
        gateway_mac=gw_mac,
        node_id=1,
        counter=200,
        tilt_x=0.15,
        tilt_y=0.10,
        vib_rms=0.02,
        shock=False,
        batt_v=4.05,
        batt_pct=85.0,
    )
    client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key},
    )
    return gw, tunnel


def test_dashboard_routes_and_placement_workflow(client, db):
    """Verifies all dashboard endpoints and node placement/unplacement lifecycle."""
    gw, tunnel = setup_dashboard_data(db, client)
    gw_mac = gw.mac

    # 1. GET /api/nodes
    nodes_res = client.get("/api/nodes").json()
    assert "now" in nodes_res
    assert "nodes" in nodes_res
    assert len(nodes_res["nodes"]) >= 1
    node_1 = next(n for n in nodes_res["nodes"] if n["node_id"] == 1)
    assert node_1["gateway_mac_address"] == gw_mac
    assert node_1["status"] == "normal"
    assert node_1["placed"] is False

    # 2. GET /api/unplaced
    unplaced_res = client.get("/api/unplaced").json()
    assert "unplaced" in unplaced_res
    assert any(n["node_id"] == 1 for n in unplaced_res["unplaced"])

    # 3. PATCH /api/nodes/{id}/placement
    node_db = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == 1).first()
    place_payload = {
        "tunnel_id": tunnel.id,
        "lat": 23.6240,
        "lon": 87.0880,
    }
    place_res = client.patch(f"/api/nodes/{node_db.id}/placement", json=place_payload)
    assert place_res.status_code == 200

    db.refresh(node_db)
    assert node_db.lat == 23.6240
    assert node_db.lon == 87.0880
    assert node_db.tunnel_id == tunnel.id

    # 4. GET /api/placements
    placements_res = client.get("/api/placements").json()
    assert "placements" in placements_res
    p1 = next(p for p in placements_res["placements"] if p["node_id"] == 1)
    assert p1["latitude"] == 23.6240

    # 5. GET /api/gateways
    gw_res = client.get("/api/gateways").json()
    assert "gateways" in gw_res
    gw_entry = next(g for g in gw_res["gateways"] if g["gateway_mac_address"] == gw_mac)
    assert gw_entry["node_count"] == 1
    assert gw_entry["placed_nodes"] == 1

    # 6. GET /api/history
    hist_res = client.get(f"/api/history?gateway={gw_mac}&node_id=1").json()
    assert "readings" in hist_res
    assert len(hist_res["readings"]) >= 1

    # 7. GET /api/export.xlsx
    export_res = client.get(f"/api/export.xlsx?gateway={gw_mac}")
    assert export_res.status_code == 200
    assert export_res.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert len(export_res.content) > 1000

    # 8. DELETE /api/nodes/{id}/placement
    del_res = client.delete(f"/api/nodes/{node_db.id}/placement")
    assert del_res.status_code == 200

    db.refresh(node_db)
    assert node_db.lat is None
    assert node_db.lon is None

    # 9. GET /api/health
    health_res = client.get("/api/health").json()
    assert health_res["status"] == "healthy"
    assert health_res["database"] == "PostgreSQL"
    assert health_res["payload_size"] == 38


def test_trigger_hazard_returns_under_5_seconds(client, db):
    """A3: Asserts POST /api/demo/trigger-hazard returns in under 5 seconds against a running server."""
    import threading
    import time
    import uvicorn
    from app.main import app
    from app.routers.demo import simulator_instance

    setup_dashboard_data(db, client)

    # Start live server on an ephemeral port so simulator packets are received concurrently
    server_port = 8894
    config = uvicorn.Config(app, host="127.0.0.1", port=server_port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(0.5)

    old_url = simulator_instance.target_url
    simulator_instance.target_url = f"http://127.0.0.1:{server_port}/api/ingest"

    try:
        t0 = time.time()
        resp = client.post(
            "/api/demo/trigger-hazard",
            json={
                "gateway_mac_address": "BC:FF:4D:11:88:99",
                "node_id": 1,
                "level": "critical",
            },
        )
        duration = time.time() - t0
        assert resp.status_code == 200
        assert duration < 5.0, f"Hazard trigger took {duration:.2f}s, expected under 5 seconds"
    finally:
        simulator_instance.target_url = old_url
        server.should_exit = True
        thread.join(timeout=2)
