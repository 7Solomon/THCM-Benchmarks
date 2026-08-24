#!/usr/bin/env python3
"""
plot_digitize.py -- extract data series from a raster plot, locally.

Replaces the browser digitizer as the DEFAULT extraction route. Three techniques,
each validated against a real paper figure before being put here:

  colour   mask on distance to a target RGB          (COST TU1404 Fig. 7, red TP18)
  markers  connected components + hole test          (Bazant 2004 Fig. 5, open circles)
  curve    per-column row tracking, optional envelope (Faria 2006 Fig. 8b, 3 curves)

Workflow -- calibration is a FILE, so it is written once and reused:

  plot_digitize.py axes      crop.png                       # propose a calibration
  plot_digitize.py calibrate crop.png --x 137 0 1002 200 \\
                                      --y 578 0.0 34 -1.6  -o cal.json
  plot_digitize.py series    crop.png -c cal.json --mode colour --colour '#c0392b' -o out.csv
  plot_digitize.py verify    crop.png -c cal.json --csv out.csv -o check.png

`axes` only PROPOSES. Auto-detection of tick ladders is unreliable on real figures --
read its output, then pin the numbers with `calibrate`. Getting this wrong is silent:
every extracted value is affinely wrong and the curve still looks plausible.

`verify` is not optional. It replots the CSV over the crop so the calibration can be
checked by eye (or by an agent with an image tool). A digitization nobody looked at
is a digitization nobody should trust.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

try:
    from PIL import Image, ImageDraw
except ImportError:                                        # pragma: no cover
    sys.exit("plot_digitize needs Pillow: pip install pillow")
try:
    from scipy import ndimage
except ImportError:                                        # pragma: no cover
    sys.exit("plot_digitize needs scipy: pip install scipy")


# ── calibration ──────────────────────────────────────────────────────────────

@dataclass
class Cal:
    """Two reference points per axis, in pixels and in data units.

    Pixel rows increase DOWNWARD, so y_px0 is normally the bottom of the plot and
    y_px1 the top. Inverted value axes (a stress plot with -1.6 at the top) need no
    special handling -- give the values as they are printed and the mapping follows.
    """
    x_px0: float; x_val0: float; x_px1: float; x_val1: float
    y_px0: float; y_val0: float; y_px1: float; y_val1: float
    log_x: bool = False
    log_y: bool = False

    def x(self, px):
        f = (np.asarray(px, float) - self.x_px0) / (self.x_px1 - self.x_px0)
        if self.log_x:
            return 10 ** (np.log10(self.x_val0) + f * (np.log10(self.x_val1) - np.log10(self.x_val0)))
        return self.x_val0 + f * (self.x_val1 - self.x_val0)

    def y(self, px):
        f = (np.asarray(px, float) - self.y_px0) / (self.y_px1 - self.y_px0)
        if self.log_y:
            return 10 ** (np.log10(self.y_val0) + f * (np.log10(self.y_val1) - np.log10(self.y_val0)))
        return self.y_val0 + f * (self.y_val1 - self.y_val0)

    def inv_x(self, val):
        if self.log_x:
            f = (np.log10(val) - np.log10(self.x_val0)) / (np.log10(self.x_val1) - np.log10(self.x_val0))
        else:
            f = (np.asarray(val, float) - self.x_val0) / (self.x_val1 - self.x_val0)
        return self.x_px0 + f * (self.x_px1 - self.x_px0)

    def inv_y(self, val):
        if self.log_y:
            f = (np.log10(val) - np.log10(self.y_val0)) / (np.log10(self.y_val1) - np.log10(self.y_val0))
        else:
            f = (np.asarray(val, float) - self.y_val0) / (self.y_val1 - self.y_val0)
        return self.y_px0 + f * (self.y_px1 - self.y_px0)

    @classmethod
    def load(cls, p):
        return cls(**json.loads(Path(p).read_text(encoding="utf-8")))

    def save(self, p):
        Path(p).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


# ── image helpers ────────────────────────────────────────────────────────────

def load_rgb(path):
    return np.array(Image.open(path).convert("RGB"))


def dark_mask(img, thresh=128):
    return (img[:, :, 0] < thresh) & (img[:, :, 1] < thresh) & (img[:, :, 2] < thresh)


def colour_mask(img, target, tol=60, min_sat=40):
    """Pixels near `target` in RGB, excluding greys.

    `min_sat` is what keeps a red mask from swallowing black axis lines and text:
    a grey pixel has max(RGB) - min(RGB) ~ 0 no matter how dark it is.
    """
    t = np.asarray(target, float)
    d = np.linalg.norm(img.astype(float) - t, axis=2)
    sat = img.max(2).astype(int) - img.min(2).astype(int)
    return (d < tol) & (sat >= min_sat)


def apply_excludes(mask, boxes):
    """Blank out rectangles (legend, title box, axis labels). x0 y0 x1 y1, pixels."""
    for x0, y0, x1, y1 in boxes or []:
        mask[int(y0):int(y1), int(x0):int(x1)] = False
    return mask


def parse_colour(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) == 6:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    parts = [int(p) for p in s.replace(",", " ").split()]
    if len(parts) == 3:
        return tuple(parts)
    raise argparse.ArgumentTypeError(f"cannot parse colour {s!r}: use #RRGGBB or 'R,G,B'")


# ── axis proposal ────────────────────────────────────────────────────────────

def propose_axes(img):
    """Find the plot frame and any tick ladder. PROPOSAL ONLY -- verify by eye."""
    d = dark_mask(img)
    h, w = d.shape
    rows, cols = d.sum(1), d.sum(0)
    # frame candidates: long runs, at least 40% of the span
    row_hits = [int(r) for r in np.argsort(rows)[::-1][:12] if rows[r] > 0.40 * w]
    col_hits = [int(c) for c in np.argsort(cols)[::-1][:12] if cols[c] > 0.40 * h]
    out = {
        "image_size": [int(w), int(h)],
        "horizontal_lines_rows": sorted(row_hits)[:6],
        "vertical_lines_cols": sorted(col_hits)[:6],
    }
    if col_hits and row_hits:
        axis_col, axis_row = min(col_hits), max(row_hits)
        out["likely_origin_px"] = [axis_col, axis_row]
        out["ladder_y_rows"] = _ladder(d[:, max(0, axis_col - 18):axis_col].sum(1))
        out["ladder_x_cols"] = _ladder(d[axis_row + 2:axis_row + 20, :].sum(0))
    return out


def _ladder(profile, min_hits=6):
    """Group runs of hits into tick positions, then report the spacing."""
    idx = [i for i, v in enumerate(profile) if v >= min_hits]
    if not idx:
        return {"positions": [], "spacing": None}
    groups, cur = [], [idx[0]]
    for i in idx[1:]:
        (cur.append(i) if i - cur[-1] <= 4 else (groups.append(cur), cur.clear(), cur.append(i)))
    groups.append(cur)
    pos = [round(sum(g) / len(g), 1) for g in groups if g]
    sp = [round(b - a, 1) for a, b in zip(pos, pos[1:])]
    return {"positions": pos, "spacing": sp,
            "median_spacing": round(float(np.median(sp)), 2) if sp else None}


# ── extraction modes ─────────────────────────────────────────────────────────

def extract_curve(mask, pick="mean", monotonic=None, tol=0.0):
    """One (col, row) per column. `pick`: mean | upper | lower.

    upper = smallest row index = highest on the page. For a value axis that
    increases upward that is the maximum; on an inverted axis it is the minimum.
    Use `verify` rather than reasoning about it.

    `monotonic` ('inc'|'dec', in DATA units after mapping) drops columns that
    reverse -- the cheapest way to reject text and leader lines that touch a curve.
    """
    pts = []
    for c in range(mask.shape[1]):
        rs = np.nonzero(mask[:, c])[0]
        if rs.size == 0:
            continue
        r = {"mean": rs.mean(), "upper": rs.min(), "lower": rs.max()}[pick]
        pts.append((float(c), float(r)))
    return pts, monotonic, tol


def extract_markers(mask, area=(60, 200), side=(9, 22), ring=False, min_hole=12):
    """Connected components that look like plot markers.

    `ring=True` keeps only components enclosing white -- an open circle. That is what
    separates a data marker from a solid line it happens to touch.
    """
    lab, _ = ndimage.label(mask, structure=np.ones((3, 3)))
    pts = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        hgt, wid = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        patch = lab[sl] == i
        sz = int(patch.sum())
        if not (area[0] <= sz <= area[1] and side[0] <= wid <= side[1]
                and side[0] <= hgt <= side[1] and 0.6 <= wid / max(hgt, 1) <= 1.7):
            continue
        if ring:
            hole = int(ndimage.binary_fill_holes(patch).sum()) - sz
            if hole < min_hole:
                continue
        ys, xs = np.nonzero(patch)
        pts.append((float(xs.mean() + sl[1].start), float(ys.mean() + sl[0].start)))
    return sorted(pts)


def dedupe(points, radius=6.0):
    """Collapse detections of the SAME marker.

    A ring found by the component pass and again by the hole pass differs by a
    pixel or two, so a naive round() misses it and the marker is exported twice.
    Keeps the first of each cluster; points are in pixels, so one radius works
    regardless of the axis scaling.
    """
    kept = []
    for x, y in sorted(points):
        if any((x - kx) ** 2 + (y - ky) ** 2 <= radius ** 2 for kx, ky in kept):
            continue
        kept.append((x, y))
    return kept


def find_hidden_markers(mask, area=(60, 200), side=(9, 22), min_area=0):
    """Markers fused with a curve, recovered from the WHITE they enclose.

    An open circle sitting on the line merges into one component, so the component
    test above misses it -- but its interior is still an enclosed white region.
    This recovered 5 of 13 markers on Bazant 2004 Fig. 5.
    """
    holes = ndimage.binary_fill_holes(mask) & ~mask
    lab, _ = ndimage.label(holes)
    pts = []
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        hgt, wid = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        sz = int((lab[sl] == i).sum())
        if sz < min_area:
            continue
        if not (area[0] <= sz <= area[1] and side[0] <= wid <= side[1] and side[0] <= hgt <= side[1]):
            continue
        ys, xs = np.nonzero(lab == i)
        pts.append((float(xs.mean()), float(ys.mean())))
    return sorted(pts)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_axes(a):
    print(json.dumps(propose_axes(load_rgb(a.image)), indent=2))
    print("\n# PROPOSAL ONLY. Check these against the printed tick labels, then pin\n"
          "# them with:  plot_digitize.py calibrate ... --x px v px v --y px v px v",
          file=sys.stderr)


def cmd_calibrate(a):
    cal = Cal(x_px0=a.x[0], x_val0=a.x[1], x_px1=a.x[2], x_val1=a.x[3],
              y_px0=a.y[0], y_val0=a.y[1], y_px1=a.y[2], y_val1=a.y[3],
              log_x=a.log_x, log_y=a.log_y)
    cal.save(a.out)
    img = load_rgb(a.image)
    print(f"wrote {a.out}")
    print(f"  x: px {cal.x_px0:g}->{cal.x_px1:g} = {cal.x_val0:g}->{cal.x_val1:g}"
          f"{' (log)' if cal.log_x else ''}")
    print(f"  y: px {cal.y_px0:g}->{cal.y_px1:g} = {cal.y_val0:g}->{cal.y_val1:g}"
          f"{' (log)' if cal.log_y else ''}")
    print(f"  image spans x {cal.x(0):g}..{cal.x(img.shape[1]):g}, "
          f"y {cal.y(img.shape[0]):g}..{cal.y(0):g}")


def cmd_series(a):
    img, cal = load_rgb(a.image), Cal.load(a.cal)
    if a.mode == "colour":
        if not a.colour:
            sys.exit("--mode colour needs --colour")
        mask = colour_mask(img, parse_colour(a.colour), a.tol, a.min_sat)
    else:
        mask = dark_mask(img, a.dark_threshold)
    if a.roi:
        x0, y0, x1, y1 = a.roi
        keep = np.zeros_like(mask)
        keep[int(y0):int(y1), int(x0):int(x1)] = mask[int(y0):int(y1), int(x0):int(x1)]
        mask = keep
    mask = apply_excludes(mask, a.exclude)
    if not mask.any():
        sys.exit("no pixels selected -- widen --tol, drop --roi, or check --colour")

    if a.mode == "markers":
        px = extract_markers(mask, tuple(a.area), tuple(a.side), ring=a.ring,
                             min_hole=a.min_hole)
        if a.recover_hidden:
            px += find_hidden_markers(mask, tuple(a.area), tuple(a.side),
                                      min_area=a.min_hole)
        px = dedupe(px, a.dedupe)
    else:
        px, _, _ = extract_curve(mask, a.pick)

    rows = [(float(cal.x(c)), float(cal.y(r))) for c, r in px]
    if a.monotonic and rows:
        sign = 1.0 if a.monotonic == "inc" else -1.0
        kept = [rows[0]]
        for x, y in rows[1:]:
            if sign * (y - kept[-1][1]) >= -abs(a.monotonic_tol):
                kept.append((x, y))
        dropped, rows = len(rows) - len(kept), kept
        if dropped:
            print(f"  monotonic filter dropped {dropped} points", file=sys.stderr)
    if a.resample and len(rows) > a.resample:
        idx = np.linspace(0, len(rows) - 1, a.resample).round().astype(int)
        rows = [rows[i] for i in sorted(set(idx))]

    with open(a.out, "w", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        wr.writerow([a.x_col, a.y_col])
        for x, y in rows:
            wr.writerow([f"{x:.4g}", f"{y:.4g}"])
    print(f"wrote {a.out}: {len(rows)} points, "
          f"x {min(r[0] for r in rows):.4g}..{max(r[0] for r in rows):.4g}, "
          f"y {min(r[1] for r in rows):.4g}..{max(r[1] for r in rows):.4g}")
    print("  -> now run `verify`; an unchecked digitization is not evidence.",
          file=sys.stderr)


def cmd_verify(a):
    cal = Cal.load(a.cal)
    im = Image.open(a.image).convert("RGB")
    dr = ImageDraw.Draw(im)
    n = 0
    for path, col in zip(a.csv, [(0, 160, 255), (255, 0, 200), (0, 200, 80), (255, 140, 0)]):
        with open(path, encoding="utf-8") as f:
            rd = csv.reader(f)
            next(rd, None)
            for row in rd:
                if len(row) < 2:
                    continue
                try:
                    px, py = float(cal.inv_x(float(row[0]))), float(cal.inv_y(float(row[1])))
                except ValueError:
                    continue
                dr.ellipse([px - 3, py - 3, px + 3, py + 3], outline=col, width=2)
                n += 1
    im.save(a.out)
    print(f"wrote {a.out}: {n} points replotted over {Path(a.image).name}")
    print("  Every marker must sit ON its curve. If they are offset or sheared,\n"
          "  the calibration is wrong -- not the extraction.")


# ── cli ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Digitize a data series from a raster plot.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("axes", help="propose a calibration from the frame and ticks")
    q.add_argument("image"); q.set_defaults(func=cmd_axes)

    q = sub.add_parser("calibrate", help="pin the calibration and write cal.json")
    q.add_argument("image")
    q.add_argument("--x", nargs=4, type=float, required=True,
                   metavar=("PX0", "VAL0", "PX1", "VAL1"))
    q.add_argument("--y", nargs=4, type=float, required=True,
                   metavar=("PX0", "VAL0", "PX1", "VAL1"))
    q.add_argument("--log-x", action="store_true"); q.add_argument("--log-y", action="store_true")
    q.add_argument("-o", "--out", default="cal.json"); q.set_defaults(func=cmd_calibrate)

    q = sub.add_parser("series", help="extract one series to CSV")
    q.add_argument("image")
    q.add_argument("-c", "--cal", required=True)
    q.add_argument("--mode", choices=["colour", "curve", "markers"], default="curve")
    q.add_argument("--colour", help="#RRGGBB or R,G,B (mode=colour)")
    q.add_argument("--tol", type=float, default=60.0, help="RGB distance (default 60)")
    q.add_argument("--min-sat", type=int, default=40,
                   help="reject greys below this saturation (default 40)")
    q.add_argument("--dark-threshold", type=int, default=128)
    q.add_argument("--pick", choices=["mean", "upper", "lower"], default="mean",
                   help="which row per column; upper = highest on the page")
    q.add_argument("--ring", action="store_true",
                   help="markers: keep only components enclosing white (open circles)")
    q.add_argument("--recover-hidden", action="store_true",
                   help="markers: also recover markers fused with a curve")
    q.add_argument("--area", nargs=2, type=int, default=[60, 200], metavar=("MIN", "MAX"),
                   help="markers: component/hole pixel area. RAISE the minimum to reject "
                        "letter counters -- an 'o' in an annotation is a ring too")
    q.add_argument("--min-hole", type=int, default=12,
                   help="markers: minimum enclosed-white area. THE discriminator between "
                        "a data ring and a letter counter -- an 'o' in an annotation "
                        "encloses far less white than a plot marker. Measure one of each "
                        "before choosing.")
    q.add_argument("--dedupe", type=float, default=6.0,
                   help="collapse detections within N px of each other (default 6)")
    q.add_argument("--side", nargs=2, type=int, default=[9, 22], metavar=("MIN", "MAX"))
    q.add_argument("--roi", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"))
    q.add_argument("--exclude", nargs=4, type=float, action="append",
                   metavar=("X0", "Y0", "X1", "Y1"),
                   help="blank a rectangle (legend, labels); repeatable")
    q.add_argument("--monotonic", choices=["inc", "dec"],
                   help="drop points that reverse -- rejects text touching the curve")
    q.add_argument("--monotonic-tol", type=float, default=0.0)
    q.add_argument("--resample", type=int, help="thin to about N points")
    q.add_argument("--x-col", default="x"); q.add_argument("--y-col", default="y")
    q.add_argument("-o", "--out", required=True); q.set_defaults(func=cmd_series)

    q = sub.add_parser("verify", help="replot a CSV over the crop -- always do this")
    q.add_argument("image")
    q.add_argument("-c", "--cal", required=True)
    q.add_argument("--csv", nargs="+", required=True)
    q.add_argument("-o", "--out", required=True); q.set_defaults(func=cmd_verify)

    a = p.parse_args(argv)
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
