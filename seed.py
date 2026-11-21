"""Root wrapper for Server/seed.py"""
import os
import sys

server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Server")
sys.path.insert(0, server_dir)
os.chdir(server_dir)

from seed import main, parse_args, seed_company_and_manager

if __name__ == "__main__":
    args = parse_args()
    comp = args.company or "Bharat Coking Coal Limited"
    u = args.username or "manager"
    p = args.password or "Manager@2026"
    fn = args.full_name or "General Manager"
    print(f"--- Seeding Company: '{comp}', Manager: '{u}' ---")
    seed_company_and_manager(comp, u, p, fn)
    print("--- Seed completed successfully ---")
