from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import ModuleInfo, Relationship, RepositoryModel, Symbol


def _resolve_relative_module(module_name: str, file_name: str, level: int, module_path: str) -> str:
    if level <= 0:
        return module_path

    module_path = module_path.lstrip(".")
    parts = module_name.split(".") if module_name else []
    package_parts = parts if file_name.endswith("__init__.py") else parts[:-1]
    for _ in range(level - 1):
        if package_parts:
            package_parts = package_parts[:-1]
    if not module_path:
        return ".".join(package_parts)
    if package_parts:
        return ".".join(package_parts + module_path.split("."))
    return module_path


def _build_alias_map(module: ModuleInfo) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for imp in module.imports:
        if imp.type == "import":
            if imp.module:
                key = imp.alias or imp.module.split(".")[-1]
                aliases[key] = imp.module
        elif imp.type == "from_import":
            if imp.module:
                resolved_module = _resolve_relative_module(module.name, module.file, imp.level, imp.module)
                target = f"{resolved_module}.{imp.symbol}" if imp.symbol and resolved_module else imp.symbol
                key = imp.alias or imp.symbol
                if key:
                    aliases[key] = target
    return aliases


def _build_symbol_index(repository: RepositoryModel) -> Dict[str, List[str]]:
    index: Dict[str, List[str]] = {}
    for module in repository.modules:
        for symbol in module.symbols:
            index.setdefault(symbol.name, []).append(symbol.qualified_name)
    return index


def _resolve_local_reference(module: ModuleInfo, target: str, symbol_index: Dict[str, List[str]], aliases: Dict[str, str]) -> Tuple[Optional[str], str]:
    if not target or target == "<unknown>":
        return None, "unknown"

    known_symbols = {
        qualified_name
        for qualified_names in symbol_index.values()
        for qualified_name in qualified_names
    }
    if target in aliases:
        imported_target = aliases[target]
        if imported_target in known_symbols:
            return imported_target, "resolved"
        return None, "unresolved"

    same_module_matches = [symbol for symbol in module.symbols if symbol.name == target]
    if same_module_matches:
        return same_module_matches[0].qualified_name, "resolved"

    if target.startswith("self.") and target.count(".") >= 1:
        member = target.split(".", 1)[1].split(".", 1)[0]
        for symbol in module.symbols:
            if symbol.symbol_type == "method" and symbol.name == member and symbol.parent:
                if symbol.parent.startswith(module.name):
                    return symbol.qualified_name, "resolved"
        return None, "unknown"

    if "." in target:
        head, tail = target.split(".", 1)
        if head in aliases:
            base = aliases[head]
            candidate = f"{base}.{tail}" if base else tail
            if candidate in known_symbols:
                return candidate, "resolved"
            return None, "unresolved"

    return None, "unresolved"


def _resolve_base_name(module: ModuleInfo, base_expr: str, symbol_index: Dict[str, List[str]], aliases: Dict[str, str]) -> Tuple[Optional[str], str]:
    if not base_expr or base_expr == "<unknown>":
        return None, "unknown"
    if base_expr in aliases:
        return aliases[base_expr], "resolved"
    matches = symbol_index.get(base_expr, [])
    if len(matches) == 1:
        return matches[0], "resolved"
    if len(matches) > 1:
        return None, "ambiguous"
    return None, "unresolved"


def resolve_module(module: ModuleInfo, module_map: Dict[str, ModuleInfo], symbol_index: Dict[str, List[str]]) -> List[Relationship]:
    """Resolve syntactic calls to symbol identities using the repository symbol index."""
    relationships: List[Relationship] = []
    aliases = _build_alias_map(module)

    for call in module.calls:
        source = call.get("caller") or module.name
        target = call.get("target") or "<unknown>"
        resolved_to, status = _resolve_local_reference(module, target, symbol_index, aliases)
        if resolved_to is None and target.startswith("self."):
            status = "unknown"
        rel = Relationship(
            source=source,
            target=target,
            type="calls",
            source_file=module.file,
            source_line=call.get("line"),
            confidence="certain" if status == "resolved" else "probable" if status in {"ambiguous", "resolved"} else "uncertain",
            resolution_status=status,
            resolved_to=resolved_to,
        )
        if resolved_to is not None:
            rel.target = resolved_to
        else:
            rel.target = target
        relationships.append(rel)
        call["resolved_to"] = resolved_to
        call["resolution_status"] = status

    return relationships


def build_symbol_table(module: ModuleInfo) -> List[Symbol]:
    symbols: List[Symbol] = []
    for class_info in module.classes:
        is_test_class = module.is_test_file and (
            class_info.name.startswith("Test")
            or any(base.endswith("TestCase") for base in class_info.bases)
        )
        symbols.append(
            Symbol(
                id=class_info.qualified_name,
                name=class_info.name,
                qualified_name=class_info.qualified_name,
                symbol_type="class",
                file=module.file,
                module=module.name,
                line=class_info.line,
                end_line=class_info.end_line,
                details={"is_test": False, "kind": "class"},
            )
        )
        for method in class_info.methods:
            is_test_method = is_test_class and method.name.startswith("test_")
            symbols.append(
                Symbol(
                    id=method.qualified_name,
                    name=method.name,
                    qualified_name=method.qualified_name,
                    symbol_type="method",
                    file=module.file,
                    module=module.name,
                    line=method.line,
                    end_line=method.end_line,
                    parent=class_info.qualified_name,
                    details={
                        "is_test": is_test_method,
                        "kind": "test_method" if is_test_method else "method",
                    },
                )
            )
    for function in module.functions:
        is_test_function = (
            module.is_test_file
            and function.containing_class is None
            and function.name.startswith("test_")
        )
        symbols.append(
            Symbol(
                id=function.qualified_name,
                name=function.name,
                qualified_name=function.qualified_name,
                symbol_type="function",
                file=module.file,
                module=module.name,
                line=function.line,
                end_line=function.end_line,
                details={
                    "is_test": is_test_function,
                    "kind": "test_function" if is_test_function else "function",
                },
            )
        )
    return symbols


def build_decorator_relationships(
    module: ModuleInfo,
    symbol_index: Dict[str, List[str]],
) -> List[Relationship]:
    relationships: List[Relationship] = []
    aliases = _build_alias_map(module)
    known_symbols = {
        qualified_name
        for qualified_names in symbol_index.values()
        for qualified_name in qualified_names
    }

    for decorator in module.decorators:
        target = decorator.get("target") or decorator.get("decorator", "<unknown>")
        resolved_to, status = _resolve_local_reference(module, target, symbol_index, aliases)
        if resolved_to not in known_symbols:
            resolved_to = None
            status = "unknown" if target == "<unknown>" else "unresolved"

        decorator["resolution_status"] = status
        decorator["resolved_to"] = resolved_to
        relationships.append(
            Relationship(
                source=decorator["source"],
                target=resolved_to or target,
                type="decorated_by",
                source_file=module.file,
                source_line=decorator.get("line"),
                confidence="certain" if resolved_to else "uncertain",
                resolution_status=status,
                resolved_to=resolved_to,
                details={
                    "decorator_expression": decorator.get("decorator"),
                    "arguments": decorator.get("arguments", []),
                },
            )
        )
    return relationships


def build_test_relationships(
    module: ModuleInfo,
    call_relationships: List[Relationship],
    symbols_by_qualified_name: Dict[str, Symbol],
) -> List[Relationship]:
    aliases = _build_alias_map(module)
    relationships: List[Relationship] = []

    for call, call_relationship in zip(module.calls, call_relationships):
        test_symbol = symbols_by_qualified_name.get(call_relationship.source)
        if test_symbol is None or not test_symbol.details.get("is_test"):
            continue

        target = call.get("target", "")
        head, separator, _ = target.partition(".")
        directly_imported = target in aliases or (separator and head in aliases)
        resolved_to = call_relationship.resolved_to
        resolved_symbol = symbols_by_qualified_name.get(resolved_to or "")
        if (
            not directly_imported
            or resolved_symbol is None
            or resolved_symbol.symbol_type not in {"function", "class"}
        ):
            continue

        relationships.append(
            Relationship(
                source=call_relationship.source,
                target=resolved_symbol.qualified_name,
                type="tests",
                source_file=module.file,
                source_line=call.get("line"),
                confidence="certain",
                resolution_status="resolved",
                resolved_to=resolved_symbol.qualified_name,
            )
        )
    return relationships


def build_configuration_relationships(module: ModuleInfo) -> List[Relationship]:
    relationships: List[Relationship] = []
    for reference in module.configuration_references:
        name = reference["name"]
        is_assignment = reference.get("access_type") == "assignment"
        status = "resolved" if reference.get("confidence") == "certain" else "unknown"
        relationships.append(
            Relationship(
                source=reference.get("scope") or module.name,
                target=f"config.{name}",
                type="writes_config" if is_assignment else "reads_config",
                source_file=module.file,
                source_line=reference.get("line"),
                confidence=reference.get("confidence", "certain"),
                resolution_status=status,
                resolved_to=f"config.{name}" if status == "resolved" else None,
                details={
                    "name": name,
                    "configuration_type": reference.get("type"),
                    "access_type": reference.get("access_type"),
                    "sensitive": reference.get("sensitive", False),
                },
            )
        )
    return relationships


def build_database_relationships(
    module: ModuleInfo,
    symbol_index: Dict[str, List[str]],
) -> List[Relationship]:
    relationships: List[Relationship] = []
    aliases = _build_alias_map(module)
    known_symbols = {
        qualified_name
        for qualified_names in symbol_index.values()
        for qualified_name in qualified_names
    }
    for reference in module.database_references:
        scope = reference.get("scope") or module.name
        table = reference.get("table")
        operation = reference.get("operation", "UNKNOWN")
        confidence = reference.get("confidence", "likely")

        if operation == "DEFINE":
            if table:
                target = f"db.table.{table}"
                relationships.append(
                    Relationship(
                        source=scope,
                        target=target,
                        type="defines_table",
                        source_file=module.file,
                        source_line=reference.get("line"),
                        confidence="certain",
                        resolution_status="resolved",
                        resolved_to=target,
                        details={
                            "operation": operation,
                            "api": reference.get("api"),
                            "model": scope,
                            "table": table,
                        },
                    )
                )
            continue

        if table:
            target = f"db.table.{table}"
            status = "resolved"
            relationships.append(
                Relationship(
                    source=scope,
                    target=target,
                    type="queries",
                    source_file=module.file,
                    source_line=reference.get("line"),
                    confidence=confidence,
                    resolution_status=status,
                    resolved_to=target,
                    details={"operation": operation, "api": reference.get("api")},
                )
            )
            if operation == "SELECT":
                relation_type = "reads_table"
            elif operation in {"INSERT", "UPDATE", "DELETE"}:
                relation_type = "writes_table"
            else:
                relation_type = None
            if relation_type:
                relationships.append(
                    Relationship(
                        source=scope,
                        target=target,
                        type=relation_type,
                        source_file=module.file,
                        source_line=reference.get("line"),
                        confidence=confidence,
                        resolution_status=status,
                        resolved_to=target,
                        details={"operation": operation, "api": reference.get("api")},
                    )
                )
            continue

        model_name = reference.get("model")
        if not model_name:
            continue
        resolved_model = aliases.get(model_name)
        if resolved_model not in known_symbols:
            candidates = symbol_index.get(model_name, [])
            resolved_model = candidates[0] if len(candidates) == 1 else None
        relation_type = {
            "SELECT": "queries",
            "INSERT": "writes",
            "UPDATE": "updates",
            "DELETE": "deletes",
        }.get(operation, "uses")
        relationships.append(
            Relationship(
                source=scope,
                target=resolved_model or model_name,
                type=relation_type,
                source_file=module.file,
                source_line=reference.get("line"),
                confidence="likely",
                resolution_status="resolved" if resolved_model else "unknown",
                resolved_to=resolved_model,
                details={"operation": operation, "api": reference.get("api")},
            )
        )
    return relationships


def build_inheritance_relationships(repository: RepositoryModel, symbol_index: Dict[str, List[str]]) -> List[Relationship]:
    relationships: List[Relationship] = []
    for module in repository.modules:
        aliases = _build_alias_map(module)
        for class_info in module.classes:
            for base_name in class_info.bases:
                resolved, status = _resolve_base_name(module, base_name, symbol_index, aliases)
                target = resolved or base_name
                relationship = Relationship(
                    source=class_info.qualified_name,
                    target=target,
                    type="inherits",
                    source_file=module.file,
                    source_line=class_info.line,
                    confidence="certain" if status == "resolved" else "probable" if status == "ambiguous" else "uncertain",
                    resolution_status=status,
                    resolved_to=resolved,
                )
                relationships.append(relationship)
    return relationships


def analyze_repository(repository: RepositoryModel) -> RepositoryModel:
    module_map = {module.name: module for module in repository.modules}
    for module in repository.modules:
        module.symbols = build_symbol_table(module)

    symbol_index = _build_symbol_index(repository)
    symbols_by_qualified_name = {
        symbol.qualified_name: symbol
        for module in repository.modules
        for symbol in module.symbols
    }
    all_relationships: List[Relationship] = []
    for module in repository.modules:
        call_relationships = resolve_module(module, module_map, symbol_index)
        module.relationships = list(call_relationships)
        module.relationships.extend(build_decorator_relationships(module, symbol_index))
        module.relationships.extend(
            build_test_relationships(
                module,
                call_relationships,
                symbols_by_qualified_name,
            )
        )
        module.relationships.extend(build_configuration_relationships(module))
        module.relationships.extend(build_database_relationships(module, symbol_index))
        all_relationships.extend(module.relationships)

    for relationship in build_inheritance_relationships(repository, symbol_index):
        all_relationships.append(relationship)
        for module in repository.modules:
            if relationship.source.startswith(f"{module.name}.") or relationship.source == module.name:
                module.relationships.append(relationship)

    repository.relationships = all_relationships
    repository.symbols = [symbol for module in repository.modules for symbol in module.symbols]
    repository.unresolved = [
        {"target": relation.target, "source": relation.source, "resolution_status": relation.resolution_status}
        for relation in repository.relationships
        if relation.resolution_status in {"unresolved", "unknown", "ambiguous"}
    ]
    return repository
