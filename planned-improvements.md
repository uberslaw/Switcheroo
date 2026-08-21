# Planned improvements

What is deliberately **not** in Rack Design v1, grouped so the next piece of work can be picked off the top. v1 is: import the Albert St workbook, render front/back elevations with document RU numbering, and place/move/edit gear from a catalog.

## Verify before building anything else

- **Look at the rendered elevations in a browser.** The tests assert status codes, RU ordering, and permission gates — they do not assert that the layout *looks* right. The RU column uses `rowspan` for multi-U gear with vertical PDUs floated beside it, which is the most likely thing to look wrong on first sight (a tall UPS or a NetApp pair mis-spanning). Compare `/racks/sites/1` against the workbook sheet side by side before trusting the elevations.

## Known gaps in v1 (small, sharp)

- **Vertical PDUs are read-only.** They import from the FDR sheets and render beside the RU column, but the edit list filters to RU-mounted items (`app/templates/racks/elevation.html`), so a side PDU cannot be renamed, moved between left/right, or removed from the UI. The service layer already supports the mount type.
- **Moving gear front↔back is not exposed.** `move_item` accepts a face, but the move form posts the face you are currently viewing, so a front item can only be moved within the front. Needs a face selector on the move form.
- **Catalog entries can be added but not edited or removed.** A typo in an item type name is permanent from the UI. Deleting needs a guard for types that are already placed (`RackItem.item_type_id` is `ondelete=RESTRICT`).
- **Racks cannot be reordered.** `Rack.sort_order` decides left-to-right position and is set on create; there is no UI to shuffle racks within a room.
- **Sites cannot be renamed or deleted.** Only created.
- **Air gaps are implicit.** An empty RU renders as a dashed cell. There is no explicit "air gap" item you can name or annotate, which the Albert St sheets sometimes do want (deliberate thermal gaps versus merely unused RU).

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
