from pathlib import Path

from codebase_intelligence.parser import parse_file


def test_parse_file_success(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text("def greet(name):\n    return name\n", encoding="utf-8")

    tree, metadata = parse_file(source)

    assert tree is not None
    assert metadata["error"] is None
    assert metadata["line_count"] == 3


def test_parse_file_syntax_error(tmp_path):
    broken = tmp_path / "broken.py"
    broken.write_text("def bad(:\n    pass\n", encoding="utf-8")

    tree, metadata = parse_file(broken)

    assert tree is None
    assert metadata["error"] is not None
