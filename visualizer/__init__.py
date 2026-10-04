"""Browser-based 3D graph viewer for the Codebase Intelligence graph.

This package is a *consumer* of the SQLite graph. It contains no analysis,
resolution, or parsing logic; it only reads bounded neighborhoods through
the existing ``GraphStore`` query layer and renders them.
"""
