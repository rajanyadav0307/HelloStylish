#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _request_json(method: str, url: str, body: dict | None = None) -> dict:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"

    req = urllib.request.Request(url=url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed for {url}: {exc}") from exc


def _print_step_summary(payload: dict) -> None:
    run = payload.get("run") or {}
    steps = payload.get("steps") or []
    artifacts = payload.get("artifacts") or []

    print(f"RUN STATUS: {run.get('status')}")
    print("STEPS:")
    for step in steps:
        print(f"- {step.get('step_key')}: {step.get('status')}")

    print("ARTIFACTS:")
    for art in artifacts:
        print(f"- {art.get('kind')}")

    style = next((a.get("inline_json") for a in artifacts if a.get("kind") == "style_brief"), None) or {}
    if style:
        print("STYLE_BRIEF:")
        print(f"- analysis_method: {style.get('analysis_method')}")
        print(f"- source: {style.get('source')}")
        if style.get("message"):
            print(f"- message: {style.get('message')}")

    deals = next((a.get("inline_json") for a in artifacts if a.get("kind") == "deals"), None) or {}
    if deals:
        print("DEALS:")
        print(f"- data_mode: {deals.get('data_mode')}")
        print(f"- provider: {deals.get('provider')}")
        print(f"- count: {len(deals.get('deals') or [])}")

    search = next((a.get("inline_json") for a in artifacts if a.get("kind") == "brand_search"), None) or {}
    if search:
        print("BRAND_SEARCH:")
        print(f"- data_mode: {search.get('data_mode')}")
        print(f"- provider: {search.get('provider')}")
        print(f"- candidate_count: {len(search.get('product_candidates') or [])}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real end-to-end test for HelloStylish.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL.")
    parser.add_argument("--email", required=True, help="User email to run test with.")
    parser.add_argument("--folder-id", default="", help="Optional Drive folder id to select before run.")
    parser.add_argument("--folder-name", default="", help="Optional Drive folder name when selecting folder.")
    parser.add_argument("--wait-seconds", type=int, default=180, help="Max seconds to wait for run completion.")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    email = args.email
    email_q = urllib.parse.quote(email)

    health = _request_json("GET", f"{base_url}/health")
    if not health.get("ok"):
        raise RuntimeError("API health check did not return ok=true")
    print("API HEALTH: ok")

    drive_status = _request_json("GET", f"{base_url}/api/drive/status?email={email_q}")
    print(f"DRIVE STATUS: connected={drive_status.get('connected')} selected_folder={bool(drive_status.get('selected_folder'))}")

    if args.folder_id:
        select_payload = {
            "email": email,
            "folder_id": args.folder_id,
            "folder_name": args.folder_name or None,
        }
        selected = _request_json("POST", f"{base_url}/api/drive/folder/select", select_payload)
        print(f"FOLDER SELECTED: {selected.get('folder_id')} ({selected.get('folder_name')})")
        drive_status = _request_json("GET", f"{base_url}/api/drive/status?email={email_q}")

    if not drive_status.get("connected"):
        oauth_start = _request_json("POST", f"{base_url}/api/drive/oauth/start", {"email": email})
        print("ACTION REQUIRED: Complete Google OAuth in your browser, then rerun this command.")
        print(f"OAUTH URL: {oauth_start.get('auth_url')}")
        return 2

    if not drive_status.get("selected_folder"):
        folders = _request_json("GET", f"{base_url}/api/drive/folders?email={email_q}")
        print("ACTION REQUIRED: Select a Drive folder, then rerun.")
        print(f"FOLDER COUNT: {folders.get('count')}")
        for folder in (folders.get("folders") or [])[:10]:
            print(f"- {folder.get('id')} | {folder.get('name')}")
        print(
            "Select command: curl -X POST "
            f"{base_url}/api/drive/folder/select "
            "-H 'content-type: application/json' "
            f"-d '{{\"email\":\"{email}\",\"folder_id\":\"<folder_id>\",\"folder_name\":\"<optional>\"}}'"
        )
        return 3

    photos = _request_json("GET", f"{base_url}/api/drive/photos?email={email_q}&limit=10")
    print(f"PHOTO CHECK: {photos.get('count')} photos visible in selected folder")

    run_resp = _request_json("POST", f"{base_url}/api/runs", {"email": email, "trigger": "manual"})
    run_id = run_resp.get("run_id")
    if not run_id:
        raise RuntimeError("Run creation failed (missing run_id)")
    print(f"RUN CREATED: {run_id}")

    deadline = time.time() + max(15, args.wait_seconds)
    last_status = None
    final_payload: dict | None = None
    while time.time() < deadline:
        payload = _request_json("GET", f"{base_url}/api/runs/{run_id}")
        status = (payload.get("run") or {}).get("status")
        if status != last_status:
            print(f"RUN STATUS: {status}")
            last_status = status
        if status in {"SUCCEEDED", "FAILED", "CANCELED"}:
            final_payload = payload
            break
        time.sleep(1.0)

    if final_payload is None:
        print("ERROR: timed out waiting for terminal run status")
        return 4

    _print_step_summary(final_payload)
    run_status = (final_payload.get("run") or {}).get("status")
    return 0 if run_status == "SUCCEEDED" else 5


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1)
