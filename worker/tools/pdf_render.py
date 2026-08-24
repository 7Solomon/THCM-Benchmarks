#!/usr/bin/env python3
"""Render PDF pages and crop figure panels — the whole PDF side of the
web-extraction route.

Deliberately self-contained: the queue must not depend on the THCM-Fem
extraction toolkit. Vector extraction is NOT done here; figures go to the
AI digitizer (SKILLs/EXTRACT-WEB.md), which needs exactly two things —
a page image to look at, and a tight crop of one panel to upload.

    pdf_render.py prepare IN.pdf OUT.pdf
    pdf_render.py pages  PAPER.pdf
    pdf_render.py render PAPER.pdf 7 outdir/ [--dpi 200] [--max-side 1600]
    pdf_render.py crop   outdir/page7.png 0.08 0.10 0.52 0.48 outdir/fig7a.png

IMAGE SIZE IS A HARD GATE, not a preference. A harness that hands an image to a
model rejects oversized ones -- dsh's `read_image` refuses with
IMAGE_DIMENSION_TOO_LARGE / IMAGE_TOO_LARGE and tells you to downscale. An A4 page
at 300 dpi is ~2480x3508, which is over the limit in most deployments. Both `render`
and `crop` therefore clamp the longest side to --max-side (default 1600) and say so.
Render at whatever dpi makes the ticks sharp; the clamp keeps the file readable.
"""
import argparse
import os
import shutil
import sys

import pymupdf
from PIL import Image

# Longest edge, in pixels, of any image this script writes. Sized to sit under the
# image-input gates of the agent harnesses that consume these files; raise it only
# after checking what the target harness actually accepts.
MAX_SIDE_DEFAULT = 1600


def clamp(im, max_side, what):
    """Downscale so the longest edge is <= max_side. Returns (image, note)."""
    w, h = im.size
    if max_side <= 0 or max(w, h) <= max_side:
        return im, ""
    f = max_side / float(max(w, h))
    out = im.resize((max(1, round(w * f)), max(1, round(h * f))), Image.LANCZOS)
    return out, (f"  downscaled {what} {w}x{h} -> {out.size[0]}x{out.size[1]} "
                 f"(--max-side {max_side}); raise it only if the harness accepts more")


def cmd_prepare(a):
    """Put a paper into the workdir, ready to read.

    Normally a byte-for-byte copy: the workdir should hold the paper as it was
    published, since a human may later check a number against it. Only a paper
    that is actually encrypted gets re-saved without its /Encrypt dictionary —
    some publishers ship an owner-password restriction that PyMuPDF opens but
    generic readers refuse.
    """
    doc = pymupdf.open(a.src)
    if doc.needs_pass:
        sys.exit(f"PDF needs a password, cannot be processed: {a.src}")
    n_pages = len(doc)
    os.makedirs(os.path.dirname(os.path.abspath(a.dst)), exist_ok=True)
    if doc.is_encrypted:
        doc.save(a.dst, garbage=3, deflate=True)   # no encrypt= -> written cleartext
        note = ", encryption stripped"
    else:
        doc.close()
        shutil.copy2(a.src, a.dst)
        note = ""
    print(f"prepared {a.dst}  ({n_pages} pages{note})")


def cmd_pages(a):
    """Inventory: which pages carry figures, and are they vector or raster.

    Informational only — unlike the vector route, raster is not a blocker
    here, it is the normal case. Use it to find the figure pages fast.
    """
    doc = pymupdf.open(a.pdf)
    print(f"{a.pdf}: {len(doc)} pages\n")
    print(f"{'page':>5} {'drawings':>9} {'images':>7}  looks like")
    for i, page in enumerate(doc, start=1):
        n_draw = len(page.get_drawings())
        n_img = len(page.get_images(full=True))
        if n_draw > 40:
            kind = "vector figure(s)"
        elif n_img:
            kind = "raster image(s)"
        elif n_draw:
            kind = "rules/boxes only"
        else:
            kind = "text only"
        if n_draw or n_img:
            print(f"{i:>5} {n_draw:>9} {n_img:>7}  {kind}")


def cmd_render(a):
    """Write a PNG of one page. You MUST open and look at it: panel layout,
    axis ranges, tick labels and legend entries are read with your eyes."""
    doc = pymupdf.open(a.pdf)
    if not 1 <= a.page <= len(doc):
        sys.exit(f"page {a.page} out of range (1..{len(doc)})")
    os.makedirs(a.outdir, exist_ok=True)
    out = os.path.join(a.outdir, f"page{a.page}.png")
    pix = doc[a.page - 1].get_pixmap(dpi=a.dpi)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    im, note = clamp(im, a.max_side, "page")
    im.save(out)
    print(f"wrote {out}  ({im.size[0]}x{im.size[1]} px, rendered at {a.dpi} dpi)")
    if note:
        print(note)
    print("LOOK AT IT, then crop the panel you need:")
    print(f"  pdf_render.py crop {out} <x0> <y0> <x1> <y1> {a.outdir}/panel.png")
    print("  coordinates are fractions of width/height (0..1), origin top-left")


def cmd_crop(a):
    """Crop one panel out of a rendered page.

    A tight crop is the single biggest accuracy lever for the digitizer: include
    the axes, tick labels and legend, exclude the caption and neighbouring panels.
    """
    im = Image.open(a.png)
    w, h = im.size
    box = [a.x0, a.y0, a.x1, a.y1]
    if all(0.0 <= v <= 1.0 for v in box):          # fractions
        box = [a.x0 * w, a.y0 * h, a.x1 * w, a.y1 * h]
    box = [int(round(v)) for v in box]
    if box[0] >= box[2] or box[1] >= box[3]:
        sys.exit(f"empty crop box {box} — expected x0<x1, y0<y1")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    panel, note = clamp(im.crop(box), a.max_side, "panel")
    panel.save(a.out)
    print(f"wrote {a.out}  ({panel.size[0]}x{panel.size[1]} px, "
          f"cropped {box[2]-box[0]}x{box[3]-box[1]} from {w}x{h})")
    if note:
        print(note)
    print("Open it. If tick labels are unreadable at this size, re-render the "
          "page at a higher --dpi and crop again — do not upload a blurry panel.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="normalize a paper into the workdir")
    p.add_argument("src")
    p.add_argument("dst")
    p.set_defaults(fn=cmd_prepare)

    p = sub.add_parser("pages", help="inventory of figure-bearing pages")
    p.add_argument("pdf")
    p.set_defaults(fn=cmd_pages)

    p = sub.add_parser("render", help="render one page to PNG")
    p.add_argument("pdf")
    p.add_argument("page", type=int)
    p.add_argument("outdir")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--max-side", type=int, default=MAX_SIDE_DEFAULT,
                   help=f"clamp the longest edge (default {MAX_SIDE_DEFAULT}); "
                        "0 disables")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("crop", help="crop a panel out of a rendered page")
    p.add_argument("png")
    for c in ("x0", "y0", "x1", "y1"):
        p.add_argument(c, type=float)
    p.add_argument("out")
    p.add_argument("--max-side", type=int, default=MAX_SIDE_DEFAULT,
                   help=f"clamp the longest edge (default {MAX_SIDE_DEFAULT}); "
                        "0 disables")
    p.set_defaults(fn=cmd_crop)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
