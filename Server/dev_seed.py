"""DEV ONLY: Replaced by the step 3 onboarding flow.

Ingest needs a mine, a tunnel, and a gateway row to exist in PostgreSQL,
but the real user flow for creating them (onboarding screen, gateway registration)
is implemented in Step 3. This dev-only helper creates:
- One mine
- One tunnel
- Gateways matching the simulator MACs (including 'A4:C1:38:90:12:01' and 'BC:FF:4D:11:88:99')
with placeholder api_key_hash values.
"""
import os
import sys

# Ensure current directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal
from models import Company, Gateway, Mine, Tunnel, normalize_company_name


def dev_seed():
    db = SessionLocal()
    try:
        # 1. Resolve company (seeded by seed.py, or create fallback)
        company = db.query(Company).first()
        if not company:
            company = Company(
                name="Bharat Coking Coal Limited",
                name_normalized=normalize_company_name("Bharat Coking Coal Limited"),
            )
            db.add(company)
            db.flush()
            print(f"[dev_seed] Created fallback company: {company.name}")
        else:
            print(f"[dev_seed] Using company: {company.name} (id={company.id})")

        # 2. Mine
        mine = db.query(Mine).filter(Mine.company_id == company.id, Mine.name == "Jharia Seam #11 Colliery").first()
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
            print(f"[dev_seed] Created dev mine: {mine.name} (id={mine.id})")
        else:
            print(f"[dev_seed] Dev mine already exists: {mine.name} (id={mine.id})")

        # 3. Tunnel
        tunnel = db.query(Tunnel).filter(Tunnel.mine_id == mine.id, Tunnel.code == "TUNNEL-11").first()
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
            print(f"[dev_seed] Created dev tunnel: {tunnel.name} (id={tunnel.id})")
        else:
            print(f"[dev_seed] Dev tunnel already exists: {tunnel.name} (id={tunnel.id})")

        # 4. Gateways matching simulator
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
                print(f"[dev_seed] Created dev gateway: {name} ({mac})")
            else:
                print(f"[dev_seed] Dev gateway already exists: {name} ({mac})")

        db.commit()
        print("[dev_seed] Dev seed completed successfully.")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    print("--- Running dev_seed.py (DEV ONLY helper) ---")
    dev_seed()
