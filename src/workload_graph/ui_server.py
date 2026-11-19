"""Local page and JSON API for multi-account runs with a review gate.

Status: partial
Purpose: serve the black-and-white page, start runs, stream their progress by long poll, and
    record the TAM's approve or reject decision per workload.
Inputs: HTTP on 127.0.0.1. Account ids come from the request and are never written to the project.
Outputs: the page; JSON for ``POST /api/jobs``, ``GET /api/jobs/<id>``,
    ``POST /api/jobs/<id>/review``, ``GET /api/runs``; and the legacy ``POST /api/build``.
Planned dependencies: runs, jobs, live.
Notes:
    - The route functions return ``(status, body)`` and do not touch the socket, so tests call them
      directly.
    - Event details are codes only. Request logging is off so account ids are not written out.
    - Long poll: ``GET /api/jobs/<id>?after=N&wait=S`` returns events numbered above N as soon as
      any exist, or after S seconds (capped at 25). A reader stops when ``settled`` is true.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from botocore.exceptions import BotoCoreError, ClientError

from workload_graph.jobs import Job
from workload_graph.live import build_account
from workload_graph.runs import AmbientPlanner, AssumedRolePlanner, Runs
from workload_graph.runstore import RunStore

_PAGE = Path(__file__).resolve().parent / "ui" / "index.html"
_HOST = "127.0.0.1"
_MAX_BODY = 4096
_MAX_WAIT = 25.0
_JOB_PATH = re.compile(r"^/api/jobs/([0-9a-f]{32})(/review)?$")

Reply = tuple[int, dict[str, Any]]


def _client_error(exc: ClientError) -> Reply:
    code: Any = exc.response.get("Error", {}).get("Code", "ClientError")
    return 502, {"error": str(code)}


def create_job(runs: Runs, payload: object) -> Reply:
    """Start a run. Refusals are 400; AWS errors are 502 with a code only."""
    if not isinstance(payload, dict):
        return 400, {"error": "request body must be an object"}
    accounts = payload.get("accounts")
    if not isinstance(accounts, list):
        return 400, {"error": "accounts must be a list"}
    region = str(payload.get("region", ""))
    try:
        job = runs.start([str(item) for item in accounts], region)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except ClientError as exc:
        return _client_error(exc)
    except BotoCoreError:
        return 502, {"error": "the account could not be read"}
    return 202, {"job_id": job.job_id, "accounts": list(job.accounts), "state": job.state.value}


def _event_view(event: Any) -> dict[str, Any]:
    return {"seq": event.seq, "at": event.at.isoformat(), "kind": event.kind,
            "account_id": event.account_id, "detail": event.detail}


def _job_view(job: Job, events: list[Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "job_id": job.job_id,
        "accounts": list(job.accounts),
        "state": job.state.value,
        "settled": job.is_settled,
        "events": [_event_view(event) for event in events],
        "decisions": job.decisions,
    }
    result = job.result
    if result is not None:
        body["result"] = json.loads(result.to_json())
    return body


def poll_job(runs: Runs, job_id: str, after: int, wait: float) -> Reply:
    """Events numbered above ``after``, waiting up to ``wait`` seconds for the first one."""
    job = runs.get(job_id)
    if job is None:
        return 404, {"error": "no such run"}
    timeout = min(max(wait, 0.0), _MAX_WAIT)
    events = job.wait_for_events(max(after, 0), timeout)
    return 200, _job_view(job, events)


def review_workload(runs: Runs, job_id: str, payload: object) -> Reply:
    """Record approve or reject for one workload of a run that has settled."""
    job = runs.get(job_id)
    if job is None:
        return 404, {"error": "no such run"}
    if not isinstance(payload, dict):
        return 400, {"error": "request body must be an object"}
    try:
        runs.decide(job_id, str(payload.get("workload_id", "")),
                    str(payload.get("decision", "")))
    except ValueError as exc:
        return 400, {"error": str(exc)}
    return 200, {"decisions": job.decisions}


def list_runs(runs: Runs) -> Reply:
    """Summaries of every known run, newest first."""
    return 200, {"runs": runs.list()}


class _Handler(BaseHTTPRequestHandler):
    runs: Runs | None = None

    def log_message(self, fmt: str, *args: object) -> None:
        """Skip request logging so account ids from the query are not written out."""

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        if parts.path == "/":
            body = _PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if parts.path == "/api/runs" and self.runs is not None:
            self._json(*list_runs(self.runs))
            return
        match = _JOB_PATH.match(parts.path)
        if match is None or match.group(2) or self.runs is None:
            self.send_error(404)
            return
        query = parse_qs(parts.query)
        try:
            after = int(query.get("after", ["0"])[0])
            wait = float(query.get("wait", ["0"])[0])
        except ValueError:
            self._json(400, {"error": "after must be a whole number and wait a number"})
            return
        self._json(*poll_job(self.runs, match.group(1), after, wait))

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if self.runs is None:
            self.send_error(404)
            return
        if path == "/api/build":
            self._legacy_build()
            return
        if path == "/api/jobs":
            payload = self._read_json()
            if payload is None:
                return
            self._json(*create_job(self.runs, payload))
            return
        match = _JOB_PATH.match(path)
        if match is None or not match.group(2):
            self.send_error(404)
            return
        payload = self._read_json()
        if payload is None:
            return
        self._json(*review_workload(self.runs, match.group(1), payload))

    def _read_json(self) -> object | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > _MAX_BODY:
            self._json(400, {"error": "request is too large"})
            return None
        try:
            payload: object = json.loads(self.rfile.read(length) or b"{}")
            return payload
        except json.JSONDecodeError:
            self._json(400, {"error": "request body is not JSON"})
            return None

    def _legacy_build(self) -> None:
        payload = self._read_json()
        if not isinstance(payload, dict):
            if payload is not None:
                self._json(400, {"error": "request body must be an object"})
            return
        try:
            result = build_account(str(payload["account_id"]), str(payload["region"]))
        except (KeyError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except ClientError as exc:
            self._json(*_client_error(exc))
            return
        except BotoCoreError:
            self._json(502, {"error": "the account could not be read"})
            return
        self._json(200, json.loads(result.to_json()))

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8765, external_id: str | None = None,
          runs_dir: Path = Path("out/runs")) -> None:
    """Serve the page on localhost until stopped.

    Without ``external_id`` the server reads one account with the ambient credential (test pad).
    With it, runs may include up to 20 accounts through ``TamReadOnly`` (deployed mode).
    """
    planner = AssumedRolePlanner(external_id) if external_id else AmbientPlanner()
    runs = Runs(planner, store=RunStore(runs_dir))
    handler = type("Handler", (_Handler,), {"runs": runs})
    server = ThreadingHTTPServer((_HOST, port), handler)
    print(f"open http://{_HOST}:{port}", flush=True)
    server.serve_forever()
