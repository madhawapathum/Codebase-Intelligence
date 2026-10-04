from codebase_intelligence.repository import Repository


def test_declarative_model_with_static_tablename_defines_table(tmp_path):
    (tmp_path / "models.py").write_text(
        "from sqlalchemy.orm import DeclarativeBase\n"
        "class Base(DeclarativeBase):\n"
        "    pass\n"
        "class User(Base):\n"
        "    __tablename__ = 'users'\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["source"] == "models.User"
        and edge["target"] == "db.table.users"
        and edge["type"] == "defines_table"
        for edge in result["edges"]
    )
    assert any(
        node["id"] == "db.table.users" and node["type"] == "database_table"
        for node in result["nodes"]
    )


def test_dynamic_tablename_does_not_produce_table(tmp_path):
    (tmp_path / "models.py").write_text(
        "TABLE_NAME = 'users'\n"
        "class WithVariable:\n"
        "    __tablename__ = TABLE_NAME\n"
        "class WithCall:\n"
        "    __tablename__ = get_table_name()\n"
        "class WithConcat:\n"
        "    __tablename__ = 'prefix_' + 'users'\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert not any(edge["type"] == "defines_table" for edge in result["edges"])
    assert not any(node["id"].startswith("db.table.") for node in result["nodes"])


def test_flask_sqlalchemy_model_with_tablename_defines_table(tmp_path):
    (tmp_path / "models.py").write_text(
        "class User(db.Model):\n"
        "    __tablename__ = 'users'\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["source"] == "models.User"
        and edge["target"] == "db.table.users"
        and edge["type"] == "defines_table"
        for edge in result["edges"]
    )


def test_orm_query_uses_model_argument_not_session(tmp_path):
    (tmp_path / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (tmp_path / "repository.py").write_text(
        "from models import User\n"
        "def find_with_db(db):\n"
        "    return db.query(User)\n"
        "def find_with_session(db_session):\n"
        "    return db_session.query(User)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["type"] == "queries"
        and edge["target"] == "models.User"
        and edge["source"] == "repository.find_with_db"
        for edge in result["edges"]
    )
    assert any(
        edge["type"] == "queries"
        and edge["target"] == "models.User"
        and edge["source"] == "repository.find_with_session"
        for edge in result["edges"]
    )
    assert not any(
        edge["type"] == "queries" and edge["target"] in {"db", "db_session"}
        for edge in result["edges"]
    )


def test_session_writes_recognize_db_variable_names(tmp_path):
    (tmp_path / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (tmp_path / "repository.py").write_text(
        "from sqlalchemy.orm import Session\n"
        "from models import User\n"
        "db = Session()\n"
        "db_session = Session()\n"
        "def create_user():\n"
        "    user = User()\n"
        "    db.add(user)\n"
        "def remove_user():\n"
        "    user = User()\n"
        "    db_session.delete(user)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["source"] == "repository.create_user"
        and edge["target"] == "models.User"
        and edge["type"] == "writes"
        for edge in result["edges"]
    )
    assert any(
        edge["source"] == "repository.remove_user"
        and edge["target"] == "models.User"
        and edge["type"] == "deletes"
        for edge in result["edges"]
    )


def test_with_session_context_marks_session_variable(tmp_path):
    (tmp_path / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (tmp_path / "repository.py").write_text(
        "from sqlalchemy.orm import Session\n"
        "from models import User\n"
        "def seed():\n"
        "    with Session() as db:\n"
        "        user = User()\n"
        "        db.add(user)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["source"] == "repository.seed"
        and edge["target"] == "models.User"
        and edge["type"] == "writes"
        for edge in result["edges"]
    )


def test_add_on_non_session_receiver_is_not_database_activity(tmp_path):
    (tmp_path / "module.py").write_text(
        "def enqueue(queue, item):\n"
        "    queue.add(item)\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert not any(
        edge["type"] in {"writes", "deletes", "updates"} for edge in result["edges"]
    )


def test_text_wrapped_static_sql_is_extracted(tmp_path):
    (tmp_path / "repository.py").write_text(
        "from sqlalchemy import text\n"
        "def get_users(session):\n"
        "    session.execute(text('SELECT * FROM users'))\n",
        encoding="utf-8",
    )

    result = Repository(tmp_path).analyze().to_dict()

    assert any(
        edge["source"] == "repository.get_users"
        and edge["target"] == "db.table.users"
        and edge["type"] == "reads_table"
        for edge in result["edges"]
    )
