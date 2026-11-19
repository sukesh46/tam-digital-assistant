"""Local page for one graph per workload.

Status: partial
Purpose: serve the black-and-white graph page and build from an account id the operator types.
Inputs: HTTP on 127.0.0.1.
Outputs: the page, and a ``BuildResult`` JSON for ``POST /api/build``.
Planned dependencies: live collection.
Notes: the account id is read from the request. It is not written into the project.
    LLM naming is not used.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from workload_graph.live import build_account

_PAGE = Path(__file__).resolve().parent / "ui" / "index.html"
_HOST = "127.0.0.1"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        """Skip request logging so account ids from the query are not written out."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/":
            self.send_error(404)
            return
        body = _PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/build":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > 4096:
            self._json(400, {"error": "request is too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            result = build_account(str(payload["account_id"]), str(payload["region"]))
        except (KeyError, TypeError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            self._json(502, {"error": str(code)})
            return
        except BotoCoreError:
            self._json(502, {"error": "the account could not be read"})
            return
        self._json(200, json.loads(result.to_json()))

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8765) -> None:
    """Serve the page on localhost until the process is stopped."""
    server = ThreadingHTTPServer((_HOST, port), _Handler)
    print(f"open http://{_HOST}:{port}", flush=True)
    server.serve_forever()
