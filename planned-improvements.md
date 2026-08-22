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

### What the workbook already tells us

Three things are already documented in the Albert St sheets and should be
modelled rather than asked about:

- *"Overhead cable tray going to each rack (for fiber cables)"* — containment
  is overhead and reaches every rack, so tray height is a room property and the
  horizontal leg of any run happens at that height.
- *"the front of the server racks have 1 meter space"* — a clearance rule the
  plan view can validate rather than just draw.
- Named rack-to-rack runs such as *"Inter-rack patch (Server Rack B to Comms
  Rack 24ports) — Rear mounted"*. These are the first real things to measure,
  and the front/rear note matters because the mounted face decides which side
  of the shell the cable leaves from.

### Open questions before building

**1. Units.** Store millimetres as integers throughout, display metres to 2 dp?
Millimetres avoid float drift, and rack and cabinet datasheets are already in
mm. Cable is bought in metres, so the display and any ordering output wants
metres. Two follow-ons: should an ordering figure round up to the next 0.5 m
or whole metre, and does any US-sourced kit need inch entry (a 19" rack is
482.6 mm, 1U is 1.75")? Cheap to settle now, invasive later, because it is the
storage type.

**2. Containment: assumed rectilinear, or explicit tray runs?** Three levels:
assume right-angle routing between two points, which needs no data entry but
ignores where tray actually goes; model every tray segment as a graph and
pathfind along it, which is accurate but means drawing all containment per
room; or model just the overhead spine that the workbook says already reaches
each rack, and assume rectilinear from a rack up to its nearest spine point.
The middle option looks like the sweet spot given the tray note.

**3. Slack policy.** Needs numbers: service loop at each termination (often
0.3–1 m), a waste or contingency percentage (commonly 5–10%), and whether
fibre gets more than copper, since bend radius and coiling demand more. Also
worth deciding now: copper is cut on site, but pre-terminated fibre can only be
bought in fixed stock lengths, so for fibre the useful output is "which stock
length to order", not "how many metres".

**4. 2.5D or true 3D?** Plan view with heights held as numbers (ceiling, tray,
RU position) gets every length calculation exactly right and renders as a
simple to-scale SVG. Real 3D adds a renderer and camera controls, and buys
presentation value but no extra accuracy. Recommend 2.5D, leaving 3D as a
possible viewer later.

**5. Where do room dimensions come from?** Data entry is the main cost here,
not the maths. If floor plans or CAD exist, importing beats typing. If not,
someone measures each room once. Worth knowing before designing the entry
screens.

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
