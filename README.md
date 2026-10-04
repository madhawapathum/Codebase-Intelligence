# Codebase Intelligence Backend

This project provides a Python repository analysis backend that parses Python source with the standard-library `ast` module and builds a structured codebase graph.

## Current architecture

```text
Source files
  ↓
Parser
  ↓
AST visitor
  ↓
Pass 1: symbol discovery
  ↓
Pass 2: call and reference resolution
  ↓
Relationship builder
  ↓
Repository graph model
  ↓
JSON serialization
```

The important design idea is multi-pass analysis:

1. Discover files, packages, imports, classes, methods, functions, and symbols.
2. Build a stable repository symbol universe.
3. Resolve calls and inheritance against that symbol universe.
4. Preserve unresolved or ambiguous references explicitly instead of guessing.

## Multi-pass analysis

The backend intentionally separates discovery from resolution.

### Pass 1: discovery

- file discovery under a repository root
- module naming from file paths and package boundaries
- imports and aliases
- classes, methods, functions, nested scopes
- symbol identities with qualified names

### Pass 2: resolution

- function and method call resolution
- cross-file import and alias resolution
- import-to-symbol resolution where the source is known
- inheritance analysis
- explicit `unresolved`, `ambiguous`, and `unknown` states

## Symbol identities

The backend uses stable, fully qualified names such as:

- `auth.service`
- `auth.service.UserService`
- `auth.service.UserService.get_user`
- `auth.service.authenticate`

This avoids collisions caused by short names like `User` or `save` appearing in multiple modules.

## Cross-file resolution

The resolver works against the full repository symbol universe. This means calls can be resolved even when the target is defined later in the same file or in another module.

Example:

```python
# module_a.py

def hello():
    return "hi"

# module_b.py
from module_a import hello

def run():
    return hello()
```

The resulting relationship is approximately:

```json
{
  "source": "module_b.run",
  "target": "module_a.hello",
  "type": "calls",
  "resolution_status": "resolved"
}
```

## Inheritance detection

Class bases are also resolved when possible:

```python
class User:
    pass

class AdminUser(User):
    pass
```

This yields an `inherits` relationship:

```json
{
  "source": "module.AdminUser",
  "target": "module.User",
  "type": "inherits",
  "resolution_status": "resolved"
}
```

## Resolution states

The backend keeps resolution states explicit:

- `resolved`
- `ambiguous`
- `unresolved`
- `unknown`

This is important because static analysis is conservative by design.

## Decorator analysis

Decorators on classes, functions, and methods produce `decorated_by` relationships. The
relationship retains the source expression, syntactic target, and source-form arguments
without evaluating or executing the decorator. A decorator is marked `resolved` only
when the existing repository symbol and import analysis identifies its target; otherwise
it remains `unresolved` or `unknown`.

For example, `@app.route("/users", methods=["GET"])` keeps `app.route` as its target
and stores the argument expressions as strings.

## Test discovery and test relationships

Files named `test_*.py` or `*_test.py` are marked with `is_test_file`. Files with
pytest-style top-level `test_` functions or unittest-style `Test*`/`TestCase` classes
with `test_` methods are also recognized from their AST structure.

Recognized test functions and methods remain ordinary symbols with additional `kind`
and `is_test` metadata. `tests` relationships are conservative: the analyzer creates
one when a recognized test directly calls a repository function or class imported into
that test module and the target resolves. A call by itself is not assumed to be the
thing under test. Calls on constructed objects and unresolved/external imports do not
produce a `tests` relationship unless the target can be established.

## Database and Configuration Analysis

The analyzer statically extracts common environment reads (`os.getenv`,
`os.environ.get`, and `os.environ[...]`), known configuration mapping accesses such
as `app.config[...]` and likely generic `config`/`settings` lookups, and module-level
assignments with common configuration names. Configuration relationships use
`reads_config` and `writes_config`. Sensitive values and configuration assignment
values are not included in the JSON; only names and access metadata are emitted.

For SQL, literal statements passed to `.execute(...)` and simple SQL strings assigned
to a variable before execution are recognized for `SELECT`, `INSERT`, `UPDATE`, and
`DELETE`. `FROM`, `JOIN`, `INTO`, and `UPDATE` clauses provide likely table names.
These produce `queries`, `reads_table`, and `writes_table` relationships to stable
nodes such as `db.table.users`. Obvious `Model.query...` chains produce a likely
`queries` relationship to a resolved model symbol where possible, without inferring
a database table name from the model class.

SQLAlchemy declarative models whose `__tablename__` is a static string literal are
recognized as well: `class User(Base): __tablename__ = "users"` produces a
`defines_table` relationship from `models.User` to `db.table.users` and a
`database_objects` row. Dynamic table names are left unknown rather than guessed.

This is static analysis and does not execute application code, import modules, connect
to databases, or evaluate SQL expressions. Dynamic SQL, wrapped SQL expressions,
unknown `.execute(...)` receivers, and runtime ORM/table naming may remain unknown or
undetected. Table extraction is intentionally lightweight rather than a complete SQL
parser.

## CLI usage

```bash
python -m codebase_intelligence ./project --output codebase.json --pretty
```

Test files in directories named `test` or `tests` are excluded by default by the CLI.
Use `--include-tests` when you want to analyze those directories:

```bash
python -m codebase_intelligence ./project --include-tests --output codebase.json --pretty
```

or:

```bash
python analyzer.py ./project --output codebase.json --pretty
```

## SQLite Graph Storage and Queries

SQLite can store a replaceable snapshot of the analyzed graph. The AST analysis is
still performed first; the storage layer persists files, modules, imports, symbols,
relationships, configuration references, and database objects separately, with
indexes for common lookups. Qualified symbol names and database object IDs remain
stable graph identifiers.

The SQLite schema uses `repositories`, `files`, `modules`, `imports`, `symbols`,
`relationships`, `config_references`, and `database_objects`. Files/modules use
repository-scoped internal integer keys; symbol IDs and database object IDs retain
their stable qualified graph names.

Store a graph without producing the large JSON export:

```powershell
python analyzer.py "D:\Projects\MyApp" --database ".\myapp.db"
```

Store both representations:

```powershell
python analyzer.py "D:\Projects\MyApp" --database ".\myapp.db" --output ".\myapp_map.json" --pretty
```

Query a database containing one repository (or pass its repository root as the
positional argument if the database contains more than one):

```powershell
python analyzer.py --database ".\myapp.db" --summary
python analyzer.py --database ".\myapp.db" --symbol "app.create_user"
python analyzer.py --database ".\myapp.db" --module "app"
python analyzer.py --database ".\myapp.db" --search "create_user" --limit 20
python analyzer.py --database ".\myapp.db" --relationship-type "reads_table"
python analyzer.py --database ".\myapp.db" --neighbors "app.create_user" --depth 2
```

The Python API is available through `GraphStore`, including summary, symbol/file/
module lookup, symbol search, relationship lookup, callers, callees, imports, tests,
configuration/database relationships, and a bounded neighborhood query. Result
limits default to 100 and are capped at 400; neighborhood depth defaults to 1 and
cannot exceed 3 unless the API caller explicitly configures a different maximum.
`with GraphStore(path) as store:` closes the SQLite connection safely.

The summary query returns only aggregate counts. Neighborhood queries return only
nodes and relationships adjacent to the requested node through the requested depth,
not the entire graph. Re-analyzing the same canonical repository root replaces its
stored snapshot in a transaction instead of duplicating records. JSON export remains
available as a snapshot/debug format.

## Example JSON

```json
{
  "repository": {
    "root": "examples/valid_demo",
    "file_count": 3,
    "module_count": 3
  },
  "edges": [
    {
      "source": "app.service.authenticate",
      "target": "validators.validate",
      "type": "calls",
      "resolution_status": "resolved"
    }
  ],
  "errors": []
}
```

## Test commands

```bash
.\venv\Scripts\python.exe -m pytest -q
```

## Limitations of static Python analysis

- dynamic runtime behaviors cannot always be proven
- metaprogramming and import-time side effects are not executed
- some attribute chains are intentionally treated as `unknown`
- test relationships are limited to direct, resolved imports and calls; inferred coverage is not claimed
- database/configuration extraction supports common static patterns only; dynamic behavior is not inferred

## AI retrieval layer

`codebase_intelligence.retrieval.Retriever` sits above `GraphStore` and produces
compact, provenance-bearing context packages for an AI agent. Available tools:
`search`, `get_symbol`, `get_source`, `get_neighborhood`, `get_callers`,
`get_callees`, `get_tests`, `get_database_context`, `get_config_context`, and the
combined `get_context`.

```powershell
python analyzer.py --database ".\myapp.db" --search "user"
python analyzer.py --database ".\myapp.db" --context "app.create_user"
python analyzer.py --database ".\myapp.db" --source "app.create_user"
python analyzer.py --database ".\myapp.db" --callers "app.create_user"
python analyzer.py --database ".\myapp.db" --callees "app.create_user"
python analyzer.py --database ".\myapp.db" --tests "app.create_user"
python analyzer.py --database ".\myapp.db" --database-context "app.create_user"
python analyzer.py --database ".\myapp.db" --config-context "app.create_user"
```

Results are bounded by a configurable `RetrievalLimits` budget and retain provenance
(file, line, confidence, resolution). Embeddings, vector databases, and RAG are not
used yet; the retrieval layer returns structured facts and source context.

## 3D viewer

A small Three.js graph viewer lives in `visualizer/`. Start it with:

```powershell
python visualizer\server.py
```

then open `http://127.0.0.1:8000`. See `visualizer/README.md` for details. The
viewer is a read-only consumer of the SQLite graph.

## Recommended next phase

The next logical phase is an LLM integration layer on top of the retrieval layer,
plus broader validation of the static relationship model. The viewer and retrieval
layer remain consumers of the SQLite graph.
