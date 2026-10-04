import json
from pathlib import Path
from typing import Any, Dict


def to_json(data: Dict[str, Any], pretty: bool = False) -> str:
    indent = 2 if pretty else None
    sort_keys = True
    return json.dumps(data, indent=indent, sort_keys=sort_keys)


def write_json(data: Dict[str, Any], output_path: str | Path, pretty: bool = False) -> str:
    output = Path(output_path)
    output.write_text(to_json(data, pretty=pretty), encoding="utf-8")
    return str(output)
