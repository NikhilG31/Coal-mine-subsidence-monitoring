"""Development seed script for demo mines, tunnels, and gateways.

Owns seeding sample mine sites and gateways matching simulator MAC addresses for local testing.
Must NOT execute in production environments or create user credentials.
"""
from app.database import SessionLocal
from app.models.company import Company, normalize_company_name
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.tunnel import Tunnel


def seed_demo_data() -> None:
    """Creates sample company, mine, tunnel, and gateways for development testing."""
    db = SessionLocal()
    try:
        # 1. Resolve or create company
        company = db.query(Company).first()
        if not company:
            company = Company(
                name="Bharat Coking Coal Limited",
                name_normalized=normalize_company_name("Bharat Coking Coal Limited"),
            )
            db.add(company)
            db.flush()
            print(f"[seed_demo_data] Created fallback company: {company.name}")
        else:
            print(f"[seed_demo_data] Using company: {company.name} (id={company.id})")

        # 2. Mine
        mine = db.query(Mine).filter(
            Mine.company_id == company.id,
            Mine.name == "Jharia Seam #11 Colliery",
        ).first()
        if not mine:
            mine = Mine(
                company_id=company.id,
                name="Jharia Seam #11 Colliery",
                subsidiary="BCCL",
                status="active",
                method="underground",
                area_ha=450.0,
                max_depth_m=220.0,
                lat=23.7438,
                lon=86.4116,
            )
            db.add(mine)
            db.flush()
            print(f"[seed_demo_data] Created dev mine: {mine.name} (id={mine.id})")
        else:
            print(f"[seed_demo_data] Dev mine already exists: {mine.name} (id={mine.id})")

        # 3. Tunnel
        tunnel = db.query(Tunnel).filter(
            Tunnel.mine_id == mine.id,
            Tunnel.code == "TUNNEL-11",
        ).first()
        if not tunnel:
            tunnel = Tunnel(
                mine_id=mine.id,
                name="Main Incline Shaft 11",
                code="TUNNEL-11",
                depth_m=180.0,
                status="active",
            )
            db.add(tunnel)
            db.flush()
            print(f"[seed_demo_data] Created dev tunnel: {tunnel.name} (id={tunnel.id})")
        else:
            print(f"[seed_demo_data] Dev tunnel already exists: {tunnel.name} (id={tunnel.id})")

        # 4. Gateways matching telemetry simulator
        simulator_gateways = [
            ("A4:C1:38:90:12:01", "Jharia Seam #11 Gateway (Dhanbad)"),
            ("BC:FF:4D:11:88:99", "Raniganj East Colliery Gateway (Asansol)"),
        ]

        for mac, name in simulator_gateways:
            gw = db.query(Gateway).filter(Gateway.mac == mac).first()
            if not gw:
                gw = Gateway(
                    mac=mac,
                    mine_id=mine.id,
                    name=name,
                    api_key_hash="placeholder_dev_api_key_hash",
                    active=True,
                )
                db.add(gw)
                db.flush()
                print(f"[seed_demo_data] Created dev gateway: {name} ({mac})")
            else:
                print(f"[seed_demo_data] Dev gateway already exists: {name} ({mac})")

        db.commit()
        print("[seed_demo_data] Dev seed completed successfully.")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    print("--- Running seed_demo_data.py ---")
    seed_demo_data()
