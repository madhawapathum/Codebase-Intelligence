from pathlib import Path

from codebase_intelligence.repository import Repository


def test_decorator_relationships_preserve_targets_and_arguments(tmp_path):
    source = tmp_path / "routes.py"
    source.write_text(
        "@app.route('/users', methods=['GET'])\n"
        "@login_required\n"
        "def users():\n"
        "    pass\n\n"
        "@property\n"
        "def name(self):\n"
        "    pass\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()
    edges = model.to_dict()["edges"]

    route_edge = next(
        edge
        for edge in edges
        if edge.get("decorator_expression", "").startswith("app.route")
    )
    assert route_edge["source"] == "routes.users"
    assert route_edge["target"] == "app.route"
    assert route_edge["type"] == "decorated_by"
    assert route_edge["resolution_status"] == "unresolved"
    assert route_edge["arguments"][0] == "'/users'"
    assert "methods=['GET']" in route_edge["arguments"]
    assert any(edge["target"] == "login_required" for edge in edges)
    assert any(edge["target"] == "property" for edge in edges)


def test_imported_decorator_resolves_to_repository_symbol(tmp_path):
    (tmp_path / "decorators.py").write_text(
        "def registered(func):\n    return func\n",
        encoding="utf-8",
    )
    (tmp_path / "routes.py").write_text(
        "from decorators import registered as register\n"
        "@register\n"
        "def users():\n"
        "    pass\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "routes.users"
        and edge["target"] == "decorators.registered"
        and edge["type"] == "decorated_by"
        and edge["resolution_status"] == "resolved"
        for edge in model.to_dict()["edges"]
    )


def test_multiple_and_unresolved_decorators_are_recorded(tmp_path):
    (tmp_path / "module.py").write_text(
        "@decorator_a\n"
        "@something_dynamic\n"
        "def example():\n"
        "    pass\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()
    decorator_edges = [
        edge for edge in model.to_dict()["edges"] if edge["type"] == "decorated_by"
    ]

    assert {edge["target"] for edge in decorator_edges} == {
        "decorator_a",
        "something_dynamic",
    }
    assert all(edge["resolution_status"] == "unresolved" for edge in decorator_edges)


def test_test_file_conventions_and_test_symbols_are_reported(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_auth.py").write_text(
        "def test_login():\n    pass\n\n"
        "def helper():\n    pass\n\n"
        "class TestUser:\n"
        "    def test_create(self):\n"
        "        pass\n"
        "    def helper(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "auth_test.py").write_text(
        "def test_logout():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "application.py").write_text(
        "def normal_function():\n    pass\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze().to_dict()
    file_flags = {item["path"]: item["is_test_file"] for item in model["files"]}
    symbols = {item["qualified_name"]: item for item in model["symbols"]}

    assert file_flags[str(Path("tests") / "test_auth.py")] is True
    assert file_flags["auth_test.py"] is True
    assert file_flags["application.py"] is False
    assert symbols["tests.test_auth.test_login"]["kind"] == "test_function"
    assert symbols["tests.test_auth.TestUser.test_create"]["kind"] == "test_method"
    assert symbols["tests.test_auth.helper"]["kind"] == "function"
    assert symbols["application.normal_function"]["is_test"] is False


def test_unittest_style_test_class_is_discovered_by_structure(tmp_path):
    (tmp_path / "custom_checks.py").write_text(
        "import unittest\n"
        "class AccountChecks(unittest.TestCase):\n"
        "    def test_login(self):\n"
        "        pass\n"
        "    def helper(self):\n"
        "        pass\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze().to_dict()
    file_info = next(item for item in model["files"] if item["path"] == "custom_checks.py")
    symbols = {item["qualified_name"]: item for item in model["symbols"]}

    assert file_info["is_test_file"] is True
    assert symbols["custom_checks.AccountChecks.test_login"]["kind"] == "test_method"
    assert symbols["custom_checks.AccountChecks.helper"]["is_test"] is False


def test_test_function_relationship_to_imported_function(tmp_path):
    (tmp_path / "auth.py").write_text(
        "def login():\n    return True\n",
        encoding="utf-8",
    )
    (tmp_path / "test_auth.py").write_text(
        "from auth import login\n"
        "def test_login():\n"
        "    login()\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()
    edges = model.to_dict()["edges"]

    assert any(
        edge["source"] == "test_auth.test_login"
        and edge["target"] == "auth.login"
        and edge["type"] == "calls"
        for edge in edges
    )
    assert any(
        edge["source"] == "test_auth.test_login"
        and edge["target"] == "auth.login"
        and edge["type"] == "tests"
        and edge["resolution_status"] == "resolved"
        for edge in edges
    )


def test_test_function_relationship_to_imported_class(tmp_path):
    (tmp_path / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_user.py").write_text(
        "from models import User as ImportedUser\n"
        "def test_user():\n"
        "    ImportedUser()\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "test_user.test_user"
        and edge["target"] == "models.User"
        and edge["type"] == "tests"
        for edge in model.to_dict()["edges"]
    )


def test_test_relationship_resolves_imported_module_alias(tmp_path):
    (tmp_path / "models.py").write_text(
        "class User:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_user.py").write_text(
        "import models as model_package\n"
        "def test_user():\n"
        "    model_package.User()\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "test_user.test_user"
        and edge["target"] == "models.User"
        and edge["type"] == "tests"
        for edge in model.to_dict()["edges"]
    )


def test_non_test_call_in_test_file_does_not_create_tests_relationship(tmp_path):
    (tmp_path / "library.py").write_text(
        "def helper():\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_library.py").write_text(
        "from library import helper\n"
        "def setup():\n"
        "    helper()\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert not any(
        edge["type"] == "tests" for edge in model.to_dict()["edges"]
    )


def test_unresolved_import_in_test_is_not_reported_as_tested_code(tmp_path):
    (tmp_path / "test_external.py").write_text(
        "from external_package import unknown_function\n"
        "def test_feature():\n"
        "    unknown_function()\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze().to_dict()

    call_edge = next(edge for edge in model["edges"] if edge["type"] == "calls")
    assert call_edge["target"] == "unknown_function"
    assert call_edge["resolution_status"] == "unresolved"
    assert not any(edge["type"] == "tests" for edge in model["edges"])


def test_invalid_test_file_is_reported_and_other_files_continue(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text(
        "def test_broken(:\n    pass\n",
        encoding="utf-8",
    )
    (tmp_path / "valid.py").write_text(
        "def valid():\n    return True\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze().to_dict()

    assert any(module["name"] == "valid" for module in model["modules"])
    assert any(
        error["file"].endswith("test_broken.py")
        and error["error_type"] == "SyntaxError"
        for error in model["errors"]
    )
