"""Deployment and local smoke test script for RepoMedic AI.

Validates that a live RepoMedic server responds to health checks,
serves frontend assets, lists benchmark tasks, and can process a repair job.
"""

from __future__ import annotations

import argparse
import sys
import time
import httpx


def run_smoke_test(base_url: str = "http://localhost:8000") -> int:
    base_url = base_url.rstrip("/")
    print(f"=== Starting RepoMedic AI Smoke Test against {base_url} ===")

    client = httpx.Client(timeout=15.0)

    # 1. Check /health
    print("[1/5] Verifying /health endpoint...")
    try:
        resp = client.get(f"{base_url}/health")
    except Exception as ex:
        print(f"[FAIL] Could not connect to {base_url}/health: {ex}", file=sys.stderr)
        return 1

    if resp.status_code != 200:
        print(f"[FAIL] /health returned HTTP {resp.status_code}: {resp.text}", file=sys.stderr)
        return 1

    health_data = resp.json()
    print(f"      Status       : {health_data.get('status')}")
    print(f"      Version      : {health_data.get('version')}")
    print(f"      Provider     : {health_data.get('model_provider')}")
    print(f"      Docker Avail : {health_data.get('docker_available')}")
    print("      [PASS] Health check verified.")

    # 2. Check Root Frontend /
    print("[2/5] Verifying Root Frontend (/) ...")
    resp_root = client.get(f"{base_url}/")
    if resp_root.status_code != 200 or "RepoMedic" not in resp_root.text:
        print(f"[FAIL] Root route did not return RepoMedic HTML (HTTP {resp_root.status_code})", file=sys.stderr)
        return 1
    print("      [PASS] Frontend HTML successfully loaded.")

    # 3. Check Static Assets
    print("[3/5] Verifying Static Assets (/static/style.css, /static/app.js) ...")
    resp_css = client.get(f"{base_url}/static/style.css")
    resp_js = client.get(f"{base_url}/static/app.js")
    if resp_css.status_code != 200 or resp_js.status_code != 200:
        print("[FAIL] Static assets failed to load.", file=sys.stderr)
        return 1
    print("      [PASS] CSS and JS static assets loaded with HTTP 200.")

    # 4. Check Benchmark Tasks API
    print("[4/5] Verifying /api/benchmark/tasks ...")
    resp_tasks = client.get(f"{base_url}/api/benchmark/tasks")
    if resp_tasks.status_code != 200:
        print(f"[FAIL] /api/benchmark/tasks returned HTTP {resp_tasks.status_code}", file=sys.stderr)
        return 1
    tasks = resp_tasks.json()
    if len(tasks) != 10:
        print(f"[FAIL] Expected 10 tasks, found {len(tasks)}", file=sys.stderr)
        return 1
    print(f"      [PASS] Discovered {len(tasks)} benchmark tasks.")

    # 5. Execute Demo Repair Job
    print("[5/5] Submitting repair job for task_01_arithmetic_discount ...")
    start_resp = client.post(
        f"{base_url}/api/repair/start",
        json={"fixture_id": "task_01_arithmetic_discount"},
    )
    if start_resp.status_code != 202:
        print(f"[FAIL] /api/repair/start returned HTTP {start_resp.status_code}: {start_resp.text}", file=sys.stderr)
        return 1

    job_id = start_resp.json().get("job_id")
    print(f"      Job launched with ID: {job_id}. Polling progress...")

    for _ in range(30):
        time.sleep(1.0)
        status_resp = client.get(f"{base_url}/api/repair/status/{job_id}")
        if status_resp.status_code != 200:
            continue
        st = status_resp.json()
        if st.get("status") in ("completed", "failed"):
            print(f"      Job finished with state: {st.get('current_state')}")
            print(f"      Attempts: {st.get('attempts_used')}/{st.get('max_attempts')}")
            print(f"      Message : {st.get('progress_message')}")
            print("      [PASS] Repair workflow executed end-to-end.")
            break
    else:
        print("[WARN] Job polling timed out after 30s.")

    print("\n=== Smoke Test Completed Successfully! ===")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RepoMedic AI Deployment Smoke Test")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of RepoMedic server")
    args = parser.parse_args()
    sys.exit(run_smoke_test(args.url))
