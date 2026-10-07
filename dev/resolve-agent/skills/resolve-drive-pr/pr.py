"""Ask the host to create a checked PR, or edit/review an existing PR.

Run from the fix checkout, after committing and pushing the candidate:
  python <skill-dir>/pr.py create --title 'Fix X' --body-file .omnigent/pr-body.md --base main
The result includes the PR branch. Use it for subsequent pushes and CI fixes.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import subprocess
import urllib.parse
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "edit", "review", "ready"))
    parser.add_argument("--number", type=int)
    parser.add_argument("--title")
    parser.add_argument("--body-file", type=Path)
    parser.add_argument("--base", default="main")
    parser.add_argument("--head")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--event", choices=("REQUEST_CHANGES", "COMMENT"))
    parser.add_argument("--request-id", default=uuid.uuid4().hex)
    args = parser.parse_args()
    data = {"operation": args.operation}
    for field in ("title", "number", "event", "head"):
        if getattr(args, field) is not None:
            data[field] = getattr(args, field)
    if args.body_file:
        data["body"] = args.body_file.read_text()
    if args.operation == "create":

        def git(*argv):
            return subprocess.check_output(["git", *argv], text=True).strip()

        data.update(
            head=git("rev-parse", "HEAD"),
            branch=git("branch", "--show-current"),
            base=args.base,
            draft=args.draft,
            request_id=args.request_id,
        )
        print(f"Publication request: {args.request_id}", flush=True)
    config = json.loads(Path(".omnigent/pr-gate.json").read_text())
    # The gate lives on the host's loopback, outside the sandbox network
    # namespace. Explicit proxy transport must bypass NO_PROXY=localhost.
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    target = urllib.parse.urlsplit(proxy or config["url"])
    connection = http.client.HTTPConnection(
        target.hostname, target.port, timeout=20 * 60
    )
    headers = {
        "Authorization": "Bearer " + config["authorization"],
        "Content-Type": "application/json",
    }
    if proxy and target.username is not None:
        credentials = (
            urllib.parse.unquote(target.username)
            + ":"
            + urllib.parse.unquote(target.password or "")
        )
        headers["Proxy-Authorization"] = (
            "Basic " + base64.b64encode(credentials.encode()).decode()
        )
    try:
        connection.request(
            "POST", config["url"] if proxy else "/pr", json.dumps(data), headers
        )
        response = connection.getresponse()
        raw = response.read()
        try:
            result = json.loads(raw)
        except ValueError:
            raise SystemExit(
                f"PR gate transport failed: HTTP {response.status}"
            ) from None
        if response.status != 200 or result.get("status") == "blocked":
            print(json.dumps(result, indent=2))
            raise SystemExit(1)
    finally:
        connection.close()
    print(json.dumps(result, indent=2))
    if args.operation == "create":
        if result["status"] == "existing":
            print(
                "Recovered an existing PR. Keep your local commits; update it with: "
                f"git push origin HEAD:{result['branch']}"
            )
            return
        print(
            f"Continue on this PR branch: git fetch origin {result['branch']} && "
            f"git checkout --no-track -B {result['branch']} origin/{result['branch']}"
        )


if __name__ == "__main__":
    main()
