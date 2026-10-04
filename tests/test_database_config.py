import json

from codebase_intelligence.repository import Repository


def test_environment_variables_and_config_keys_are_extracted(tmp_path):
    (tmp_path / "config.py").write_text(
        "import os\n"
        "DATABASE_URL = os.getenv('DATABASE_URL')\n"
        "DEBUG = os.environ['DEBUG']\n"
        "CACHE_URL = os.environ.get('CACHE_URL')\n"
        "def read_config(app, config):\n"
        "    app.config['SQLALCHEMY_DATABASE_URI']\n"
        "    app.config.get('SESSION_COOKIE_SECURE')\n"
        "    config.get('FEATURE_FLAG')\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()
    references = [
        reference
        for module in result["modules"]
        for reference in module["configuration_references"]
    ]
    names = {reference["name"] for reference in references}

    assert {
        "DATABASE_URL",
        "DEBUG",
        "CACHE_URL",
        "SQLALCHEMY_DATABASE_URI",
        "SESSION_COOKIE_SECURE",
        "FEATURE_FLAG",
    } <= names
    assert any(
        reference["name"] == "DATABASE_URL"
        and reference["type"] == "environment_variable"
        for reference in references
    )
    assert any(
        reference["name"] == "CACHE_URL"
        and reference["access_type"] == "os.environ.get"
        for reference in references
    )
    assert any(
        edge["type"] == "reads_config" and edge["target"] == "config.FEATURE_FLAG"
        for edge in result["edges"]
    )
    assert any(
        edge["type"] == "writes_config" and edge["target"] == "config.DEBUG"
        for edge in result["edges"]
    )


def test_sql_operations_extract_table_relationships(tmp_path):
    (tmp_path / "repository.py").write_text(
        "def get_users(cursor):\n"
        "    cursor.execute('SELECT * FROM users JOIN teams ON users.team_id = teams.id')\n"
        "def create_user(cursor):\n"
        "    cursor.execute('INSERT INTO users VALUES (...)')\n"
        "def update_user(cursor):\n"
        "    cursor.execute('UPDATE users SET name = ?')\n"
        "def delete_user(cursor):\n"
        "    cursor.execute('DELETE FROM users WHERE id = ?')\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()
    edges = result["edges"]

    assert any(
        edge["source"] == "repository.get_users"
        and edge["target"] == "db.table.users"
        and edge["type"] == "reads_table"
        for edge in edges
    )
    assert any(
        edge["source"] == "repository.get_users"
        and edge["target"] == "db.table.teams"
        and edge["type"] == "reads_table"
        for edge in edges
    )
    for function_name in ("create_user", "update_user", "delete_user"):
        assert any(
            edge["source"] == f"repository.{function_name}"
            and edge["target"] == "db.table.users"
            and edge["type"] == "writes_table"
            for edge in edges
        )
    assert any(
        node["id"] == "db.table.users" and node["type"] == "database_table"
        for node in result["nodes"]
    )


def test_sql_assigned_to_variable_is_recognized_without_serializing_query(tmp_path):
    (tmp_path / "repository.py").write_text(
        "def list_users(cursor):\n"
        "    query = 'SELECT * FROM users'\n"
        "    cursor.execute(query)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()
    serialized = json.dumps(result)

    assert any(
        edge["type"] == "reads_table" and edge["target"] == "db.table.users"
        for edge in result["edges"]
    )
    assert "SELECT * FROM users" not in serialized


def test_unknown_execute_is_not_assumed_to_be_database_access(tmp_path):
    (tmp_path / "module.py").write_text(
        "def process(thing, data):\n"
        "    thing.execute(data)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert not any(
        edge["type"] in {"queries", "reads_table", "writes_table"}
        for edge in result["edges"]
    )


def test_sensitive_configuration_values_are_not_serialized(tmp_path):
    secret = "postgresql://alice:supersecret@example.test/app"
    (tmp_path / "settings.py").write_text(
        f"DATABASE_URL = {secret!r}\n"
        "SECRET_KEY = 'my-secret-key-value'\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()
    serialized = json.dumps(result)

    assert "supersecret" not in serialized
    assert "alice" not in serialized
    assert "my-secret-key-value" not in serialized
    assignments = result["modules"][0]["assignments"]
    assert all(assignment["value"] == "[redacted]" for assignment in assignments)


def test_sql_cross_file_call_chain_is_present(tmp_path):
    (tmp_path / "repository.py").write_text(
        "def get_users(cursor):\n"
        "    cursor.execute('SELECT * FROM users')\n",
        encoding="utf-8",
    )
    (tmp_path / "service.py").write_text(
        "from repository import get_users\n"
        "def list_users(cursor):\n"
        "    return get_users(cursor)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()
    edges = result["edges"]

    assert any(
        edge["source"] == "service.list_users"
        and edge["target"] == "repository.get_users"
        and edge["type"] == "calls"
        for edge in edges
    )
    assert any(
        edge["source"] == "repository.get_users"
        and edge["target"] == "db.table.users"
        and edge["type"] == "reads_table"
        for edge in edges
    )


def test_sqlalchemy_model_query_does_not_invent_a_table(tmp_path):
    (tmp_path / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (tmp_path / "repository.py").write_text(
        "from models import User\n"
        "def find_users():\n"
        "    return User.query.all()\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()
    orm_edge = next(edge for edge in result["edges"] if edge.get("api") == "sqlalchemy_query")

    assert orm_edge["type"] == "queries"
    assert orm_edge["target"] == "models.User"
    assert orm_edge["confidence"] == "likely"
    assert not any(node["id"] == "db.table.User" for node in result["nodes"])


def test_sqlalchemy_session_add_and_delete_refer_to_resolved_model(tmp_path):
    (tmp_path / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (tmp_path / "repository.py").write_text(
        "from models import User\n"
        "def create_user(session):\n"
        "    user = User()\n"
        "    session.add(user)\n"
        "def remove_user(session):\n"
        "    user = User()\n"
        "    session.delete(user)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["source"] == "repository.create_user"
        and edge["target"] == "models.User"
        and edge["type"] == "writes"
        and edge["confidence"] == "likely"
        for edge in result["edges"]
    )
    assert any(
        edge["source"] == "repository.remove_user"
        and edge["target"] == "models.User"
        and edge["type"] == "deletes"
        and edge["confidence"] == "likely"
        for edge in result["edges"]
    )
    assert not any(node["id"] == "db.table.User" for node in result["nodes"])


def test_invalid_file_does_not_prevent_database_analysis(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "db.py").write_text(
        "def get_users(cursor):\n    cursor.execute('SELECT * FROM users')\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(error["error_type"] == "SyntaxError" for error in result["errors"])
    assert any(edge["type"] == "reads_table" for edge in result["edges"])
