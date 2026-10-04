from codebase_intelligence.repository import Repository


def test_function_defined_after_caller_resolves(tmp_path):
    file_path = tmp_path / "mod.py"
    file_path.write_text(
        "def a():\n    return b()\n\ndef b():\n    return 42\n",
        encoding="utf-8",
    )
    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "mod.a" and edge["target"] == "mod.b"
        for edge in model.to_dict()["edges"]
    )


def test_cross_file_function_import_resolves(tmp_path):
    (tmp_path / "module_a.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    (tmp_path / "module_b.py").write_text(
        "from module_a import hello\n\ndef run():\n    return hello()\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "module_b.run" and edge["target"] == "module_a.hello"
        for edge in model.to_dict()["edges"]
    )


def test_method_call_using_self_resolves(tmp_path):
    file_path = tmp_path / "service.py"
    file_path.write_text(
        "class UserService:\n    def save(self):\n        return self.validate()\n\n    def validate(self):\n        return True\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "service.UserService.save" and edge["target"] == "service.UserService.validate"
        for edge in model.to_dict()["edges"]
    )


def test_inheritance_relationships_are_recorded(tmp_path):
    file_path = tmp_path / "model.py"
    file_path.write_text(
        "class Base:\n    pass\n\nclass Child(Base):\n    pass\n",
        encoding="utf-8",
    )

    model = Repository(tmp_path).analyze()

    assert any(
        rel["source"] == "model.Child" and rel["target"] == "model.Base" and rel["type"] == "inherits"
        for rel in model.to_dict()["relationships"]
    )


def test_invalid_python_file_does_not_crash_repository(tmp_path):
    (tmp_path / "valid.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def bad(:\n    pass\n", encoding="utf-8")
    model = Repository(tmp_path).analyze()

    assert len(model.modules) == 1
    assert any("broken.py" in item["file"] for item in model.to_dict()["errors"])


def test_unresolved_reference_stays_explicit(tmp_path):
    file_path = tmp_path / "module.py"
    file_path.write_text("def run():\n    return missing()\n", encoding="utf-8")

    model = Repository(tmp_path).analyze()

    assert any(
        edge["source"] == "module.run" and edge["target"] == "missing" and edge["resolution_status"] == "unresolved"
        for edge in model.to_dict()["edges"]
    )
