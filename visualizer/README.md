# Codebase Intelligence — 3D Graph Viewer

A minimal browser-based 3D viewer for the Codebase Intelligence SQLite graph.

The viewer is a **consumer** of the graph. It does not analyze or resolve code;
it only requests bounded graph neighborhoods through the existing `GraphStore`
query layer and renders them with Three.js.

## Architecture

```
Codebase Analyzer  →  SQLite Graph  →  Query / Retrieval  →  3D Viewer
```

The SQLite database (`examples/asset_mng_graph.db`) remains the single source
of truth. The viewer never loads the whole graph — it always fetches a bounded
neighborhood (default depth 1) around a chosen root symbol.

## Requirements

- Python 3.9+ (the project venv).
- A browser with internet access the first time the page loads, because
  Three.js is loaded from a CDN (unpkg, pinned to `three@0.160.0`).

## Run it

From the project root (`D:\New folder\Code_map`):

1. **Analyze the repository** (optional — already done for the Asset project):

   ```powershell
   .\venv\Scripts\python.exe analyzer.py "D:\edu\Asset .Mng" --database "examples\asset_mng_graph.db"
   ```

2. **Start the viewer server**:

   ```powershell
   .\venv\Scripts\python.exe visualizer\server.py
   ```

   Options:

   ```powershell
   .\venv\Scripts\python.exe visualizer\server.py --db examples\asset_mng_graph.db --port 8000
   ```

3. **Open the browser**:

   ```
   http://127.0.0.1:8000
   ```

4. **Enter a symbol** (e.g. `models.User`) and click **Load**.

5. The bounded neighborhood is rendered. Drag to rotate, scroll to zoom,
   right-drag to pan, click a node or edge to inspect its metadata.

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/repositories` | List stored repository roots. |
| `GET /api/summary?root=…` | Aggregate counts for a repository. |
| `GET /api/graph?symbol=models.User&depth=1` | Bounded neighborhood as `{nodes, relationships}`. |
| `GET /api/symbol?name=models.User` | Symbol metadata. |
| `GET /api/search?q=User` | Substring symbol search (for autocomplete). |

`depth` is bounded to `0..3` and `limit` to `1..400`; an unknown repository
root returns 404, and invalid parameters return 400. All queries are
parameterized GraphStore calls — no arbitrary SQL, no filesystem access, no
execution of analyzed code.

## Files

```
visualizer/
├── server.py      # stdlib HTTP server + read-only JSON API over GraphStore
├── index.html     # page layout + Three.js import map
├── app.js         # Three.js scene, force layout, selection, details panel
├── style.css      # styling
└── README.md
```

## Notes / limitations (Phase 1)

- Only a bounded neighborhood (depth 1–3) is rendered; the full graph is never
  loaded.
- The 3D layout is a simple deterministic force-directed simulation (no
  clustering or advanced layout yet).
- Relationship labels appear in the details panel when you select an edge or a
  node (edges are also pickable by clicking near them).
- The viewer requires the analyzed graph to exist in SQLite first.
