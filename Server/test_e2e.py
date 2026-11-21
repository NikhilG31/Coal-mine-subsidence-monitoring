"""End-to-end server + simulator test over HTTP."""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from simulator import Simulator


def main():
    print("[1/4] Starting FastAPI server on port 8000...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    time.sleep(3)

    try:
        print("[2/4] Verifying /api/health...")
        with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5) as res:
            health = json.loads(res.read().decode())
            print("Health check response:", health)
            assert health["status"] == "healthy"
            assert health["database"] == "PostgreSQL"
            assert health["payload_size"] == 38

        print("[3/4] Running Simulator step (posting 38-byte packets over HTTP)...")
        sim = Simulator("http://127.0.0.1:8000/api/ingest")
        count = sim.step()
        print(f"Simulator successfully delivered {count} packets over HTTP!")
        assert count >= 6, f"Expected at least 6 packets, got {count}"

        print("[4/4] Verifying /api/nodes after simulator ingest...")
        with urllib.request.urlopen("http://127.0.0.1:8000/api/nodes", timeout=5) as res:
            data = json.loads(res.read().decode())
            nodes = data.get("nodes", [])
            print(f"Retrieved {len(nodes)} nodes from PostgreSQL:")
            for n in nodes:
                print(
                    f"  Node #{n['node_id']} (GW {n['gateway_mac_address']}): "
                    f"Status={n['status'].upper()}, "
                    f"Tilt=({n['tilt_x']}°, {n['tilt_y']}°), "
                    f"Vib={n['vibration_magnitude']}g, "
                    f"Batt={n['battery_percentage']}%, "
                    f"Placed={n['placed']}"
                )
            assert len(nodes) >= 6

        print("\n>>> End-to-end verification SUCCEEDED! FastAPI + PostgreSQL + Simulator in sync. <<<\n")

    finally:
        print("Stopping server process...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
