from codebase_intelligence.repository import Repository


def test_repository_discovers_and_analyzes_files(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        "def validate():\n    return True\n\n\ndef authenticate():\n    return validate()\n",
        encoding="utf-8",
    )

    repo = Repository(root=tmp_path)
    model = repo.analyze()

    assert len(model.files) == 2
    assert any(module.name == "package.service" for module in model.modules)
    assert any(edge["type"] == "calls" for edge in model.to_dict()["edges"])
