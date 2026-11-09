# 12 — F12 SVG site plan: reviewer decision (Tier 1)

Fresh reviewer. I read `11_f12_code_writer_response.md`, the F6 docs (08–10) and the
diff `b40f669..95af8bf`, then ran checks myself.

## Verdict: GO, with the fixes below

The leak rule holds. `plan.render` is called only in `report.write_all`, after
scores, flags, the sort and `attach_fits`. It writes nothing on `p`. The spies
(slope on and `--no-slope`), the CSV and geojson identity test with the plan off,
and the static backstop are all sound. The SVG never reads the owner or any
address. It uses the standard library plus shapely, so no new dependency. No
root file was touched.

## Fixed in this step

1. **Legend text ran off the 640 px canvas.** A long envelope line (about 95
   characters) or a not-checked reason (up to about 120) was clipped. Legend rows
   now wrap at 84 characters, and the SVG height follows the line count. The new
   test checks the wrap and the height.
2. **The scale bar was drawn over the parcel.** It sat inside the plan square,
   where the bottom edge of the parcel is drawn. It now sits in a 24 px band
   below the square (tested). The north arrow got a white backdrop, so it stays
   readable over the drawing.
3. **"Largest open part" acres could disagree with the brief.** The plan
   measured the area in feet, while sitefit measures it in metres. It now uses
   the same metres projection and `geo.acres`. A test checks that it matches
   `largest_open_acres`.
4. **Control characters in a parcel id broke the XML.** `html.escape` keeps
   characters XML 1.0 forbids, such as `\x01` and lone surrogates. They are now
   replaced with `?` (tested).
5. **A stale `.svg` could survive a rerun into the same `--out`.** That left a
   plan beside a brief that says "Not drawn". `write_all` now removes it
   (tested).
6. The SVG is written as `encoding="utf-8"` explicitly instead of the locale
   default, because the file has no XML declaration.
7. The envelope label's x position is clamped so the text stays on the canvas.

The pinned hash changed because of these fixes, and I re-pinned it. The pin
depends on the GEOS version: after a shapely upgrade, re-pin the hash only if the
structure tests still pass.

Screener suite: 316 tests OK. No live run.
