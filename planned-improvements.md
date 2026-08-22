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

## Next up: room geometry and computed cable lengths

The point of this is that cable length stops being typed in and starts being
derived. `CablePath.length_m` already exists but is stubbed — 0.20 m for a patch
cord to the adjacent RU, and `NULL` for horizontal runs, whose docstring still
says they "stay unmeasured". Geometry is what lets us fill those in.

### 1. Rack shell dimensions

`Rack` currently knows only `ru_height`. A shell needs the outside of the
cabinet, because cable enters at the top or bottom, not at the device:

- `width_mm`, `depth_mm` — external footprint, for the plan view and for
  routing around neighbours
- `plinth_mm`, `roof_mm` — dead height below RU1 and above the top RU, so
  external height is `plinth + ru_height × 44.45 + roof`
- `entry` — top, bottom, or both, being where a cable actually leaves the shell

One RU is 44.45 mm, and because RU numbering runs document-style with RU1 at
the bottom, height off the floor for an item is
`plinth_mm + (ru_start − 1) × 44.45`. That single line is what turns an
elevation into a measurable model.

### 2. Room as a real entity

`floor` and `room` are free text on `Rack` today, which cannot carry a ceiling
height or hold rack positions. A plan view needs a `Room`: floor, name,
`width_mm`, `length_mm`, `ceiling_height_mm`, with racks pointing at it and
each rack carrying `pos_x_mm`, `pos_y_mm` and `rotation_deg`. Rotation matters
because it decides which way the front face and its cable entry point.

Migrating the existing free-text rooms (MCR L27, IDF L26, IDF L21) into rows is
the fiddly part, not the geometry.

### 3. Power origin and existing outlets

- `PowerSource` — the pinpoint: label (e.g. `DB-27A`), `x`, `y`, `z`, and a
  kind of ceiling / wall / floor box / busway. Ceiling feeds set `z` to the
  room's ceiling height.
- `PowerOutlet` — label, position, circuit or phase, rated amps, and what it
  currently feeds. This is the "existing outlet connections" list, so a new
  rack can be measured from the nearest real outlet rather than from the board.

### 4. How length is actually computed

Straight-line distance would under-read badly. Cables run rectilinearly along
containment, so the estimate is a sum of segments:

    rise from the device's RU to the rack's cable entry
  + rise from the entry up to tray height (typically just under the ceiling)
  + horizontal run along tray as |dx| + |dy|, not diagonal
  + drop at the far end, down to the target RU or outlet
  + slack: a service loop at each end plus a waste percentage

So the calculator needs a tray height and a slack policy, and it should show
its working per segment. A number with no breakdown is not something anyone
will trust enough to cut cable to.

### 5. The zoom-out view

A top-down plan per room, to scale, showing rack footprints, the power source
pin, outlets, and tray runs — sitting beside the existing front-on elevation
rather than replacing it. Clicking a rack in plan opens its elevation.

### Open questions before building

- Millimetres throughout, and display in metres to 2 dp? (Assumed.)
- Draw containment explicitly as tray runs to route along, or just assume
  rectilinear between points? Explicit tray is more accurate and much more
  data entry.
- Slack policy numbers: service loop per end, and waste percentage.
- Is plan-plus-heights (2.5D) enough, or is a real 3D view wanted? 2.5D covers
  the length maths at a fraction of the effort.

### Also still open from before

- Endpoints that can address a rack item or a PDU outlet, not just switch
  ports and panel jacks, so a traced path can start at real gear.
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
