"""Development seed script for demo mines, tunnels, and gateways.

Owns seeding sample mine sites and gateways matching simulator MAC addresses for local testing.
Must NOT execute in production environments or create user credentials.
"""
import argparse
import sys
from typing import Optional

from app.config import validate_demo_gateway_key
from app.database import SessionLocal
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.tunnel import Tunnel
from app.services.gateway_keys import hash_key


def seed_demo_data(company_name: Optional[str] = None) -> None:
    """Creates sample mine, tunnel, and gateways for development testing."""
    try:
        demo_key = validate_demo_gateway_key()
    except ValueError as e:
        print(f"[seed_demo_data] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    key_hash = hash_key(demo_key)
    db = SessionLocal()
    try:
        # 1. Resolve company
        if company_name:
            company = db.query(Company).filter(Company.name == company_name.strip()).first()
            if not company:
                print(f"[seed_demo_data] ERROR: Company '{company_name}' not found.", file=sys.stderr)
                sys.exit(1)
        else:
            companies = db.query(Company).all()
            if len(companies) == 0:
                print(
                    "[seed_demo_data] ERROR: No companies found in database. Please run seed_manager first.",
                    file=sys.stderr,
                )
                sys.exit(1)
            elif len(companies) > 1:
                print(
                    "[seed_demo_data] ERROR: Multiple companies exist. Please specify --company.",
                    file=sys.stderr,
                )
                sys.exit(1)
            company = companies[0]

        print(f"[seed_demo_data] Using company: {company.name} (id={company.id})")

        # 2. Mine
        mine = (
            db.query(Mine)
            .filter(
                Mine.company_id == company.id,
                Mine.name == "Jharia Seam #11 Colliery",
            )
            .first()
        )
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
        tunnel = (
            db.query(Tunnel)
            .filter(
                Tunnel.mine_id == mine.id,
                Tunnel.code == "TUNNEL-11",
            )
            .first()
        )
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
                    api_key_hash=key_hash,
                    active=True,
                )
                db.add(gw)
                db.flush()
                print(f"[seed_demo_data] Created dev gateway: {name} ({mac})")
            else:
                gw.api_key_hash = key_hash
                gw.active = True
                print(f"[seed_demo_data] Updated dev gateway with valid hash: {name} ({mac})")

        db.commit()
        print("[seed_demo_data] Dev seed completed successfully.")

    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed development mine, tunnel, and gateway dataset")
    parser.add_argument("--company", type=str, default=None, help="Target company name")
    args = parser.parse_args()
    seed_demo_data(company_name=args.company)


if __name__ == "__main__":
    main()
