# Planned improvements

What is deliberately **not** in Rack Design v1, grouped so the next piece of work can be picked off the top. v1 is: import the Albert St workbook, render front/back elevations with document RU numbering, and place/move/edit gear from a catalog.

## Known gaps in v1 (small, sharp)

- **Air gaps are implicit.** An empty RU renders as a dashed cell. There is no explicit "air gap" item you can name or annotate, which the Albert St sheets sometimes do want (deliberate thermal gaps versus merely unused RU).
- **No drag-and-drop.** A move is a form with a target top RU. Dragging an RU cell would read closer to how people use an elevation.
- **Re-import is all or nothing.** "Re-import from workbook" rebuilds a whole site and discards layout edits. There is no per-rack re-import and no diff/preview of what would change.

## Done since the first pass

Verified in a browser, not just by tests:

- RU rows are a fixed height, so labels stay aligned and side-by-side racks share one RU baseline. Previously racks drifted up to ~100px out of step, because each rack is its own table and row height followed cell content.
- Imported blanking, spare, shelves, cable management and reserve rows stay 1 RU each. They used to merge into a single 27U blank, so placing one server meant deleting the whole block.
- Vertical PDUs are editable — rename, swap rail, remove — and can never take an RU slot.
- Moves take an explicit face, and no longer silently flip an item's face to whichever side you were viewing.
- Catalog types can be renamed, retuned and deleted, with deletion refused while instances exist.
- Racks reorder left to right; sites rename, and delete only once empty.

## Phase 2 (bigger, previously agreed)

- **Cable and power tracing.** The original ask. Draw front↔back and rack-to-rack paths, and trace power from gear to PDU to UPS. `CablePath` already exists for the patching side and would need endpoints that can address a rack item or a PDU outlet, not just switch ports and panel jacks.
- **Device registry (CMDB-lite).** Serial, asset tag, warranty, owner, lifecycle state per placed instance, with catalog types staying as the pick-and-place layer.
- **Photos and drawings.** Per item type, with an optional per-instance override. Today categories only carry a silhouette style that drives a colour.
- **Live linkage to Switcheroo inventory.** A placed Catalyst row should open its existing faceplate when a `Switch` with that name or management IP exists.
- **Drag-and-drop moves.** Today a move is a form with a target top RU. Dragging an RU cell would be closer to how people read an elevation.
- **Multi-site import.** `app/services/rack_import.py` hardcodes the Albert St sheet names and column positions. Other office workbooks need either a column-mapping UI or a per-workbook profile.
- **Print / PDF elevation.** A zoomed-out printable sheet matching the move-in document, so Rack Design can replace the spreadsheet rather than mirror it.

## Permissions and audit

- **Per-site or per-rack edit grants.** Capabilities are global per user today: `rack_edit_layout` lets you edit every rack at every site.
- **Audit log of layout changes.** Who moved what RU, when. The change-request tables cover switch writes, not layout edits.

## Settled decisions (not gaps)

- RU numbering follows the document: high RU at the top, RU 1 at the bottom.
- Rack Design lives inside Switcheroo, behind the same login.
- Rack Design is separate from the Brisbane switch-stack view; it does not replace `Stacks` or `Patching`.
- The imported Albert St layout is a starting point and is meant to be edited, not treated as read-only truth.
