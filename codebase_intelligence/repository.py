from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .models import FileInfo, ModuleInfo, Relationship, RepositoryModel
from .parser import parse_file
from .resolver import analyze_repository
from .visitor import ModuleVisitor


DEFAULT_EXCLUDES = {".git", ".venv", "venv", "__pycache__", "node_modules", "dist", "build"}


def module_name_from_path(root: Path, file_path: Path) -> str:
    relative = file_path.relative_to(root)
    parts = list(relative.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts:
        return file_path.stem
    return ".".join(parts)


def is_test_file_path(file_path: Path) -> bool:
    return file_path.name.startswith("test_") and file_path.suffix == ".py" or (
        file_path.name.endswith("_test.py")
    )


def has_test_structure(tree: ast.AST) -> bool:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            return True
        if isinstance(node, ast.ClassDef):
            has_test_method = any(
                isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef))
                and member.name.startswith("test_")
                for member in node.body
            )
            inherits_test_case = any(
                ast.unparse(base).endswith("TestCase") for base in node.bases
            )
            if has_test_method and (node.name.startswith("Test") or inherits_test_case):
                return True
    return False


class Repository:
    """Discover and analyze a repository."""

    def __init__(self, root: str | Path, exclude: Optional[Set[str]] = None):
        self.root = Path(root)
        self.exclude = set(DEFAULT_EXCLUDES)
        if exclude:
            self.exclude.update(exclude)
        self.model = RepositoryModel(root=str(self.root))

    def discover_python_files(self) -> List[Path]:
        files: List[Path] = []
        for path in self.root.rglob("*.py"):
            if any(part in self.exclude for part in path.parts):
                continue
            files.append(path)
        return sorted(files)

    def _record_parse_error(self, file_path: Path, metadata: Dict[str, Any]) -> None:
        error_data = metadata.get("error")
        if isinstance(error_data, dict):
            error = {
                "file": str(file_path),
                "error_type": error_data.get("type"),
                "message": error_data.get("message"),
                "line": error_data.get("line"),
                "offset": error_data.get("offset"),
            }
        else:
            error = {"file": str(file_path), "error_type": str(error_data), "message": str(error_data)}
        self.model.warnings.append(error)
        self.model.errors.append(error)

    def analyze(self) -> RepositoryModel:
        self.model.files = []
        self.model.modules = []
        self.model.relationships = []
        self.model.warnings = []
        self.model.errors = []
        self.model.unresolved = []

        for path in self.discover_python_files():
            tree, metadata = parse_file(path)
            if tree is None:
                self._record_parse_error(path, metadata)
                continue

            module_name = module_name_from_path(self.root, path)
            test_file = is_test_file_path(path) or has_test_structure(tree)
            visitor = ModuleVisitor(str(path), module_name, is_test_file=test_file)
            visitor.visit(tree)
            module = ModuleInfo(
                name=module_name,
                file=str(path.relative_to(self.root)),
                is_test_file=test_file,
                imports=visitor.imports,
                classes=[],
                functions=[],
                calls=visitor.calls,
                assignments=visitor.assignments,
                decorators=visitor.decorators,
                configuration_references=visitor.configuration_references,
                database_references=visitor.database_references,
            )
            for reference in module.configuration_references + module.database_references:
                reference["source_file"] = module.file
            for class_info in visitor.classes:
                module.classes.append(class_info)
            for function in visitor.functions:
                module.functions.append(function)
            module.relationships = [
                Relationship(
                    source=item["source"],
                    target=item["target"],
                    type=item["type"],
                    source_file=str(path.relative_to(self.root)),
                    source_line=item.get("line"),
                    resolution_status=item.get("resolution_status", "unknown"),
                )
                for item in visitor.relationships
            ]
            module.symbols = []
            self.model.modules.append(module)
            self.model.files.append(
                FileInfo(
                    path=str(path.relative_to(self.root)),
                    module_name=module_name,
                    line_count=metadata.get("line_count", 0),
                    is_test_file=test_file,
                )
            )

        self.model = analyze_repository(self.model)
        return self.model

    def to_dict(self) -> Dict[str, Any]:
        return self.model.to_dict()


def build_repository(root: str | Path, exclude: Optional[Set[str]] = None) -> RepositoryModel:
    repo = Repository(root=root, exclude=exclude)
    return repo.analyze()
