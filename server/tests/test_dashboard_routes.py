"""Integration tests for web dashboard data endpoints.

Owns validating telemetry inspection, node listing, map placement, export, and health check.
Must NOT target production or development databases.
"""
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.services.packet_codec import pack_gateway_packet


def setup_dashboard_data(db, client):
    """Sets up company, mine, tunnel, gateway, and ingests telemetry for test nodes."""
    comp = Company(name="Eastern Coalfields Limited", name_normalized="eastern coalfields limited")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Raniganj Incline", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Main Trunk Gallery", code="TRUNK-1", status="active")
    db.add(tunnel)
    db.flush()

    gw_mac = "BC:FF:4D:11:88:99"
    gw = Gateway(mac=gw_mac, mine_id=mine.id, name="Raniganj East Gateway", active=True)
    db.add(gw)
    db.commit()

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
    client.post("/api/ingest", content=pkt, headers={"Content-Type": "application/octet-stream"})
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

    # 3. POST /api/placements
    place_payload = {
        "gateway_mac_address": gw_mac,
        "node_id": 1,
        "latitude": 23.6240,
        "longitude": 87.0880,
    }
    place_res = client.post("/api/placements", json=place_payload)
    assert place_res.status_code == 200

    node_db = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == 1).first()
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

    # 8. DELETE /api/placements/{gateway}/{node_id}
    del_res = client.delete(f"/api/placements/{gw_mac}/1")
    assert del_res.status_code == 200

    db.refresh(node_db)
    assert node_db.lat is None
    assert node_db.lon is None

    # 9. GET /api/health
    health_res = client.get("/api/health").json()
    assert health_res["status"] == "healthy"
    assert health_res["database"] == "PostgreSQL"
    assert health_res["payload_size"] == 38
