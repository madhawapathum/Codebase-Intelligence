"""Tests for the viewer's read-only query/API integration.

These tests build a tiny repository graph in SQLite and exercise the
``ViewerAPI`` facade (and the HTTP server) to confirm the viewer only reads
bounded neighborhoods through the existing ``GraphStore`` query layer.
"""

import json
import threading
import urllib.request

import pytest

from codebase_intelligence.graph_store import GraphStore
from codebase_intelligence.repository import Repository
from visualizer.server import ViewerAPI, build_server


def _make_db(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "models.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n"
        "class Base(DeclarativeBase):\n"
        "    pass\n"
        "class User(Base):\n"
        "    __tablename__ = 'users'\n",
        encoding="utf-8",
    )
    (project / "app.py").write_text(
        "from models import User\n"
        "def create_user():\n"
        "    return User()\n",
        encoding="utf-8",
    )
    model = Repository(project).analyze()
    database = tmp_path / "graph.db"
    with GraphStore(database) as store:
        store.store_repository(model)
    return str(project), str(database)


def test_repositories_and_summary(tmp_path):
    root, database = _make_db(tmp_path)
    api = ViewerAPI(database)

    assert api.repositories() == [root]
    summary = api.summary(root)
    assert summary["symbols"] >= 2
    assert summary["database_objects"] == 1


def test_graph_returns_bounded_neighborhood(tmp_path):
    root, database = _make_db(tmp_path)
    api = ViewerAPI(database)

    graph = api.graph("models.User", root=root, depth=1)
    ids = {node["id"] for node in graph["nodes"]}

    assert graph["repository"] == root
    assert "models.User" in ids
    assert "db.table.users" in ids
    assert any(
        relationship["type"] == "defines_table"
        and relationship["source"] == "models.User"
        and relationship["target"] == "db.table.users"
        for relationship in graph["relationships"]
    )


def test_graph_validates_repository_root(tmp_path):
    _, database = _make_db(tmp_path)
    api = ViewerAPI(database)

    with pytest.raises(LookupError):
        api.graph("models.User", root="C:/does/not/exist", depth=1)


def test_graph_validates_unknown_symbol(tmp_path):
    root, database = _make_db(tmp_path)
    api = ViewerAPI(database)

    with pytest.raises(LookupError):
        api.graph("models.DoesNotExist", root=root, depth=1)


def test_graph_bounds_depth_and_limit(tmp_path):
    root, database = _make_db(tmp_path)
    api = ViewerAPI(database)

    with pytest.raises(ValueError):
        api.graph("models.User", root=root, depth=99)
    with pytest.raises(ValueError):
        api.graph("models.User", root=root, depth=1, limit=0)


def test_http_api_serves_graph(tmp_path):
    root, database = _make_db(tmp_path)
    server = build_server("127.0.0.1", 0, database)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/repositories"
        ) as response:
            payload = json.loads(response.read())
        assert payload["repositories"] == [root]

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/graph?symbol=models.User&depth=1"
        ) as response:
            graph = json.loads(response.read())
        assert any(node["id"] == "db.table.users" for node in graph["nodes"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
