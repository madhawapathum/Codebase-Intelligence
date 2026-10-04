"""Provider-neutral AI tool interface above the retrieval layer.

This module exposes a small, read-only, bounded set of tools an AI agent can call
to query the Codebase Intelligence graph. It is a thin boundary:

    AI  →  CodebaseTools  →  Retriever  →  GraphStore  →  SQLite

It never parses code, never executes repository/source code, never runs SQL or
shell commands supplied by the AI, never connects to application databases, and
never mutates the graph. It only returns structured, JSON-compatible facts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .graph_store import DEFAULT_MAX_DEPTH, MAX_LIMIT
from .retrieval import Retriever, RetrievalLimits

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "search",
        "description": "Search the codebase graph for symbols, database tables, and configuration keys matching a substring.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Substring to search for, e.g. 'user'."},
                "limit": {"type": "integer", "description": "Maximum results (optional, bounded)."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_symbol",
        "description": "Return metadata and a relationship-type summary for one symbol by qualified name.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name, e.g. 'models.User'."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_source",
        "description": "Return the source region for a symbol, bounded by a line budget.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "max_lines": {"type": "integer", "description": "Maximum source lines to return (optional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_context",
        "description": "Return a bounded context package for a symbol: symbol, source, relationships, callers, callees, tests, database and configuration context.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name, e.g. 'app.create_user'."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_neighborhood",
        "description": "Return a bounded graph neighborhood around a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "depth": {"type": "integer", "description": "Neighborhood depth 0..3 (default 1)."},
                "limit": {"type": "integer", "description": "Maximum nodes/relationships (optional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_callers",
        "description": "Return the direct callers of a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "limit": {"type": "integer", "description": "Maximum callers (optional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_callees",
        "description": "Return the direct callees of a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "limit": {"type": "integer", "description": "Maximum callees (optional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_tests",
        "description": "Return tests associated with a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "limit": {"type": "integer", "description": "Maximum tests (optional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_database_context",
        "description": "Return database/ORM relationships for a symbol (writes, queries, defines_table, etc.).",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "limit": {"type": "integer", "description": "Maximum relationships (optional)."},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_config_context",
        "description": "Return configuration references (reads/writes) for a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Qualified symbol name."},
                "limit": {"type": "integer", "description": "Maximum references (optional)."},
            },
            "required": ["name"],
        },
    },
]
class CodebaseTools:
    """Read-only, bounded, structured tool access over ``Retriever``.

    Each method returns an envelope::

        {"ok": True, "tool": "...", "query": "...", "result": {...}}
        {"ok": False, "tool": "...", "query": "...", "error": {"type": "...", "message": "..."}}

    Normal retrieval failures (e.g. an unknown symbol) are returned as structured
    errors rather than raised. Unexpected programming errors still propagate.
    """

    def __init__(self, database_path: str, limits: Optional[RetrievalLimits] = None):
        self.retriever = Retriever(database_path, limits=limits)
        self._registry_map = {
            "search": self.search,
            "get_symbol": self.get_symbol,
            "get_source": self.get_source,
            "get_context": self.get_context,
            "get_neighborhood": self.get_neighborhood,
            "get_callers": self.get_callers,
            "get_callees": self.get_callees,
            "get_tests": self.get_tests,
            "get_database_context": self.get_database_context,
            "get_config_context": self.get_config_context,
        }

    @staticmethod
    def _success(tool: str, query: str, result: Any) -> Dict[str, Any]:
        return {"ok": True, "tool": tool, "query": query, "result": result}

    @staticmethod
    def _failure(tool: str, query: Optional[str], error_type: str, message: str) -> Dict[str, Any]:
        return {
            "ok": False,
            "tool": tool,
            "query": query,
            "error": {"type": error_type, "message": message},
        }

    @staticmethod
    def _coerce_int(value: Any, name: str, default: int, minimum: int = 1, maximum: Optional[int] = None) -> int:
        if value is None:
            return default
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"'{name}' must be an integer")
        if value < minimum or (maximum is not None and value > maximum):
            raise ValueError(f"'{name}' is out of range")
        return value

    def _call(self, tool: str, query: str, func) -> Dict[str, Any]:
        try:
            return self._success(tool, query, func())
        except LookupError as exc:
            return self._failure(tool, query, "symbol_not_found", str(exc))
        except ValueError as exc:
            return self._failure(tool, query, "invalid_arguments", str(exc))

    @staticmethod
    def _require_name(name: Any) -> str:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("'name' must be a non-empty string")
        return name

    # -- tool methods ------------------------------------------------------
    def search(self, query: str, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "search"
        if not isinstance(query, str) or not query.strip():
            return self._failure(tool, query, "invalid_arguments", "'query' must be a non-empty string")

        def run():
            resolved_limit = self._coerce_int(limit, "limit", self.retriever.limits.max_search_results, maximum=MAX_LIMIT)
            return self.retriever.search(query, limit=resolved_limit)

        return self._call(tool, query, run)

    def get_symbol(self, name: str) -> Dict[str, Any]:
        tool = "get_symbol"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))
        return self._call(tool, name, lambda: self.retriever.get_symbol(name))

    def get_source(self, name: str, max_lines: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_source"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved = self._coerce_int(max_lines, "max_lines", self.retriever.limits.max_source_lines, minimum=1)
            return self.retriever.get_source(name, max_lines=resolved)

        return self._call(tool, name, run)

    def get_context(self, name: str) -> Dict[str, Any]:
        tool = "get_context"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))
        return self._call(tool, name, lambda: self.retriever.get_context(name))

    def get_neighborhood(self, name: str, depth: int = 1, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_neighborhood"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved_depth = self._coerce_int(depth, "depth", 1, minimum=0, maximum=DEFAULT_MAX_DEPTH)
            resolved_limit = self._coerce_int(limit, "limit", self.retriever.limits.max_neighborhood_nodes, maximum=MAX_LIMIT)
            return self.retriever.get_neighborhood(name, depth=resolved_depth, limit=resolved_limit)

        return self._call(tool, name, run)
    def get_callers(self, name: str, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_callers"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved = self._coerce_int(limit, "limit", self.retriever.limits.max_callers, maximum=MAX_LIMIT)
            return self.retriever.get_callers(name, limit=resolved)

        return self._call(tool, name, run)

    def get_callees(self, name: str, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_callees"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved = self._coerce_int(limit, "limit", self.retriever.limits.max_callees, maximum=MAX_LIMIT)
            return self.retriever.get_callees(name, limit=resolved)

        return self._call(tool, name, run)

    def get_tests(self, name: str, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_tests"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved = self._coerce_int(limit, "limit", self.retriever.limits.max_tests, maximum=MAX_LIMIT)
            return self.retriever.get_tests(name, limit=resolved)

        return self._call(tool, name, run)

    def get_database_context(self, name: str, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_database_context"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved = self._coerce_int(limit, "limit", self.retriever.limits.max_database, maximum=MAX_LIMIT)
            return self.retriever.get_database_context(name, limit=resolved)

        return self._call(tool, name, run)

    def get_config_context(self, name: str, limit: Optional[int] = None) -> Dict[str, Any]:
        tool = "get_config_context"
        try:
            name = self._require_name(name)
        except ValueError as exc:
            return self._failure(tool, name, "invalid_arguments", str(exc))

        def run():
            resolved = self._coerce_int(limit, "limit", self.retriever.limits.max_config, maximum=MAX_LIMIT)
            return self.retriever.get_config_context(name, limit=resolved)

        return self._call(tool, name, run)

    # -- dispatch / metadata ----------------------------------------------
    def call(self, name: str, arguments: Any) -> Dict[str, Any]:
        """Execute a single tool call by name with a JSON-object of arguments."""
        if not isinstance(name, str):
            return self._failure(name, None, "unknown_tool", "tool name must be a string")
        method = self._registry_map.get(name)
        if method is None:
            return self._failure(name, None, "unknown_tool", f"unknown tool: {name}")
        if not isinstance(arguments, dict):
            return self._failure(name, None, "invalid_arguments", "arguments must be a JSON object")
        try:
            return method(**arguments)
        except TypeError as exc:
            return self._failure(name, None, "invalid_arguments", str(exc))

    def definitions(self) -> List[Dict[str, Any]]:
        return list(TOOL_DEFINITIONS)


def get_tool_definitions() -> List[Dict[str, Any]]:
    """Return the provider-neutral metadata for every available tool."""
    return list(TOOL_DEFINITIONS)


def execute_tool(name: str, arguments: Any, tools: CodebaseTools) -> Dict[str, Any]:
    """Execute a single tool call against a ``CodebaseTools`` instance."""
    return tools.call(name, arguments)


