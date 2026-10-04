from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


DEFAULT_ENCODING = "utf-8"


def parse_file(path: str | Path) -> Tuple[Optional[ast.AST], Dict[str, Any]]:
    """Parse a Python source file and return the AST plus metadata."""
    file_path = Path(path)
    metadata: Dict[str, Any] = {
        "path": str(file_path),
        "module_name": None,
        "line_count": 0,
        "error": None,
    }

    if not file_path.exists():
        metadata["error"] = "FileNotFoundError"
        return None, metadata

    if not file_path.is_file():
        metadata["error"] = "NotAFile"
        return None, metadata

    try:
        source = file_path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        try:
            source = file_path.read_text(encoding=DEFAULT_ENCODING)
        except (UnicodeDecodeError, OSError):
            metadata["error"] = "EncodingError"
            return None, metadata

    metadata["line_count"] = source.count("\n") + (1 if source else 0)

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        metadata["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "line": exc.lineno,
            "offset": exc.offset,
        }
        return None, metadata

    metadata["module_name"] = file_path.stem
    return tree, metadata
