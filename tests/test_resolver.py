from codebase_intelligence.repository import Repository


def test_cross_file_call_resolution(tmp_path):
    validators = tmp_path / "validators.py"
    validators.write_text("def validate(user):\n    return user\n", encoding="utf-8")

    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "service.py").write_text(
        "from validators import validate\n\n\ndef authenticate(user):\n    return validate(user)\n",
        encoding="utf-8",
    )

    repo = Repository(root=tmp_path)
    model = repo.analyze()

    assert any(
        edge["source"] == "app.service.authenticate" and edge["target"] == "validators.validate"
        for edge in model.to_dict()["edges"]
    )
