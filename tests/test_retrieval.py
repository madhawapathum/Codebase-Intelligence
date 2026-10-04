"""Tests for the AI-oriented retrieval layer.

These build a small repository, store its graph in SQLite, and verify that
``Retriever`` returns compact, bounded, provenance-bearing context.
"""

from codebase_intelligence.graph_store import GraphStore
from codebase_intelligence.repository import Repository
from codebase_intelligence.retrieval import Retriever, RetrievalLimits


def _make_repository(tmp_path):
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
    return str(project), str(database)


def test_search_returns_relevant_results(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    result = retriever.search("user", root=root)
    names = {item["name"] for item in result["results"]}

    assert "models.User" in names
    assert "db.table.users" in names


def test_get_symbol_returns_metadata(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    symbol = retriever.get_symbol("models.User", root=root)

    assert symbol["qualified_name"] == "models.User"
    assert symbol["type"] == "class"
    assert symbol["module"] == "models"
    assert symbol["file"] == "models.py"
    assert symbol["relationship_counts"]["defines_table"] == 1


def test_get_source_returns_only_symbol_region(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    source = retriever.get_source("app.create_user", root=root)

    assert source["file"] == "app.py"
    assert "def create_user" in source["content"]
    assert "login" not in source["content"]


def test_get_neighborhood_is_bounded(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    graph = retriever.get_neighborhood("models.User", root=root, depth=1)
    ids = {node["id"] for node in graph["nodes"]}

    assert "models.User" in ids
    assert "db.table.users" in ids
    assert any(r["type"] == "defines_table" for r in graph["relationships"])


def test_get_callers_and_callees(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    callers = retriever.get_callers("models.User", root=root)
    assert any(c["source"] == "app.create_user" for c in callers["callers"])

    callees = retriever.get_callees("app.create_user", root=root)
    assert any(c["target"] == "models.User" for c in callees["callees"])


def test_get_tests(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    tests = retriever.get_tests("app.create_user", root=root)
    assert any(t["source"] == "tests.test_app.test_create_user" for t in tests["tests"])


def test_get_database_context(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    database_context = retriever.get_database_context("models.User", root=root)
    assert any(
        d["type"] == "defines_table" and d["target"] == "db.table.users"
        for d in database_context["database"]
    )


def test_get_config_context(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    config = retriever.get_config_context("app.login", root=root)
    assert any(
        c["type"] == "reads_config" and c["target"] == "config.SECRET_KEY"
        for c in config["configuration"]
    )


def test_get_context_is_complete_and_bounded(tmp_path):
    root, database = _make_repository(tmp_path)
    retriever = Retriever(database)

    context = retriever.get_context("app.create_user", root=root)

    for key in ("symbol", "source", "relationships", "callers", "callees", "tests", "database", "configuration"):
        assert key in context
    assert context["symbol"]["qualified_name"] == "app.create_user"
    assert "def create_user" in context["source"]["content"]
    assert any(c["target"] == "models.User" for c in context["callees"])
    assert any(t["source"] == "tests.test_app.test_create_user" for t in context["tests"])


def test_source_respects_line_budget(tmp_path):
    root, database = _make_repository(tmp_path)
    small = Retriever(database, limits=RetrievalLimits(max_source_lines=2))

    source = small.get_source("app.create_user", root=root)

    assert source["truncated"] is True
    assert source["line_count"] <= 2
