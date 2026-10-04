from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ImportInfo:
    type: str
    module: Optional[str]
    symbol: Optional[str]
    alias: Optional[str]
    level: int = 0
    line: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "module": self.module,
            "symbol": self.symbol,
            "alias": self.alias,
            "level": self.level,
            "line": self.line,
        }


@dataclass
class Symbol:
    id: str
    name: str
    qualified_name: str
    symbol_type: str
    file: str
    module: str
    line: int
    end_line: int
    parent: Optional[str] = None
    scope: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "id": self.id,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "symbol_type": self.symbol_type,
            "file": self.file,
            "module": self.module,
            "line": self.line,
            "end_line": self.end_line,
            "parent": self.parent,
            "scope": self.scope,
        }
        payload.update(self.details)
        return payload


@dataclass
class FunctionInfo:
    name: str
    qualified_name: str
    line: int
    end_line: int
    parameters: List[str] = field(default_factory=list)
    return_annotation: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    is_async: bool = False
    containing_class: Optional[str] = None
    calls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "line": self.line,
            "end_line": self.end_line,
            "parameters": self.parameters,
            "return_annotation": self.return_annotation,
            "decorators": self.decorators,
            "is_async": self.is_async,
            "containing_class": self.containing_class,
            "calls": self.calls,
        }


@dataclass
class ClassInfo:
    name: str
    qualified_name: str
    line: int
    end_line: int
    bases: List[str] = field(default_factory=list)
    decorators: List[str] = field(default_factory=list)
    methods: List[FunctionInfo] = field(default_factory=list)
    is_nested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "line": self.line,
            "end_line": self.end_line,
            "bases": self.bases,
            "decorators": self.decorators,
            "methods": [method.to_dict() for method in self.methods],
            "is_nested": self.is_nested,
        }


@dataclass
class FileInfo:
    path: str
    module_name: str
    line_count: int
    language: str = "python"
    is_test_file: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "module_name": self.module_name,
            "line_count": self.line_count,
            "language": self.language,
            "is_test_file": self.is_test_file,
        }


@dataclass
class Relationship:
    source: str
    target: str
    type: str
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    confidence: str = "certain"
    resolution_status: str = "unknown"
    resolved_to: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "source": self.source,
            "target": self.target,
            "type": self.type,
            "source_file": self.source_file,
            "source_line": self.source_line,
            "confidence": self.confidence,
            "resolution_status": self.resolution_status,
            "resolved_to": self.resolved_to,
        }
        payload.update(self.details)
        return payload


@dataclass
class ModuleInfo:
    name: str
    file: str
    is_test_file: bool = False
    imports: List[ImportInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    calls: List[Dict[str, Any]] = field(default_factory=list)
    assignments: List[Dict[str, Any]] = field(default_factory=list)
    decorators: List[Dict[str, Any]] = field(default_factory=list)
    configuration_references: List[Dict[str, Any]] = field(default_factory=list)
    database_references: List[Dict[str, Any]] = field(default_factory=list)
    symbols: List[Symbol] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "file": self.file,
            "is_test_file": self.is_test_file,
            "imports": [entry.to_dict() for entry in self.imports],
            "classes": [entry.to_dict() for entry in self.classes],
            "functions": [entry.to_dict() for entry in self.functions],
            "calls": self.calls,
            "assignments": self.assignments,
            "decorators": self.decorators,
            "configuration_references": self.configuration_references,
            "database_references": self.database_references,
            "symbols": [entry.to_dict() for entry in self.symbols],
            "relationships": [entry.to_dict() for entry in self.relationships],
            "warnings": self.warnings,
        }


@dataclass
class RepositoryModel:
    root: str
    files: List[FileInfo] = field(default_factory=list)
    modules: List[ModuleInfo] = field(default_factory=list)
    symbols: List[Symbol] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": {
                "root": self.root,
                "file_count": len(self.files),
                "module_count": len(self.modules),
            },
            "files": [entry.to_dict() for entry in self.files],
            "modules": [entry.to_dict() for entry in self.modules],
            "symbols": [entry.to_dict() for entry in self.symbols],
            "relationships": [entry.to_dict() for entry in self.relationships],
            "errors": self.errors,
            "warnings": self.warnings,
            "unresolved": self.unresolved,
            "nodes": [
                {
                    "id": symbol.qualified_name,
                    "type": symbol.symbol_type,
                    "file": symbol.file,
                    "line": symbol.line,
                }
                for symbol in self.symbols
            ] + self._reference_nodes(),
            "edges": [
                {
                    "source": relation.source,
                    "target": relation.target,
                    "type": relation.type,
                    "source_file": relation.source_file,
                    "source_line": relation.source_line,
                    "confidence": relation.confidence,
                    "resolution_status": relation.resolution_status,
                    "resolved_to": relation.resolved_to,
                    **relation.details,
                }
                for relation in self.relationships
            ],
        }

    def _reference_nodes(self) -> List[Dict[str, Any]]:
        nodes: Dict[str, Dict[str, Any]] = {}
        for module in self.modules:
            for reference in module.configuration_references:
                name = reference["name"]
                node_id = f"config.{name}"
                nodes[node_id] = {
                    "id": node_id,
                    "type": "configuration",
                    "name": name,
                    "sensitive": reference.get("sensitive", False),
                }
            for reference in module.database_references:
                table = reference.get("table")
                if table:
                    node_id = f"db.table.{table}"
                    nodes[node_id] = {
                        "id": node_id,
                        "type": "database_table",
                        "name": table,
                    }
        return [nodes[key] for key in sorted(nodes)]
