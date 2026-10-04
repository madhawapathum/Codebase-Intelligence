import ast

from codebase_intelligence.visitor import ModuleVisitor


def test_imports_are_recorded():
    source = "from pathlib import Path\nimport os\nfrom package.module import function as f\n"
    tree = ast.parse(source)
    visitor = ModuleVisitor("demo.py", "demo")
    visitor.visit(tree)

    assert any(item.module == "os" for item in visitor.imports if item.type == "import")
    assert any(item.module == "package.module" and item.symbol == "function" for item in visitor.imports if item.type == "from_import")
