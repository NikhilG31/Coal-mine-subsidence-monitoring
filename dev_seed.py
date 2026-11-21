"""Root wrapper for Server/dev_seed.py (DEV ONLY)."""
import os
import sys

server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Server")
sys.path.insert(0, server_dir)
os.chdir(server_dir)

from dev_seed import dev_seed

if __name__ == "__main__":
    print("--- Running dev_seed.py (DEV ONLY helper) ---")
    dev_seed()
