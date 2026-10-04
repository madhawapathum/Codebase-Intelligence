from __future__ import annotations

import ast
import copy
import re
from typing import Any, Dict, List, Optional, Set

from .models import ClassInfo, FunctionInfo, ImportInfo, Symbol


CONFIG_NAMES = {
    "DEBUG", "DATABASE_URL", "DB_URL", "DB_HOST", "DB_PORT", "DB_NAME",
    "API_URL", "SECRET_KEY", "APP_ENV", "ENV", "HOST", "PORT",
}
CONFIG_HINTS = ("DATABASE", "DB_", "API_", "SECRET", "TOKEN", "PASSWORD", "HOST", "PORT", "DEBUG", "ENV", "URL")
SENSITIVE_HINTS = ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "CREDENTIAL", "DATABASE_URL", "DB_URL")
SQL_OPERATION_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
SQL_TABLE_RE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE)\s+[`\"\[]?([A-Za-z_][\w.$]*)", re.IGNORECASE)


class _StringLiteralRedactor(ast.NodeTransformer):
    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, (str, bytes)):
            return ast.copy_location(ast.Constant(value="<redacted>"), node)
        return node


class ModuleVisitor(ast.NodeVisitor):
    """Collect module symbols and relationships from a Python AST."""

    def __init__(self, file_path: str, module_name: str, is_test_file: bool = False):
        self.file_path = file_path
        self.module_name = module_name
        self.is_test_file = is_test_file
        self.imports: List[ImportInfo] = []
        self.classes: List[ClassInfo] = []
        self.functions: List[FunctionInfo] = []
        self.calls: List[Dict[str, Any]] = []
        self.assignments: List[Dict[str, Any]] = []
        self.decorators: List[Dict[str, Any]] = []
        self.configuration_references: List[Dict[str, Any]] = []
        self.database_references: List[Dict[str, Any]] = []
        self._module_aliases: Dict[str, str] = {}
        self._imported_os_names: Dict[str, str] = {}
        self._sql_assignments: Dict[tuple[str, str], str] = {}
        self._instance_types: Dict[tuple[str, str], str] = {}
        self._session_names: Set[str] = set()
        self.current_class: Optional[ClassInfo] = None
        self.current_function: Optional[FunctionInfo] = None
        self.symbols: List[Symbol] = []
        self.relationships: List[Dict[str, Any]] = []

    def _qualify_name(self, name: str) -> str:
        if self.current_class is not None:
            if self.current_function is not None and self.current_function.containing_class is not None:
                return f"{self.current_function.qualified_name}.{name}"
            return f"{self.current_class.qualified_name}.{name}"
        if self.current_function is not None:
            return f"{self.current_function.qualified_name}.{name}"
        if self.module_name:
            return f"{self.module_name}.{name}"
        return name

    def _record_symbol(self, symbol: Symbol) -> None:
        self.symbols.append(symbol)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "os":
                self._module_aliases[alias.asname or "os"] = "os"
            self.imports.append(
                ImportInfo(
                    type="import",
                    module=alias.name,
                    symbol=None,
                    alias=alias.asname,
                    level=0,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_base = node.module or ""
        module_path = f"{'.' * node.level}{module_base}" if node.level else module_base
        for alias in node.names:
            if node.level == 0 and node.module == "os" and alias.name in {"getenv", "environ"}:
                self._imported_os_names[alias.asname or alias.name] = alias.name
            self.imports.append(
                ImportInfo(
                    type="from_import",
                    module=module_path,
                    symbol=alias.name,
                    alias=alias.asname,
                    level=node.level,
                    line=node.lineno,
                )
            )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_name = node.name
        parent_name = self.current_class.qualified_name if self.current_class is not None else self.module_name
        qualified_name = f"{parent_name}.{class_name}" if parent_name else class_name
        if self.current_class is None and self.current_function is None:
            qualified_name = f"{self.module_name}.{class_name}" if self.module_name else class_name

        class_info = ClassInfo(
            name=class_name,
            qualified_name=qualified_name,
            line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            bases=[ast.unparse(base) for base in node.bases],
            decorators=[self._safe_decorator_expression(d) for d in node.decorator_list],
            is_nested=self.current_class is not None,
        )
        self.classes.append(class_info)
        previous_class = self.current_class
        self.current_class = class_info
        self._record_symbol(
            Symbol(
                id=qualified_name,
                name=class_name,
                qualified_name=qualified_name,
                symbol_type="class",
                file=self.file_path,
                module=self.module_name,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parent=previous_class.qualified_name if previous_class is not None else None,
            )
        )
        for decorator in node.decorator_list:
            target, arguments = self._decorator_parts(decorator)
            expression = self._safe_decorator_expression(decorator, target, arguments)
            self.decorators.append({
                "source": qualified_name,
                "decorator": expression,
                "target": target,
                "arguments": arguments,
                "line": decorator.lineno,
                "type": "class",
                "resolution_status": "unknown",
                "resolved_to": None,
            })
        self._record_declarative_table(node, qualified_name)
        self.generic_visit(node)
        self.current_class = previous_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.AST) -> None:
        name = node.name
        is_method = self.current_class is not None and self.current_function is None
        base_name = self.current_class.qualified_name if self.current_class is not None else self.current_function.qualified_name if self.current_function is not None else self.module_name
        qualified_name = f"{base_name}.{name}" if base_name else name
        if self.current_class is None and self.current_function is None:
            qualified_name = f"{self.module_name}.{name}" if self.module_name else name

        function_info = FunctionInfo(
            name=name,
            qualified_name=qualified_name,
            line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            parameters=[arg.arg for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs],
            return_annotation=ast.unparse(node.returns) if node.returns is not None else None,
            decorators=[self._safe_decorator_expression(d) for d in node.decorator_list],
            is_async=isinstance(node, ast.AsyncFunctionDef),
            containing_class=self.current_class.name if self.current_class is not None else None,
        )
        if self.current_class is not None and self.current_function is None:
            self.current_class.methods.append(function_info)
        else:
            self.functions.append(function_info)

        for decorator in node.decorator_list:
            target, arguments = self._decorator_parts(decorator)
            expression = self._safe_decorator_expression(decorator, target, arguments)
            self.decorators.append({
                "source": qualified_name,
                "decorator": expression,
                "target": target,
                "arguments": arguments,
                "line": decorator.lineno,
                "type": "method" if is_method else "function",
                "resolution_status": "unknown",
                "resolved_to": None,
            })

        previous_function = self.current_function
        self.current_function = function_info
        self._record_symbol(
            Symbol(
                id=qualified_name,
                name=name,
                qualified_name=qualified_name,
                symbol_type="method" if is_method else "function",
                file=self.file_path,
                module=self.module_name,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                parent=self.current_class.qualified_name if self.current_class is not None else None,
            )
        )
        self.generic_visit(node)
        self.current_function = previous_function

    def visit_Call(self, node: ast.Call) -> None:
        scope = self.current_function.qualified_name if self.current_function is not None else self.current_class.qualified_name if self.current_class is not None else self.module_name
        class_name = self.current_class.qualified_name if self.current_class is not None else None
        target = self._call_target(node.func)
        call_info = {
            "caller": scope,
            "target": target,
            "callee": target,
            "resolved_to": None,
            "resolution_status": "unknown",
            "line": node.lineno,
            "column": node.col_offset,
            "expression": self._safe_source_expression(node),
            "scope": scope,
            "class": class_name,
        }
        self.calls.append(call_info)
        self.relationships.append({
            "source": scope,
            "target": target,
            "type": "calls",
            "line": node.lineno,
            "resolution_status": "unknown",
        })
        self._record_environment_call(node)
        self._record_configuration_get_call(node)
        self._record_sql_call(node, scope)
        self._record_orm_query(node, scope)
        self._record_orm_session_write(node, scope)
        self.generic_visit(node)

    def _call_target(self, func: ast.AST) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts: List[str] = []
            current: ast.AST = func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                parts.reverse()
                return ".".join(parts)
        if isinstance(func, ast.Call):
            return self._call_target(func.func)
        return "<unknown>"

    def _safe_source_expression(self, node: ast.AST) -> str:
        safe_node = _StringLiteralRedactor().visit(copy.deepcopy(node))
        return ast.unparse(safe_node)

    def _decorator_parts(self, decorator: ast.AST) -> tuple[str, List[str]]:
        if isinstance(decorator, ast.Call):
            target = ast.unparse(decorator.func)
            sensitive_decorator = any(
                hint in target.upper() for hint in SENSITIVE_HINTS
            )
            arguments = []
            for argument in decorator.args:
                argument_text = ast.unparse(argument)
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    if sensitive_decorator or ("://" in argument.value and "@" in argument.value):
                        argument_text = "'[redacted]'"
                arguments.append(argument_text)
            for keyword in decorator.keywords:
                sensitive_keyword = keyword.arg is not None and any(
                    hint in keyword.arg.upper() for hint in SENSITIVE_HINTS
                )
                value_text = (
                    "'[redacted]'"
                    if sensitive_keyword or sensitive_decorator
                    else ast.unparse(keyword.value)
                )
                if (
                    isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                    and "://" in keyword.value.value
                    and "@" in keyword.value.value
                ):
                    value_text = "'[redacted]'"
                arguments.append(f"{keyword.arg}={value_text}" if keyword.arg else f"**{value_text}")
            return target, arguments
        return ast.unparse(decorator), []

    def _safe_decorator_expression(
        self,
        decorator: ast.AST,
        target: Optional[str] = None,
        arguments: Optional[List[str]] = None,
    ) -> str:
        if isinstance(decorator, ast.Call):
            target = target or ast.unparse(decorator.func)
            arguments = arguments if arguments is not None else []
            return f"{target}({', '.join(arguments)})"
        return ast.unparse(decorator)

    def _scope_name(self) -> str:
        if self.current_function is not None:
            return self.current_function.qualified_name
        if self.current_class is not None:
            return self.current_class.qualified_name
        return self.module_name

    def _record_config_reference(
        self,
        name: str,
        line: int,
        access_type: str,
        confidence: str = "certain",
        reference_type: str = "configuration_key",
    ) -> None:
        reference = {
            "name": name,
            "type": reference_type,
            "access_type": access_type,
            "source_file": self.file_path,
            "line": line,
            "scope": self._scope_name(),
            "confidence": confidence,
            "sensitive": any(hint in name.upper() for hint in SENSITIVE_HINTS),
        }
        if reference not in self.configuration_references:
            self.configuration_references.append(reference)

    def _environment_accessor(self, func: ast.AST) -> Optional[str]:
        if isinstance(func, ast.Attribute) and func.attr == "get":
            receiver = func.value
            if (
                isinstance(receiver, ast.Attribute)
                and receiver.attr == "environ"
                and isinstance(receiver.value, ast.Name)
                and self._module_aliases.get(receiver.value.id) == "os"
            ):
                return "os.environ.get"
        if isinstance(func, ast.Attribute) and func.attr == "getenv":
            if isinstance(func.value, ast.Name) and self._module_aliases.get(func.value.id) == "os":
                return "os.getenv"
        if isinstance(func, ast.Name) and self._imported_os_names.get(func.id) == "getenv":
            return "os.getenv"
        return None

    def _record_environment_call(self, node: ast.Call) -> None:
        accessor = self._environment_accessor(node.func)
        if accessor and node.args and isinstance(node.args[0], ast.Constant):
            name = node.args[0].value
            if isinstance(name, str):
                self._record_config_reference(
                    name,
                    node.lineno,
                    accessor,
                    reference_type="environment_variable",
                )

    def _record_configuration_get_call(self, node: ast.Call) -> None:
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "get" or not node.args:
            return
        if not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            return
        receiver = ast.unparse(node.func.value)
        config_object = self._configuration_object(receiver)
        if config_object is None:
            return
        confidence, access_type = config_object
        self._record_config_reference(
            node.args[0].value,
            node.lineno,
            access_type,
            confidence=confidence,
        )

    def _configuration_object(self, expression: str) -> Optional[tuple[str, str]]:
        if expression.endswith(".config"):
            return "certain", "config.get"
        if expression in {"config", "settings"}:
            return "likely", f"{expression}.get"
        return None

    def visit_Subscript(self, node: ast.Subscript) -> None:
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            value_expression = ast.unparse(node.value)
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
                and isinstance(node.value.value, ast.Name)
                and self._module_aliases.get(node.value.value.id) == "os"
            ):
                self._record_config_reference(
                    key.value,
                    node.lineno,
                    "os.environ[]",
                    reference_type="environment_variable",
                )
            elif (
                isinstance(node.value, ast.Name)
                and self._imported_os_names.get(node.value.id) == "environ"
            ):
                self._record_config_reference(
                    key.value,
                    node.lineno,
                    "os.environ[]",
                    reference_type="environment_variable",
                )
            else:
                config_object = self._configuration_object(value_expression)
                if config_object is not None:
                    confidence, access_type = config_object
                    self._record_config_reference(
                        key.value,
                        node.lineno,
                        f"{access_type}[]",
                        confidence=confidence,
                    )
        self.generic_visit(node)

    def _likely_configuration_name(self, name: str) -> bool:
        upper_name = name.upper()
        return upper_name in CONFIG_NAMES or any(hint in upper_name for hint in CONFIG_HINTS)

    def _safe_assignment_value(self, target: ast.AST, value: ast.AST) -> Optional[str]:
        target_name = ast.unparse(target)
        simple_name = target.id if isinstance(target, ast.Name) else target_name
        value_source = ast.unparse(value)

        sensitive_target = any(hint in simple_name.upper() for hint in SENSITIVE_HINTS)
        sensitive_content = any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and (
                any(hint in node.value.upper() for hint in SENSITIVE_HINTS)
                or ("://" in node.value and "@" in node.value)
            )
            for node in ast.walk(value)
        )
        if self._likely_configuration_name(simple_name) or sensitive_target or sensitive_content:
            return "[redacted]"
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if SQL_OPERATION_RE.match(value.value):
                return "[SQL statement omitted]"
            if "://" in value.value and "@" in value.value:
                return "[redacted]"
        return value_source

    def _record_config_assignment(self, target: ast.AST, line: int) -> None:
        if isinstance(target, ast.Name) and self._likely_configuration_name(target.id):
            self._record_config_reference(
                target.id,
                line,
                "assignment",
                reference_type="configuration_key",
            )
            return
        if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
            if isinstance(target.slice.value, str):
                config_object = self._configuration_object(ast.unparse(target.value))
                if config_object is not None:
                    confidence, _ = config_object
                    self._record_config_reference(
                        target.slice.value,
                        line,
                        "assignment",
                        confidence=confidence,
                    )

    def _record_sql_call(self, node: ast.Call, scope: Optional[str]) -> None:
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "execute" or not node.args:
            return

        sql: Optional[str] = None
        argument = node.args[0]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            sql = argument.value
        elif isinstance(argument, ast.Name):
            sql = self._sql_assignments.get((scope or "", argument.id))
            if sql is None:
                sql = self._sql_assignments.get((self.module_name, argument.id))
        elif (
            isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Name)
            and argument.func.id == "text"
            and argument.args
            and isinstance(argument.args[0], ast.Constant)
            and isinstance(argument.args[0].value, str)
        ):
            sql = argument.args[0].value
        if sql is None:
            return

        operation_match = SQL_OPERATION_RE.match(sql)
        if operation_match is None:
            return
        operation = operation_match.group(1).upper()
        table_matches = SQL_TABLE_RE.findall(sql)
        if operation == "SELECT":
            selected_tables = [
                (keyword, table)
                for keyword, table in table_matches
                if keyword.upper() in {"FROM", "JOIN"}
            ]
        elif operation == "INSERT":
            selected_tables = [
                (keyword, table)
                for keyword, table in table_matches
                if keyword.upper() == "INTO"
            ][:1]
        elif operation == "UPDATE":
            selected_tables = [
                (keyword, table)
                for keyword, table in table_matches
                if keyword.upper() == "UPDATE"
            ][:1]
        else:
            selected_tables = [
                (keyword, table)
                for keyword, table in table_matches
                if keyword.upper() == "FROM"
            ][:1]

        tables: List[str] = []
        for _, table in selected_tables:
            normalized_table = table.split(".")[-1].strip("`\"[]")
            if normalized_table.lower() not in {"select", "values"} and normalized_table not in tables:
                tables.append(normalized_table)

        for table in tables:
            self.database_references.append({
                "operation": operation,
                "table": table,
                "line": node.lineno,
                "source_file": self.file_path,
                "scope": scope,
                "confidence": "certain",
                "api": ast.unparse(node.func),
            })

    def _record_orm_query(self, node: ast.Call, scope: Optional[str]) -> None:
        if not isinstance(node.func, ast.Attribute):
            return
        expression = ast.unparse(node.func)
        match = re.match(r"^([A-Za-z_]\w*)\.query(?:\.|$)", expression)
        if match is None:
            return
        model = match.group(1)
        if node.args and isinstance(node.args[0], ast.Name):
            model = node.args[0].id
        self.database_references.append({
            "operation": "SELECT",
            "table": None,
            "model": model,
            "line": node.lineno,
            "source_file": self.file_path,
            "scope": scope,
            "confidence": "likely",
            "api": "sqlalchemy_query",
        })

    def _record_orm_session_write(self, node: ast.Call, scope: Optional[str]) -> None:
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in {"add", "delete"}:
            return
        receiver = ast.unparse(node.func.value)
        if not self._is_session_receiver(receiver):
            return
        if not node.args or not isinstance(node.args[0], ast.Name):
            return

        variable_name = node.args[0].id
        model_name = self._instance_types.get((scope or "", variable_name))
        if model_name is None:
            model_name = self._instance_types.get((self.module_name, variable_name))
        if model_name is None:
            return
        self.database_references.append({
            "operation": "INSERT" if node.func.attr == "add" else "DELETE",
            "table": None,
            "model": model_name,
            "line": node.lineno,
            "source_file": self.file_path,
            "scope": scope,
            "confidence": "likely",
            "api": f"sqlalchemy_session.{node.func.attr}",
        })

    def _is_session_constructor(self, func: ast.AST) -> bool:
        name = func.id if isinstance(func, ast.Name) else ast.unparse(func)
        return name in {"Session", "sessionmaker", "scoped_session", "AsyncSession"}

    def _is_session_receiver(self, receiver: str) -> bool:
        if receiver == "session" or receiver.endswith(".session"):
            return True
        return receiver.split(".")[-1] in self._session_names

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            optional_vars = item.optional_vars
            if (
                isinstance(optional_vars, ast.Name)
                and isinstance(item.context_expr, ast.Call)
                and self._is_session_constructor(item.context_expr.func)
            ):
                self._session_names.add(optional_vars.id)
        self.generic_visit(node)

    def _record_declarative_table(self, node: ast.ClassDef, qualified_name: str) -> None:
        for statement in node.body:
            target: Optional[ast.AST] = None
            value: Optional[ast.AST] = None
            line = statement.lineno
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                target = statement.targets[0]
                value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                target = statement.target
                value = statement.value
            if not isinstance(target, ast.Name) or target.id != "__tablename__":
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value:
                self.database_references.append({
                    "operation": "DEFINE",
                    "table": value.value,
                    "model": qualified_name,
                    "line": line,
                    "source_file": self.file_path,
                    "scope": qualified_name,
                    "confidence": "certain",
                    "api": "sqlalchemy_declarative",
                })

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            scope = self.current_function.qualified_name if self.current_function else self.current_class.qualified_name if self.current_class else self.module_name
            self._record_config_assignment(target, node.lineno)
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
            ):
                self._instance_types[(scope, target.id)] = node.value.func.id
                if self._is_session_constructor(node.value.func):
                    self._session_names.add(target.id)
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and SQL_OPERATION_RE.match(node.value.value)
            ):
                self._sql_assignments[(scope, target.id)] = node.value.value
            self.assignments.append({
                "target": ast.unparse(target),
                "value": self._safe_assignment_value(target, node.value),
                "line": node.lineno,
                "scope": scope,
            })
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        scope = self.current_function.qualified_name if self.current_function else self.current_class.qualified_name if self.current_class else self.module_name
        self._record_config_assignment(node.target, node.lineno)
        if (
            isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and SQL_OPERATION_RE.match(node.value.value)
        ):
            self._sql_assignments[(scope, node.target.id)] = node.value.value
        self.assignments.append({
            "target": ast.unparse(node.target),
            "value": self._safe_assignment_value(node.target, node.value) if node.value is not None else None,
            "line": node.lineno,
            "scope": scope,
        })
        self.generic_visit(node)

    def build(self) -> Dict[str, Any]:
        return {
            "imports": self.imports,
            "classes": [item.to_dict() for item in self.classes],
            "functions": [item.to_dict() for item in self.functions],
            "calls": self.calls,
            "assignments": self.assignments,
            "decorators": self.decorators,
            "configuration_references": self.configuration_references,
            "database_references": self.database_references,
            "relationships": self.relationships,
            "symbols": [item.to_dict() for item in self.symbols],
        }
