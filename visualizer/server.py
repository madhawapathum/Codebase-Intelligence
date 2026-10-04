"""Local viewer server for the Codebase Intelligence graph.

Serves the static viewer (index.html / app.js / style.css) plus a small,
read-only JSON API backed by the existing ``GraphStore`` query layer.

Safety properties
-----------------
* Never executes analyzed source code.
* Never imports the target repository.
* Never connects to the target application's database.
* Never accepts arbitrary SQL (all queries are parameterized GraphStore calls).
* Validates the repository root and bounds depth/limit.
* Only serves the fixed set of static files in this directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from codebase_intelligence.graph_store import (  # noqa: E402
    DEFAULT_LIMIT,
    DEFAULT_MAX_DEPTH,
    MAX_LIMIT,
    GraphStore,
)

VISUALIZER_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = REPO_ROOT / "examples" / "asset_mng_graph.db"

STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


class ViewerAPI:
    """Read-only facade over ``GraphStore`` used by the browser viewer."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)

    def _store(self) -> GraphStore:
        return GraphStore(self.database_path)

    @staticmethod
    def _resolve_root(store: GraphStore, root: str | None) -> str:
        repositories = store.list_repositories()
        if root is None:
            if len(repositories) != 1:
                raise ValueError(
                    "provide 'root' when the database contains zero or multiple repositories"
                )
            return repositories[0]
        if root not in repositories:
            raise LookupError(f"no stored repository at {root}")
        return root

    def repositories(self) -> list:
        with self._store() as store:
            return store.list_repositories()

    def summary(self, root: str | None = None) -> dict:
        with self._store() as store:
            return store.get_summary(self._resolve_root(store, root))

    def symbol(self, name: str, root: str | None = None) -> dict | None:
        with self._store() as store:
            return store.get_symbol(self._resolve_root(store, root), name)

    def search(self, query: str, root: str | None = None, limit: int = DEFAULT_LIMIT) -> list:
        with self._store() as store:
            return store.search_symbols(
                self._resolve_root(store, root), query, limit=limit
            )

    def graph(
        self,
        symbol: str,
        root: str | None = None,
        depth: int = 1,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        if depth < 0 or depth > DEFAULT_MAX_DEPTH:
            raise ValueError(f"depth must be between 0 and {DEFAULT_MAX_DEPTH}")
        if limit < 1 or limit > MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
        with self._store() as store:
            root = self._resolve_root(store, root)
            graph = store.get_neighborhood(root, symbol, depth=depth, limit=limit)
            nodes = graph.get("nodes", [])
            if (
                len(nodes) == 1
                and nodes[0]["id"] == symbol
                and nodes[0].get("type") == "external_or_unresolved"
                and not graph.get("relationships")
            ):
                raise LookupError(f"symbol not found: {symbol}")
            graph["repository"] = root
            return graph


class ViewerRequestHandler(BaseHTTPRequestHandler):
    server_version = "CodebaseIntelligenceViewer/0.1"

    @property
    def api(self) -> ViewerAPI:
        return self.server.api  # type: ignore[attr-defined]

    def _send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)

    def _send_static(self, name: str) -> None:
        if name not in STATIC_FILES:
            self._send_error(404, "not found")
            return
        data = (VISUALIZER_DIR / name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", STATIC_FILES[name])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _param(query: dict, key: str, default: str | None = None) -> str | None:
        values = query.get(key)
        return values[0] if values else default

    @staticmethod
    def _int_param(query: dict, key: str, default: int) -> int:
        raw = ViewerRequestHandler._param(query, key)
        if raw is None or raw == "":
            return default
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"'{key}' must be an integer") from exc

    def log_message(self, fmt, *args) -> None:  # noqa: A002
        sys.stderr.write("[viewer] %s\n" % (fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._send_static("index.html")
                return
            if path.startswith("/api/"):
                self._route_api(path, query)
                return
            self._send_static(path.lstrip("/"))
        except LookupError as exc:
            self._send_error(404, str(exc))
        except ValueError as exc:
            self._send_error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._send_error(500, str(exc))

    def _route_api(self, path: str, query: dict) -> None:
        api = self.api
        if path == "/api/repositories":
            self._send_json({"repositories": api.repositories()})
            return
        if path == "/api/summary":
            self._send_json(api.summary(self._param(query, "root")))
            return
        if path == "/api/symbol":
            name = self._param(query, "name") or ""
            if not name:
                raise ValueError("missing 'name'")
            symbol = api.symbol(name, self._param(query, "root"))
            if symbol is None:
                raise LookupError(f"symbol not found: {name}")
            self._send_json(symbol)
            return
        if path == "/api/search":
            q = self._param(query, "q") or ""
            limit = self._int_param(query, "limit", DEFAULT_LIMIT)
            symbols = api.search(q, self._param(query, "root"), limit=limit)
            self._send_json({"symbols": symbols})
            return
        if path == "/api/graph":
            symbol = self._param(query, "symbol") or ""
            if not symbol:
                raise ValueError("missing 'symbol'")
            depth = self._int_param(query, "depth", 1)
            limit = self._int_param(query, "limit", DEFAULT_LIMIT)
            self._send_json(
                api.graph(symbol, self._param(query, "root"), depth=depth, limit=limit)
            )
            return
        self._send_error(404, "unknown endpoint")


def build_server(host: str, port: int, database_path: str | Path) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), ViewerRequestHandler)
    server.daemon_threads = True
    server.api = ViewerAPI(database_path)  # type: ignore[attr-defined]
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve the Codebase Intelligence 3D graph viewer."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DATABASE),
        help="Path to the SQLite graph database",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind")
    args = parser.parse_args(argv)

    server = build_server(args.host, args.port, args.db)
    print(f"Codebase Intelligence viewer: http://{args.host}:{args.port}")
    print(f"Graph database: {args.db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

