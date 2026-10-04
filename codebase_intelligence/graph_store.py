from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from .models import RepositoryModel


DEFAULT_LIMIT = 100
MAX_LIMIT = 400
DEFAULT_MAX_DEPTH = 3


class GraphStore:
    """Persist repository graph snapshots in SQLite and query targeted graph data."""

    def __init__(self, database_path: str | Path):
        self.database_path = str(database_path)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS repositories (
                id INTEGER PRIMARY KEY,
                root_path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                path TEXT NOT NULL,
                language TEXT NOT NULL,
                module_name TEXT NOT NULL,
                line_count INTEGER NOT NULL,
                is_test_file INTEGER NOT NULL DEFAULT 0,
                UNIQUE(repository_id, path)
            );
            CREATE TABLE IF NOT EXISTS modules (
                id INTEGER PRIMARY KEY,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                UNIQUE(repository_id, name)
            );
            CREATE TABLE IF NOT EXISTS symbols (
                id TEXT NOT NULL,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                qualified_name TEXT NOT NULL,
                name TEXT NOT NULL,
                symbol_type TEXT NOT NULL,
                line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                parent TEXT,
                scope TEXT,
                is_test INTEGER NOT NULL DEFAULT 0,
                module_name TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(repository_id, id)
            );
            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                module_id INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
                import_type TEXT NOT NULL,
                source_module TEXT,
                imported_name TEXT,
                alias TEXT,
                relative_level INTEGER NOT NULL DEFAULT 0,
                line INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS relationships (
                id INTEGER PRIMARY KEY,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                target TEXT NOT NULL,
                type TEXT NOT NULL,
                source_file TEXT,
                source_line INTEGER,
                confidence TEXT NOT NULL,
                resolution_status TEXT NOT NULL,
                resolved_to TEXT,
                details_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS config_references (
                id INTEGER PRIMARY KEY,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                config_type TEXT NOT NULL,
                file TEXT NOT NULL,
                line INTEGER NOT NULL,
                sensitive INTEGER NOT NULL DEFAULT 0,
                scope TEXT,
                access_type TEXT,
                confidence TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS database_objects (
                id TEXT NOT NULL,
                repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
                object_type TEXT NOT NULL,
                name TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(repository_id, id)
            );
            CREATE INDEX IF NOT EXISTS idx_files_path ON files(repository_id, path);
            CREATE INDEX IF NOT EXISTS idx_files_module ON files(repository_id, module_name);
            CREATE INDEX IF NOT EXISTS idx_modules_name ON modules(repository_id, name);
            CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(repository_id, qualified_name);
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(repository_id, name);
            CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(repository_id, file_id);
            CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(repository_id, source);
            CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(repository_id, target);
            CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships(repository_id, type);
            CREATE INDEX IF NOT EXISTS idx_config_name ON config_references(repository_id, name);
            CREATE INDEX IF NOT EXISTS idx_db_objects_name ON database_objects(repository_id, name);
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _repository_id(self, root_path: str | Path) -> Optional[int]:
        normalized_root = str(Path(root_path).resolve())
        row = self.connection.execute(
            "SELECT id FROM repositories WHERE root_path = ?",
            (normalized_root,),
        ).fetchone()
        return int(row["id"]) if row else None

    def list_repositories(self) -> List[str]:
        rows = self.connection.execute(
            "SELECT root_path FROM repositories ORDER BY root_path"
        ).fetchall()
        return [str(row["root_path"]) for row in rows]

    def store_repository(self, model: RepositoryModel) -> int:
        """Replace any stored snapshot for this root in one transaction."""
        root_path = str(Path(model.root).resolve())
        with self.connection:
            self.connection.execute(
                "INSERT INTO repositories(root_path) VALUES (?) "
                "ON CONFLICT(root_path) DO NOTHING",
                (root_path,),
            )
            repository_id = int(
                self.connection.execute(
                    "SELECT id FROM repositories WHERE root_path = ?",
                    (root_path,),
                ).fetchone()["id"]
            )
            for table in (
                "relationships",
                "imports",
                "config_references",
                "database_objects",
                "symbols",
                "modules",
                "files",
            ):
                self.connection.execute(
                    f"DELETE FROM {table} WHERE repository_id = ?",
                    (repository_id,),
                )

            file_ids: Dict[str, int] = {}
            for file_info in model.files:
                cursor = self.connection.execute(
                    """
                    INSERT INTO files(
                        repository_id, path, language, module_name, line_count, is_test_file
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repository_id,
                        file_info.path,
                        file_info.language,
                        file_info.module_name,
                        file_info.line_count,
                        int(file_info.is_test_file),
                    ),
                )
                file_ids[file_info.path] = int(cursor.lastrowid)

            module_ids: Dict[str, int] = {}
            for module in model.modules:
                file_id = file_ids.get(module.file)
                if file_id is None:
                    continue
                cursor = self.connection.execute(
                    "INSERT INTO modules(repository_id, file_id, name) VALUES (?, ?, ?)",
                    (repository_id, file_id, module.name),
                )
                module_ids[module.name] = int(cursor.lastrowid)
                for import_info in module.imports:
                    self.connection.execute(
                        """
                        INSERT INTO imports(
                            repository_id, module_id, import_type, source_module,
                            imported_name, alias, relative_level, line
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            repository_id,
                            module_ids[module.name],
                            import_info.type,
                            import_info.module,
                            import_info.symbol,
                            import_info.alias,
                            import_info.level,
                            import_info.line,
                        ),
                    )
                for reference in module.configuration_references:
                    self.connection.execute(
                        """
                        INSERT INTO config_references(
                            repository_id, name, config_type, file, line,
                            sensitive, scope, access_type, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            repository_id,
                            reference["name"],
                            reference.get("type", "configuration_key"),
                            reference.get("source_file", module.file),
                            reference.get("line", 0),
                            int(reference.get("sensitive", False)),
                            reference.get("scope"),
                            reference.get("access_type"),
                            reference.get("confidence", "unknown"),
                        ),
                    )

            for symbol in model.symbols:
                file_id = file_ids.get(symbol.file)
                if file_id is None:
                    continue
                self.connection.execute(
                    """
                    INSERT INTO symbols(
                        id, repository_id, file_id, qualified_name, name, symbol_type,
                        line, end_line, parent, scope, is_test, module_name, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol.qualified_name,
                        repository_id,
                        file_id,
                        symbol.qualified_name,
                        symbol.name,
                        symbol.symbol_type,
                        symbol.line,
                        symbol.end_line,
                        symbol.parent,
                        symbol.scope,
                        int(bool(symbol.details.get("is_test"))),
                        symbol.module,
                        json.dumps(symbol.details, sort_keys=True),
                    ),
                )

            table_metadata: Dict[str, Dict[str, Any]] = {}
            for relationship in model.relationships:
                if (
                    relationship.type == "defines_table"
                    and relationship.target.startswith("db.table.")
                ):
                    table_metadata.setdefault(
                        relationship.target,
                        {
                            "model": relationship.source,
                            "table": relationship.target.removeprefix("db.table."),
                            "source_file": relationship.source_file,
                            "source_line": relationship.source_line,
                        },
                    )

            object_ids: Set[str] = set()
            for relationship in model.relationships:
                if relationship.target.startswith("db.table."):
                    object_id = relationship.target
                    object_name = relationship.target.removeprefix("db.table.")
                    if object_id not in object_ids:
                        self.connection.execute(
                            """
                            INSERT INTO database_objects(
                                id, repository_id, object_type, name, metadata_json
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                object_id,
                                repository_id,
                                "database_table",
                                object_name,
                                json.dumps(table_metadata.get(object_id, {}), sort_keys=True),
                            ),
                        )
                        object_ids.add(object_id)

            for relationship in model.relationships:
                self.connection.execute(
                    """
                    INSERT INTO relationships(
                        repository_id, source, target, type, source_file, source_line,
                        confidence, resolution_status, resolved_to, details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        repository_id,
                        relationship.source,
                        relationship.target,
                        relationship.type,
                        relationship.source_file,
                        relationship.source_line,
                        relationship.confidence,
                        relationship.resolution_status,
                        relationship.resolved_to,
                        json.dumps(relationship.details, sort_keys=True),
                    ),
                )
        return repository_id

    def _require_repository(self, root_path: str | Path) -> int:
        repository_id = self._repository_id(root_path)
        if repository_id is None:
            raise LookupError(f"No stored repository at {root_path}")
        return repository_id

    @staticmethod
    def _row_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    @staticmethod
    def _limit(limit: int) -> int:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        return min(limit, MAX_LIMIT)

    def get_summary(self, root_path: str | Path) -> Dict[str, int]:
        repository_id = self._require_repository(root_path)

        def count(table: str, condition: str = "") -> int:
            row = self.connection.execute(
                f"SELECT COUNT(*) AS count FROM {table} WHERE repository_id = ? {condition}",
                (repository_id,),
            ).fetchone()
            return int(row["count"])

        return {
            "files": count("files"),
            "modules": count("modules"),
            "symbols": count("symbols"),
            "relationships": count("relationships"),
            "tests": count("symbols", "AND is_test = 1"),
            "database_objects": count("database_objects"),
            "config_references": count("config_references"),
        }

    def get_symbol(self, root_path: str | Path, qualified_name: str) -> Optional[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        row = self.connection.execute(
            """
            SELECT s.*, f.path AS file
            FROM symbols s JOIN files f ON f.id = s.file_id
            WHERE s.repository_id = ? AND s.qualified_name = ?
            """,
            (repository_id, qualified_name),
        ).fetchone()
        result = self._row_dict(row)
        if result is not None:
            result["metadata"] = json.loads(result.pop("metadata_json"))
            result["is_test"] = bool(result["is_test"])
        return result

    def get_file(self, root_path: str | Path, path: str) -> Optional[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        row = self.connection.execute(
            "SELECT * FROM files WHERE repository_id = ? AND path = ?",
            (repository_id, path),
        ).fetchone()
        result = self._row_dict(row)
        if result is not None:
            result["is_test_file"] = bool(result["is_test_file"])
        return result

    def get_relationships(
        self,
        root_path: str | Path,
        symbol: str,
        limit: int = DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        rows = self.connection.execute(
            """
            SELECT * FROM relationships
            WHERE repository_id = ? AND (source = ? OR target = ? OR resolved_to = ?)
            ORDER BY id LIMIT ?
            """,
            (repository_id, symbol, symbol, symbol, self._limit(limit)),
        ).fetchall()
        return [self._relationship_dict(row) for row in rows]

    def get_callers(self, root_path: str | Path, symbol: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        return self._get_relationships_by_type(
            root_path, symbol, ("calls",), incoming=True, limit=limit
        )

    def get_callees(self, root_path: str | Path, symbol: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        return self._get_relationships_by_type(
            root_path, symbol, ("calls",), incoming=False, limit=limit
        )

    def get_tests_for(self, root_path: str | Path, symbol: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        return self._get_relationships_by_type(
            root_path, symbol, ("tests",), incoming=True, limit=limit
        )

    def get_config_references(self, root_path: str | Path, symbol: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        module_name = self._module_for_symbol(repository_id, symbol)
        sources = {symbol}
        if module_name:
            sources.add(module_name)
        placeholders = ",".join("?" for _ in sources)
        rows = self.connection.execute(
            f"""
            SELECT * FROM relationships
            WHERE repository_id = ? AND source IN ({placeholders})
              AND type IN ('reads_config', 'writes_config')
            ORDER BY id LIMIT ?
            """,
            (repository_id, *sources, self._limit(limit)),
        ).fetchall()
        return [self._relationship_dict(row) for row in rows]

    def get_database_relationships(self, root_path: str | Path, symbol: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        return self._get_relationships_by_type(
            root_path,
            symbol,
            ("queries", "reads_table", "writes_table", "writes", "updates", "deletes"),
            incoming=False,
            limit=limit,
        )

    def get_relationships_by_types(
        self,
        root_path: str | Path,
        symbol: str,
        relationship_types: Sequence[str],
        limit: int = DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        """Return relationships of the given types where ``symbol`` is source or target."""
        repository_id = self._require_repository(root_path)
        types = tuple(relationship_types)
        type_placeholders = ",".join("?" for _ in types)
        rows = self.connection.execute(
            f"""
            SELECT * FROM relationships
            WHERE repository_id = ? AND (source = ? OR target = ?)
              AND type IN ({type_placeholders})
            ORDER BY id LIMIT ?
            """,
            (repository_id, symbol, symbol, *types, self._limit(limit)),
        ).fetchall()
        return [self._relationship_dict(row) for row in rows]

    def _get_relationships_by_type(
        self,
        root_path: str | Path,
        symbol: str,
        relationship_types: Sequence[str],
        incoming: bool,
        limit: int,
    ) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        type_placeholders = ",".join("?" for _ in relationship_types)
        direction = "target" if incoming else "source"
        query = (
            f"SELECT * FROM relationships WHERE repository_id = ? "
            f"AND {direction} = ? AND type IN ({type_placeholders}) ORDER BY id LIMIT ?"
        )
        rows = self.connection.execute(
            query,
            (repository_id, symbol, *relationship_types, self._limit(limit)),
        ).fetchall()
        return [self._relationship_dict(row) for row in rows]

    @staticmethod
    def _relationship_dict(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["details"] = json.loads(result.pop("details_json"))
        return result

    def get_imports(self, root_path: str | Path, symbol: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        module_name = self._module_for_symbol(repository_id, symbol)
        if module_name is None:
            return []
        rows = self.connection.execute(
            """
            SELECT i.import_type, i.source_module, i.imported_name, i.alias,
                   i.relative_level, i.line, m.name AS module_name
            FROM imports i JOIN modules m ON m.id = i.module_id
            WHERE i.repository_id = ? AND m.name = ?
            ORDER BY i.line, i.id LIMIT ?
            """,
            (repository_id, module_name, self._limit(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def _module_for_symbol(self, repository_id: int, symbol: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT module_name FROM symbols WHERE repository_id = ? AND qualified_name = ?",
            (repository_id, symbol),
        ).fetchone()
        if row:
            return str(row["module_name"])
        row = self.connection.execute(
            "SELECT name FROM modules WHERE repository_id = ? AND (name = ? OR ? LIKE name || '.%') "
            "ORDER BY LENGTH(name) DESC LIMIT 1",
            (repository_id, symbol, symbol),
        ).fetchone()
        return str(row["name"]) if row else None

    def get_module(self, root_path: str | Path, module_name: str, limit: int = DEFAULT_LIMIT) -> Optional[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        row = self.connection.execute(
            """
            SELECT m.name, f.path AS file, f.line_count, f.is_test_file
            FROM modules m JOIN files f ON f.id = m.file_id
            WHERE m.repository_id = ? AND m.name = ?
            """,
            (repository_id, module_name),
        ).fetchone()
        if row is None:
            return None
        symbols = self.connection.execute(
            """
            SELECT qualified_name, name, symbol_type, line, end_line, parent, scope, is_test
            FROM symbols WHERE repository_id = ? AND module_name = ?
            ORDER BY line LIMIT ?
            """,
            (repository_id, module_name, self._limit(limit)),
        ).fetchall()
        result = dict(row)
        result["is_test_file"] = bool(result["is_test_file"])
        result["symbols"] = [dict(symbol) for symbol in symbols]
        for symbol_data in result["symbols"]:
            symbol_data["is_test"] = bool(symbol_data["is_test"])
        symbol_ids = [item["qualified_name"] for item in result["symbols"]]
        placeholders = ",".join("?" for _ in symbol_ids)
        if placeholders:
            relations = self.connection.execute(
                f"""
                SELECT * FROM relationships
                WHERE repository_id = ?
                  AND (
                    source = ? OR source LIKE ? OR source IN ({placeholders})
                    OR target IN ({placeholders})
                  )
                ORDER BY id LIMIT ?
                """,
                (
                    repository_id,
                    module_name,
                    f"{module_name}.%",
                    *symbol_ids,
                    *symbol_ids,
                    self._limit(limit),
                ),
            ).fetchall()
            result["relationships"] = [self._relationship_dict(item) for item in relations]
        else:
            relations = self.connection.execute(
                """
                SELECT * FROM relationships
                WHERE repository_id = ? AND (source = ? OR source LIKE ?)
                ORDER BY id LIMIT ?
                """,
                (repository_id, module_name, f"{module_name}.%", self._limit(limit)),
            ).fetchall()
            result["relationships"] = [self._relationship_dict(item) for item in relations]
        return result

    def search_symbols(self, root_path: str | Path, query: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        pattern = f"%{query}%"
        rows = self.connection.execute(
            """
            SELECT s.qualified_name, s.name, s.symbol_type, s.module_name,
                   s.line, f.path AS file, s.is_test
            FROM symbols s JOIN files f ON f.id = s.file_id
            WHERE s.repository_id = ?
              AND (s.name LIKE ? OR s.qualified_name LIKE ? OR s.module_name LIKE ? OR f.path LIKE ?)
            ORDER BY s.qualified_name LIMIT ?
            """,
            (repository_id, pattern, pattern, pattern, pattern, self._limit(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_database_objects(self, root_path: str | Path, query: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        pattern = f"%{query}%"
        rows = self.connection.execute(
            """
            SELECT id, object_type, name
            FROM database_objects
            WHERE repository_id = ? AND (id LIKE ? OR name LIKE ?)
            ORDER BY name LIMIT ?
            """,
            (repository_id, pattern, pattern, self._limit(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_config_references(self, root_path: str | Path, query: str, limit: int = DEFAULT_LIMIT) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        pattern = f"%{query}%"
        rows = self.connection.execute(
            """
            SELECT name, config_type, file, line, scope, access_type, sensitive
            FROM config_references
            WHERE repository_id = ? AND name LIKE ?
            GROUP BY name ORDER BY name LIMIT ?
            """,
            (repository_id, pattern, self._limit(limit)),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_relationships(
        self,
        root_path: str | Path,
        relationship_type: str,
        limit: int = DEFAULT_LIMIT,
    ) -> List[Dict[str, Any]]:
        repository_id = self._require_repository(root_path)
        rows = self.connection.execute(
            "SELECT * FROM relationships WHERE repository_id = ? AND type = ? ORDER BY id LIMIT ?",
            (repository_id, relationship_type, self._limit(limit)),
        ).fetchall()
        return [self._relationship_dict(row) for row in rows]

    def get_neighborhood(
        self,
        root_path: str | Path,
        node_id: str,
        depth: int = 1,
        limit: int = DEFAULT_LIMIT,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> Dict[str, Any]:
        if depth < 0 or depth > max_depth:
            raise ValueError(f"depth must be between 0 and {max_depth}")
        repository_id = self._require_repository(root_path)
        limit = self._limit(limit)
        visited: Set[str] = {node_id}
        frontier: Set[str] = {node_id}
        edges: Dict[int, Dict[str, Any]] = {}

        for _ in range(depth):
            if not frontier or len(visited) >= limit:
                break
            frontier_list = sorted(frontier)
            placeholders = ",".join("?" for _ in frontier_list)
            rows = self.connection.execute(
                f"""
                SELECT * FROM relationships
                WHERE repository_id = ? AND (source IN ({placeholders}) OR target IN ({placeholders}))
                ORDER BY id LIMIT ?
                """,
                (repository_id, *frontier_list, *frontier_list, limit),
            ).fetchall()
            next_frontier: Set[str] = set()
            for row in rows:
                edge = self._relationship_dict(row)
                endpoints = {edge["source"], edge["target"]}
                new_endpoints = endpoints - visited
                if len(visited) + len(new_endpoints) > limit:
                    continue
                edges[edge["id"]] = edge
                visited.update(new_endpoints)
                next_frontier.update(new_endpoints)
            frontier = next_frontier

        nodes = self._get_nodes(repository_id, sorted(visited))
        return {
            "root": node_id,
            "depth": depth,
            "nodes": nodes,
            "relationships": [edges[key] for key in sorted(edges)[:limit]],
        }

    def _get_nodes(self, repository_id: int, node_ids: Iterable[str]) -> List[Dict[str, Any]]:
        ids = list(node_ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        symbols = self.connection.execute(
            f"""
            SELECT s.qualified_name AS id, s.symbol_type AS type, s.name, s.module_name,
                   s.line, f.path AS file, s.is_test
            FROM symbols s JOIN files f ON f.id = s.file_id
            WHERE s.repository_id = ? AND s.qualified_name IN ({placeholders})
            """,
            (repository_id, *ids),
        ).fetchall()
        nodes = [dict(row) for row in symbols]
        found = {node["id"] for node in nodes}
        for node_id in ids:
            if node_id in found:
                continue
            config = self.connection.execute(
                """
                SELECT name, 'configuration' AS type, MAX(sensitive) AS sensitive
                FROM config_references
                WHERE repository_id = ? AND 'config.' || name = ?
                GROUP BY name
                LIMIT 1
                """,
                (repository_id, node_id),
            ).fetchone()
            if config:
                nodes.append({"id": node_id, **dict(config)})
                continue
            database_object = self.connection.execute(
                "SELECT id, object_type AS type, name FROM database_objects "
                "WHERE repository_id = ? AND id = ?",
                (repository_id, node_id),
            ).fetchone()
            if database_object:
                nodes.append(dict(database_object))
            else:
                nodes.append({"id": node_id, "type": "external_or_unresolved"})
        return nodes
