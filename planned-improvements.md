# Planned improvements

Ideas parked so v1 Rack Design stays focused on editable elevations.

## Rack Design (next)

- **Cable / power tracing** — draw front↔back and rack-to-rack paths; reuse or extend `CablePath` for elevation endpoints (ports, PDUs, patch panels).
- **Photos / drawings** — attach silhouettes or photos per catalog item type; optional per-instance override.
- **Device registry (CMDB-lite)** — fuller asset fields (serial, asset tag, warranty, owner) linked from placed items; keep catalog types as the pick-and-place layer.
- **Live Switcheroo linkage** — click a placed Catalyst row to open the existing faceplate / stack page when a matching `Switch` exists.
- **Drag-and-drop** — HTMX or light JS move by dragging RU cells instead of form move.
- **Multi-site import** — generalize the Albert St XLSX parser for other office workbooks.
- **Print / PDF elevation** — zoomed-out printable sheets matching the move-in document layout.

## Permissions

- Per-site or per-rack edit grants (today capabilities are global per user).
- Audit log of layout moves (who moved what RU when).
