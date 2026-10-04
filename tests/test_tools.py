"""Tests for the AI tool interface (``CodebaseTools``).

These verify that the tool layer is a safe, structured, read-only boundary over
the retrieval layer.
"""

import json

from codebase_intelligence.graph_store import GraphStore
from codebase_intelligence.repository import Repository
from codebase_intelligence.tools import CodebaseTools, execute_tool, get_tool_definitions

EXPECTED_TOOLS = {
    "search",
    "get_symbol",
    "get_source",
    "get_context",
    "get_neighborhood",
    "get_callers",
    "get_callees",
    "get_tests",
    "get_database_context",
    "get_config_context",
}


def _make_tools(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "models.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n"
        "\n"
        "class Base(DeclarativeBase):\n"
        "    pass\n"
        "\n"
        "class User(Base):\n"
        "    __tablename__ = 'users'\n",
        encoding="utf-8",
    )
    (project / "app.py").write_text(
        "import os\n"
        "from models import User\n"
        "\n"
        "def create_user(name):\n"
        "    user = User()\n"
        "    return user\n"
        "\n"
        "def login():\n"
        "    return os.getenv('SECRET_KEY')\n",
        encoding="utf-8",
    )
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import create_user\n"
        "\n"
        "def test_create_user():\n"
        "    create_user()\n",
        encoding="utf-8",
    )

    model = Repository(project).analyze()
    database = tmp_path / "graph.db"
    with GraphStore(database) as store:
        store.store_repository(model)
    return CodebaseTools(str(database)), database


def test_registry_lists_all_tools():
    definitions = get_tool_definitions()
    names = {definition["name"] for definition in definitions}
    assert names == EXPECTED_TOOLS
    for definition in definitions:
        assert "description" in definition
        assert "parameters" in definition


def test_get_symbol_success(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_symbol("models.User")
    assert result["ok"] is True
    assert result["tool"] == "get_symbol"
    assert result["result"]["qualified_name"] == "models.User"
    assert result["result"]["type"] == "class"


def test_get_symbol_unknown(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_symbol("does.not.exist")
    assert result["ok"] is False
    assert result["error"]["type"] == "symbol_not_found"


def test_get_source(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_source("app.create_user")
    assert result["ok"] is True
    assert "def create_user" in result["result"]["content"]
    assert "login" not in result["result"]["content"]


def test_get_context(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_context("app.create_user")
    assert result["ok"] is True
    for key in ("symbol", "source", "relationships", "callers", "callees", "tests", "database", "configuration"):
        assert key in result["result"]


def test_search(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.search("user")
    assert result["ok"] is True
    names = {item["name"] for item in result["result"]["results"]}
    assert "models.User" in names


def test_callers(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_callers("models.User")
    assert result["ok"] is True
    assert any(c["source"] == "app.create_user" for c in result["result"]["callers"])


def test_callees(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_callees("app.create_user")
    assert result["ok"] is True
    assert any(c["target"] == "models.User" for c in result["result"]["callees"])
def test_tests(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_tests("app.create_user")
    assert result["ok"] is True
    assert any(t["source"] == "tests.test_app.test_create_user" for t in result["result"]["tests"])


def test_database_context(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_database_context("models.User")
    assert result["ok"] is True
    assert any(
        d["type"] == "defines_table" and d["target"] == "db.table.users"
        for d in result["result"]["database"]
    )


def test_config_context(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_config_context("app.login")
    assert result["ok"] is True
    assert any(
        c["type"] == "reads_config" and c["target"] == "config.SECRET_KEY"
        for c in result["result"]["configuration"]
    )


def test_neighborhood(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.get_neighborhood("models.User")
    assert result["ok"] is True
    ids = {node["id"] for node in result["result"]["nodes"]}
    assert "db.table.users" in ids


def test_unknown_tool(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = tools.call("does_not_exist", {"name": "x"})
    assert result["ok"] is False
    assert result["error"]["type"] == "unknown_tool"


def test_malformed_arguments(tmp_path):
    tools, _ = _make_tools(tmp_path)
    assert tools.call("get_context", "not-a-dict")["error"]["type"] == "invalid_arguments"
    assert tools.call("get_symbol", {})["error"]["type"] == "invalid_arguments"
    assert tools.search("user", limit=0)["error"]["type"] == "invalid_arguments"
    assert tools.get_neighborhood("models.User", depth=99)["error"]["type"] == "invalid_arguments"


def test_results_are_json_serializable(tmp_path):
    tools, _ = _make_tools(tmp_path)
    calls = [
        ("search", {"query": "user"}),
        ("get_symbol", {"name": "models.User"}),
        ("get_source", {"name": "app.create_user"}),
        ("get_context", {"name": "app.create_user"}),
        ("get_neighborhood", {"name": "models.User", "depth": 1}),
        ("get_callers", {"name": "models.User"}),
        ("get_callees", {"name": "app.create_user"}),
        ("get_tests", {"name": "app.create_user"}),
        ("get_database_context", {"name": "models.User"}),
        ("get_config_context", {"name": "app.login"}),
    ]
    for name, arguments in calls:
        json.dumps(tools.call(name, arguments))  # must not raise


def test_tool_layer_does_not_modify_database(tmp_path):
    tools, database = _make_tools(tmp_path)
    before = database.read_bytes()
    tools.get_context("app.create_user")
    tools.search("user")
    tools.get_symbol("models.User")
    tools.get_database_context("models.User")
    tools.get_neighborhood("models.User", depth=2)
    assert database.read_bytes() == before


def test_execute_tool_function(tmp_path):
    tools, _ = _make_tools(tmp_path)
    result = execute_tool("get_symbol", {"name": "models.User"}, tools)
    assert result["ok"] is True
    assert result["result"]["qualified_name"] == "models.User"

