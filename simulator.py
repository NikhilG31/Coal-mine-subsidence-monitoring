"""Root wrapper for Server/simulator.py"""
import os
import sys

server_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Server")
sys.path.insert(0, server_dir)
os.chdir(server_dir)

from simulator import main

if __name__ == "__main__":
    main()
