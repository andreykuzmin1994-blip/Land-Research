"""Export Fulton County 2024 orthophotos for parcels, with the parcel outline
(red) drawn on. Usage: python3 aerials.py RUN_DIR OUT_DIR rank,rank,...
One image per parcel: OUT_DIR/<rank>.jpg (1000 px, 10% margin)."""
import io, json, sys, time
from pathlib import Path
import requests
from PIL import Image, ImageDraw
from shapely.geometry import shape

RUN, OUT = Path(sys.argv[1]), Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
ranks = {int(x) for x in sys.argv[3].split(",")}
EXPORT = "https://gismaps.fultoncountyga.gov/arcgispub2/rest/services/Imagery/Pictometry2024_NAIP/MapServer/export"
UA = {"User-Agent": "land-screener/0.1 (internal land-acquisition research)"}
feats = [f for f in json.load(open(RUN / "ranked.geojson"))["features"] if int(f["properties"]["rank"]) in ranks]
for f in feats:
    rank = int(f["properties"]["rank"]); out = OUT / f"{rank}.jpg"
    if out.exists():
        continue
    g = shape(f["geometry"])
    x0, y0, x1, y1 = g.bounds
    import math
    kx = math.cos(math.radians((y0 + y1) / 2))          # make the box square on the ground
    w, h = (x1 - x0) * kx, (y1 - y0)
    side = max(w, h) * 1.2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bx0, bx1 = cx - side / 2 / kx, cx + side / 2 / kx
    by0, by1 = cy - side / 2, cy + side / 2
    params = {"bbox": f"{bx0},{by0},{bx1},{by1}", "bboxSR": 4326, "imageSR": 4326, "size": "1000,1000",
              "format": "jpg", "f": "image"}
    for attempt in range(4):
        try:
            r = requests.get(EXPORT, params=params, headers=UA, timeout=90)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            break
        except Exception as exc:
            time.sleep(2 ** attempt)
    else:
        print("failed", rank); continue
    W, H = img.size
    d = ImageDraw.Draw(img)
    px = lambda x, y: ((x - bx0) / (bx1 - bx0) * W, (by1 - y) / (by1 - by0) * H)
    parts = [g] if g.geom_type == "Polygon" else list(g.geoms)
    for p in parts:
        for ring in [p.exterior, *p.interiors]:
            d.line([px(x, y) for x, y in ring.coords], fill=(255, 40, 40), width=4)
    # scale bar: 200 ft
    ft_per_px = side * 364000 / W
    bar = 200 / ft_per_px
    d.rectangle([20, H - 40, 20 + bar, H - 32], fill=(255, 255, 255)); d.text((24 + bar, H - 46), "200 ft", fill=(255, 255, 255))
    img.save(out, quality=82)
    time.sleep(1.0)
    print("ok", rank, flush=True)
