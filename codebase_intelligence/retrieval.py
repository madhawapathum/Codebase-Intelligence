"""AI-oriented retrieval layer over the Codebase Intelligence SQLite graph.

This module sits *above* ``GraphStore`` and produces compact, provenance-bearing
context packages for an AI agent. It never parses code, never resolves symbols,
never connects to the target database, and never returns the entire graph.

The retrieval layer answers *"what is relevant?"*; it does not reason about or
summarize meaning (that is left to the LLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .graph_store import DEFAULT_MAX_DEPTH, GraphStore

DATABASE_RELATIONSHIP_TYPES = {
    "defines_table",
    "reads_table",
    "writes_table",
    "queries",
    "writes",
    "updates",
    "deletes",
}

CONFIG_RELATIONSHIP_TYPES = {"reads_config", "writes_config"}


@dataclass
class RetrievalLimits:
    """Configurable context budget for AI retrieval."""

    max_source_lines: int = 120
    max_relationships: int = 20
    max_callers: int = 10
    max_callees: int = 20
    max_tests: int = 10
    max_database: int = 20
    max_config: int = 10
    max_neighborhood_nodes: int = 60
    max_search_results: int = 20


class Retriever:
    """Compact, bounded retrieval of graph + source context for an AI agent."""

    def __init__(self, database_path: str | Path, limits: Optional[RetrievalLimits] = None):
        self.database_path = str(database_path)
        self.limits = limits or RetrievalLimits()

    def _store(self) -> GraphStore:
        return GraphStore(self.database_path)

    @staticmethod
    def _resolve_root(store: GraphStore, root: str | None) -> str:
        repositories = store.list_repositories()
        if root is None:
            if len(repositories) != 1:
                raise ValueError(
                    "provide 'root' when the database contains zero or multiple repositories"
                )
            return repositories[0]
        if root not in repositories:
            raise LookupError(f"no stored repository at {root}")
        return root

    @staticmethod
    def _relationship_dict(relationship: Dict[str, Any]) -> Dict[str, Any]:
        """Compact, provenance-bearing representation of a relationship."""
        return {
            "type": relationship.get("type"),
            "source": relationship.get("source"),
            "target": relationship.get("target"),
            "source_file": relationship.get("source_file"),
            "source_line": relationship.get("source_line"),
            "confidence": relationship.get("confidence"),
            "resolution": relationship.get("resolution_status"),
            "resolved_to": relationship.get("resolved_to"),
            "details": relationship.get("details", {}),
        }

    @staticmethod
    def _symbol_summary(symbol: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": symbol.get("name"),
            "qualified_name": symbol.get("qualified_name"),
            "type": symbol.get("symbol_type"),
            "module": symbol.get("module_name"),
            "file": symbol.get("file"),
            "line": symbol.get("line"),
            "end_line": symbol.get("end_line"),
            "parent": symbol.get("parent"),
            "scope": symbol.get("scope"),
            "is_test": bool(symbol.get("is_test")),
            "metadata": symbol.get("metadata") or {},
        }

    def _read_source(self, root: str, file: str, start: int, end: int) -> Optional[str]:
        root_path = Path(root).resolve()
        path = (root_path / file).resolve()
        try:
            if not path.is_relative_to(root_path) or not path.is_file():
                return None
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            return None
        start = max(1, int(start))
        end = max(start, int(end))
        return "\n".join(lines[start - 1 : end])
    # -- Tool 1: search ---------------------------------------------------
    def search(self, query: str, root: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or self.limits.max_search_results
        with self._store() as store:
            root = self._resolve_root(store, root)
            symbols = store.search_symbols(root, query, limit=limit)
            database = store.search_database_objects(root, query, limit=limit)
            config = store.search_config_references(root, query, limit=limit)

        results: List[Dict[str, Any]] = []
        for symbol in symbols:
            results.append({
                "name": symbol["qualified_name"],
                "type": symbol["symbol_type"],
                "module": symbol.get("module_name"),
                "file": symbol.get("file"),
                "line": symbol.get("line"),
                "is_test": bool(symbol.get("is_test")),
            })
        for obj in database:
            results.append({
                "name": obj["id"],
                "type": obj["object_type"],
                "table": obj.get("name"),
            })
        for reference in config:
            results.append({
                "name": f"config.{reference['name']}",
                "type": "configuration",
                "file": reference.get("file"),
                "line": reference.get("line"),
                "config_type": reference.get("config_type"),
            })
        results = results[:limit]
        return {"query": query, "count": len(results), "results": results}

    # -- Tool 2: symbol lookup -------------------------------------------
    def get_symbol(self, name: str, root: Optional[str] = None) -> Dict[str, Any]:
        with self._store() as store:
            root = self._resolve_root(store, root)
            symbol = store.get_symbol(root, name)
            if symbol is None:
                raise LookupError(f"symbol not found: {name}")
            relationships = store.get_relationships(root, name, limit=100)
        counts: Dict[str, int] = {}
        for relationship in relationships:
            rel_type = relationship["type"]
            counts[rel_type] = counts.get(rel_type, 0) + 1
        summary = self._symbol_summary(symbol)
        summary["relationship_counts"] = counts
        return summary

    # -- Tool 3: source retrieval ----------------------------------------
    def get_source(self, symbol: str, root: Optional[str] = None, max_lines: Optional[int] = None) -> Dict[str, Any]:
        max_lines = max_lines or self.limits.max_source_lines
        with self._store() as store:
            root = self._resolve_root(store, root)
            info = store.get_symbol(root, symbol)
            if info is None:
                raise LookupError(f"symbol not found: {symbol}")
        file = info["file"]
        start = int(info["line"])
        requested_end = int(info.get("end_line") or start)
        end = requested_end
        truncated = False
        if end - start + 1 > max_lines:
            end = start + max_lines - 1
            truncated = True
        return {
            "file": file,
            "start_line": start,
            "end_line": end,
            "requested_end_line": requested_end,
            "line_count": end - start + 1,
            "truncated": truncated,
            "content": self._read_source(root, file, start, end),
        }

    # -- Tool 4: neighborhood ---------------------------------------------
    def get_neighborhood(self, symbol: str, root: Optional[str] = None, depth: int = 1, limit: Optional[int] = None) -> Dict[str, Any]:
        if depth < 0 or depth > DEFAULT_MAX_DEPTH:
            raise ValueError(f"depth must be between 0 and {DEFAULT_MAX_DEPTH}")
        limit = limit or self.limits.max_neighborhood_nodes
        with self._store() as store:
            root = self._resolve_root(store, root)
            graph = store.get_neighborhood(root, symbol, depth=depth, limit=limit)
        return {
            "root": graph["root"],
            "depth": graph["depth"],
            "nodes": graph["nodes"],
            "relationships": [self._relationship_dict(r) for r in graph["relationships"]],
        }
    # -- Tool 5: callers --------------------------------------------------
    def get_callers(self, symbol: str, root: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or self.limits.max_callers
        with self._store() as store:
            root = self._resolve_root(store, root)
            relationships = store.get_callers(root, symbol, limit=limit)
        callers = [self._relationship_dict(r) for r in relationships]
        return {"symbol": symbol, "count": len(callers), "callers": callers}

    # -- Tool 6: callees --------------------------------------------------
    def get_callees(self, symbol: str, root: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or self.limits.max_callees
        with self._store() as store:
            root = self._resolve_root(store, root)
            relationships = store.get_callees(root, symbol, limit=limit)
        callees = [self._relationship_dict(r) for r in relationships]
        return {"symbol": symbol, "count": len(callees), "callees": callees}

    # -- Tool 7: tests ----------------------------------------------------
    def get_tests(self, symbol: str, root: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or self.limits.max_tests
        with self._store() as store:
            root = self._resolve_root(store, root)
            relationships = store.get_tests_for(root, symbol, limit=limit)
        tests = [self._relationship_dict(r) for r in relationships]
        return {"symbol": symbol, "count": len(tests), "tests": tests}

    # -- Tool 8: database context -----------------------------------------
    def get_database_context(self, symbol: str, root: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or self.limits.max_database
        with self._store() as store:
            root = self._resolve_root(store, root)
            relationships = store.get_relationships_by_types(
                root, symbol, sorted(DATABASE_RELATIONSHIP_TYPES), limit=limit
            )
        database = [self._relationship_dict(r) for r in relationships]
        return {"symbol": symbol, "count": len(database), "database": database}

    # -- Tool 9: configuration context ------------------------------------
    def get_config_context(self, symbol: str, root: Optional[str] = None, limit: Optional[int] = None) -> Dict[str, Any]:
        limit = limit or self.limits.max_config
        with self._store() as store:
            root = self._resolve_root(store, root)
            relationships = store.get_config_references(root, symbol, limit=limit)
        configuration = [self._relationship_dict(r) for r in relationships][:limit]
        return {"symbol": symbol, "count": len(configuration), "configuration": configuration}

    # -- Higher-level operation -------------------------------------------
    def get_context(self, symbol: str, root: Optional[str] = None) -> Dict[str, Any]:
        limits = self.limits
        with self._store() as store:
            root = self._resolve_root(store, root)
            info = store.get_symbol(root, symbol)
            if info is None:
                raise LookupError(f"symbol not found: {symbol}")

            source = self._source_from_symbol(root, info, limits.max_source_lines)
            relationships = store.get_relationships(root, symbol, limit=limits.max_relationships)
            callers = [self._relationship_dict(r) for r in store.get_callers(root, symbol, limit=limits.max_callers)]
            callees = [self._relationship_dict(r) for r in store.get_callees(root, symbol, limit=limits.max_callees)]
            tests = [self._relationship_dict(r) for r in store.get_tests_for(root, symbol, limit=limits.max_tests)]
            database = [
                self._relationship_dict(r)
                for r in store.get_relationships_by_types(
                    root, symbol, sorted(DATABASE_RELATIONSHIP_TYPES), limit=limits.max_database
                )
            ]
            configuration = [
                self._relationship_dict(r)
                for r in store.get_config_references(root, symbol, limit=limits.max_config)
            ]

        return {
            "query": symbol,
            "symbol": self._symbol_summary(info),
            "source": source,
            "relationships": [self._relationship_dict(r) for r in relationships],
            "callers": callers,
            "callees": callees,
            "tests": tests,
            "database": database,
            "configuration": configuration,
        }

    def _source_from_symbol(self, root: str, info: Dict[str, Any], max_lines: int) -> Dict[str, Any]:
        file = info["file"]
        start = int(info["line"])
        requested_end = int(info.get("end_line") or start)
        end = requested_end
        truncated = False
        if end - start + 1 > max_lines:
            end = start + max_lines - 1
            truncated = True
        return {
            "file": file,
            "start_line": start,
            "end_line": end,
            "requested_end_line": requested_end,
            "line_count": end - start + 1,
            "truncated": truncated,
            "content": self._read_source(root, file, start, end),
        }


