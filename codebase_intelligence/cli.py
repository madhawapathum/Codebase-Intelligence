from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .repository import Repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze a Python repository for code structure and relationships.")
    parser.add_argument("root", nargs="?", help="Repository path to analyze or query")
    parser.add_argument("--output", "-o", help="Write JSON output to this file")
    parser.add_argument("--database", help="Persist/query the SQLite graph at this path")
    query_group = parser.add_mutually_exclusive_group()
    query_group.add_argument("--summary", action="store_true", help="Print stored repository summary")
    query_group.add_argument("--symbol", help="Look up a stored symbol by qualified name")
    query_group.add_argument("--module", help="Look up a stored module by name")
    query_group.add_argument("--neighbors", help="Print a stored graph neighborhood")
    query_group.add_argument("--search", help="Search stored symbols by name, module, or file")
    query_group.add_argument("--relationship-type", help="Search stored relationships by type")
    query_group.add_argument("--context", metavar="SYMBOL", help="Retrieve an AI-oriented context package for a symbol")
    query_group.add_argument("--source", metavar="SYMBOL", help="Retrieve the source region of a symbol")
    query_group.add_argument("--callers", metavar="SYMBOL", help="List callers of a symbol")
    query_group.add_argument("--callees", metavar="SYMBOL", help="List callees of a symbol")
    query_group.add_argument("--tests", metavar="SYMBOL", help="List tests associated with a symbol")
    query_group.add_argument("--database-context", metavar="SYMBOL", help="Retrieve database relationships for a symbol")
    query_group.add_argument("--config-context", metavar="SYMBOL", help="Retrieve configuration references for a symbol")
    parser.add_argument("--tool-list", action="store_true", help="List available AI tool definitions")
    parser.add_argument("--tool", metavar="NAME", help="Execute an AI tool (requires --database and --arguments)")
    parser.add_argument("--arguments", metavar="JSON", default="{}", help="JSON object of arguments for --tool")
    parser.add_argument("--depth", type=int, default=1, help="Neighborhood depth (maximum 3)")
    parser.add_argument("--limit", type=int, default=100, help="Maximum query results")
    parser.add_argument("--exclude", action="append", default=[], help="Directory names to exclude")
    parser.add_argument("--include-tests", action="store_true", help="Keep test directories during analysis")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--verbose", action="store_true", help="Print verbose output")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.tool_list:
        from .serializer import to_json
        from .tools import get_tool_definitions

        print(to_json({"tools": get_tool_definitions()}, pretty=True))
        return 0

    if args.tool:
        if not args.database:
            parser.error("--tool requires --database")
        from .serializer import to_json
        from .tools import CodebaseTools

        try:
            arguments = json.loads(args.arguments) if args.arguments else {}
        except ValueError as exc:
            print(f"error: invalid --arguments JSON: {exc}", file=sys.stderr)
            return 1
        if not isinstance(arguments, dict):
            print("error: --arguments must be a JSON object", file=sys.stderr)
            return 1
        result = CodebaseTools(args.database).call(args.tool, arguments)
        print(to_json(result, pretty=True))
        return 0

    query_requested = any(
        (
            args.summary,
            args.symbol,
            args.module,
            args.neighbors,
            args.search,
            args.relationship_type,
            args.context,
            args.source,
            args.callers,
            args.callees,
            args.tests,
            args.database_context,
            args.config_context,
        )
    )
    if query_requested:
        if not args.database:
            parser.error("stored graph queries require --database")

        retrieval_requested = any(
            (
                args.symbol,
                args.search,
                args.context,
                args.source,
                args.callers,
                args.callees,
                args.tests,
                args.database_context,
                args.config_context,
            )
        )
        if retrieval_requested:
            from .retrieval import Retriever

            retriever = Retriever(args.database)
            try:
                if args.context:
                    result = retriever.get_context(args.context, root=args.root)
                elif args.source:
                    result = retriever.get_source(args.source, root=args.root)
                elif args.callers:
                    result = retriever.get_callers(args.callers, root=args.root)
                elif args.callees:
                    result = retriever.get_callees(args.callees, root=args.root)
                elif args.tests:
                    result = retriever.get_tests(args.tests, root=args.root)
                elif args.database_context:
                    result = retriever.get_database_context(args.database_context, root=args.root)
                elif args.config_context:
                    result = retriever.get_config_context(args.config_context, root=args.root)
                elif args.search:
                    result = retriever.search(args.search, root=args.root)
                else:
                    result = retriever.get_symbol(args.symbol, root=args.root)
            except (LookupError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        else:
            from .graph_store import GraphStore

            with GraphStore(args.database) as store:
                root = args.root
                if root is None:
                    repositories = store.list_repositories()
                    if len(repositories) != 1:
                        parser.error(
                            "provide ROOT when the database contains zero or multiple repositories"
                        )
                    root = repositories[0]
                if args.summary:
                    result = store.get_summary(root)
                elif args.module:
                    result = store.get_module(root, args.module, limit=args.limit)
                elif args.neighbors:
                    result = store.get_neighborhood(
                        root,
                        args.neighbors,
                        depth=args.depth,
                        limit=args.limit,
                    )
                else:
                    result = store.search_relationships(
                        root,
                        args.relationship_type,
                        limit=args.limit,
                    )

        from .serializer import to_json

        print(to_json(result, pretty=True))
        return 0

    if args.root is None:
        parser.error("ROOT is required when analyzing a repository")

    root = Path(args.root)
    exclude = set(args.exclude)
    if not args.include_tests:
        exclude.update({"test", "tests", "__pycache__"})
    repository = Repository(root=root, exclude=exclude)
    model = repository.analyze()
    output = model.to_dict()
    if args.database:
        from .graph_store import GraphStore

        with GraphStore(args.database) as store:
            store.store_repository(model)
    if args.output:
        from .serializer import write_json
        write_json(output, args.output, pretty=args.pretty)
    if args.verbose:
        print(f"Analyzed {len(model.files)} files from {root}")
    if args.database and not args.output:
        summary = {
            "database": str(Path(args.database)),
            **{
                "files": len(model.files),
                "modules": len(model.modules),
                "symbols": len(model.symbols),
                "relationships": len(model.relationships),
            },
        }
        from .serializer import to_json
        print(to_json(summary, pretty=True))
    elif not args.output:
        from .serializer import to_json
        print(to_json(output, pretty=args.pretty))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
