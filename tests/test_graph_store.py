import json
import sqlite3

import pytest

from codebase_intelligence.graph_store import GraphStore
from codebase_intelligence.repository import Repository


def make_graph_repository(root):
    (root / "validators.py").write_text(
        "def validate():\n    return True\n",
        encoding="utf-8",
    )
    (root / "repository.py").write_text(
        "import os\n"
        "DATABASE_URL = os.getenv('DATABASE_URL')\n"
        "def get_users(cursor):\n"
        "    cursor.execute('SELECT * FROM users')\n",
        encoding="utf-8",
    )
    (root / "app.py").write_text(
        "from validators import validate\n"
        "from repository import get_users\n"
        "def authenticate(cursor):\n"
        "    validate()\n"
        "    return get_users(cursor)\n",
        encoding="utf-8",
    )
    (root / "test_app.py").write_text(
        "from app import authenticate\n"
        "def test_authenticate():\n"
        "    authenticate(None)\n",
        encoding="utf-8",
    )
    return Repository(root).analyze()


def test_database_is_created_and_repository_snapshot_stored(tmp_path):
    repository_root = tmp_path / "project"
    repository_root.mkdir()
    model = make_graph_repository(repository_root)
    database = tmp_path / "graph.db"

    with GraphStore(database) as store:
        store.store_repository(model)
        summary = store.get_summary(repository_root)
        assert store.get_file(repository_root, "app.py")["module_name"] == "app"
        assert store.get_module(repository_root, "app")["name"] == "app"

    assert database.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] >= 4
    assert summary["files"] == 4
    assert summary["modules"] == 4
    assert summary["tests"] == 1
    assert summary["database_objects"] == 1
    assert summary["config_references"] >= 2


def test_symbol_relationship_caller_and_callee_queries(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    model = make_graph_repository(project)

    with GraphStore(tmp_path / "graph.db") as store:
        store.store_repository(model)

        symbol = store.get_symbol(project, "app.authenticate")
        assert symbol["qualified_name"] == "app.authenticate"
        assert symbol["file"] == "app.py"
        assert any(
            edge["type"] == "calls" and edge["target"] == "validators.validate"
            for edge in store.get_callees(project, "app.authenticate")
        )
        assert any(
            edge["source"] == "app.authenticate"
            for edge in store.get_callers(project, "validators.validate")
        )
        assert any(
            edge["type"] == "calls"
            for edge in store.get_relationships(project, "app.authenticate")
        )
        assert store.get_imports(project, "app.authenticate")[0]["source_module"] == "validators"


def test_tests_config_and_database_queries(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    model = make_graph_repository(project)

    with GraphStore(tmp_path / "graph.db") as store:
        store.store_repository(model)

        test_edges = store.get_tests_for(project, "app.authenticate")
        assert test_edges[0]["source"] == "test_app.test_authenticate"
        assert store.get_config_references(project, "repository.get_users")
        database_edges = store.get_database_relationships(project, "repository.get_users")
        assert any(edge["type"] == "reads_table" for edge in database_edges)


def test_search_module_and_limit_queries_are_bounded(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    model = make_graph_repository(project)

    with GraphStore(tmp_path / "graph.db") as store:
        store.store_repository(model)

        assert len(store.search_symbols(project, "", limit=2)) == 2
        assert store.search_relationships(project, "reads_table", limit=1)[0]["target"] == "db.table.users"
        assert store.get_module(project, "app", limit=1)["symbols"][0]["qualified_name"] == "app.authenticate"
        with pytest.raises(ValueError):
            store.search_symbols(project, "authenticate", limit=0)


def test_neighborhood_expands_by_depth_and_stays_local(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    model = make_graph_repository(project)

    with GraphStore(tmp_path / "graph.db") as store:
        store.store_repository(model)

        first = store.get_neighborhood(project, "app.authenticate", depth=1)
        second = store.get_neighborhood(project, "app.authenticate", depth=2)

    first_ids = {node["id"] for node in first["nodes"]}
    second_ids = {node["id"] for node in second["nodes"]}
    assert "app.authenticate" in first_ids
    assert "repository.get_users" in first_ids
    assert "db.table.users" not in first_ids
    assert "db.table.users" in second_ids
    assert len(second["nodes"]) < 10
    with GraphStore(tmp_path / "graph.db") as store:
        with pytest.raises(ValueError):
            store.get_neighborhood(project, "app.authenticate", depth=4)


def test_store_replaces_repository_snapshot_without_duplicates(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    model = make_graph_repository(project)

    with GraphStore(tmp_path / "graph.db") as store:
        store.store_repository(model)
        first_summary = store.get_summary(project)
        store.store_repository(model)
        second_summary = store.get_summary(project)

    assert first_summary == second_summary


def test_cli_can_analyze_to_database_and_query_summary(tmp_path, capsys):
    from codebase_intelligence.cli import main

    project = tmp_path / "project"
    project.mkdir()
    make_graph_repository(project)
    database = tmp_path / "graph.db"

    assert main([str(project), "--database", str(database)]) == 0
    capsys.readouterr()
    assert main(["--database", str(database), "--summary"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["symbols"] >= 4

    assert main([
        "--database",
        str(database),
        "--neighbors",
        "app.authenticate",
        "--depth",
        "2",
    ]) == 0
    neighborhood = json.loads(capsys.readouterr().out)
    assert "db.table.users" in {node["id"] for node in neighborhood["nodes"]}
    assert main([
        "--database",
        str(database),
        "--relationship-type",
        "reads_table",
    ]) == 0
    matching_edges = json.loads(capsys.readouterr().out)
    assert all(edge["type"] == "reads_table" for edge in matching_edges)


def test_json_export_remains_available(tmp_path):
    from codebase_intelligence.cli import main

    project = tmp_path / "project"
    project.mkdir()
    make_graph_repository(project)
    output = tmp_path / "graph.json"

    assert main([str(project), "--output", str(output), "--pretty"]) == 0
    exported = json.loads(output.read_text(encoding="utf-8"))
    assert exported["repository"]["file_count"] == 4
