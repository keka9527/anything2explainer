#!/usr/bin/env python3
"""Build a contact sheet and dimensions inventory for source-image review."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Directory containing source images")
    parser.add_argument("--output", required=True, help="Contact-sheet JPG or PNG")
    parser.add_argument("--inventory", default="", help="CSV path; defaults beside the sheet")
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--accent", default="#14B8A6")
    args = parser.parse_args()

    if args.columns < 1 or args.columns > 12:
        parser.error("--columns must be between 1 and 12")

    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    if not source.is_dir():
        raise SystemExit(f"Input directory does not exist: {source}")

    files = sorted(
        path
        for path in source.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES and path.resolve() != output
    )
    if not files:
        raise SystemExit("No images found")

    Image.MAX_IMAGE_PIXELS = 50_000_000
    cell_w, cell_h = 210, 170
    rows = math.ceil(len(files) / args.columns)
    sheet = Image.new("RGB", (args.columns * cell_w, rows * cell_h), "#080B0D")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    dimensions: list[dict[str, object]] = []

    for index, path in enumerate(files):
        x = (index % args.columns) * cell_w
        y = (index // args.columns) * cell_h
        status = "ok"
        error = ""
        width = height = 0
        mode = image_format = ""
        try:
            with Image.open(path) as image:
                image.seek(0)
                width, height = image.size
                mode = image.mode
                image_format = str(image.format or "")
                thumb = ImageOps.contain(image.convert("RGB"), (194, 132))
            px = x + (cell_w - thumb.width) // 2
            py = y + 8 + (132 - thumb.height) // 2
            sheet.paste(thumb, (px, py))
        except Exception as exc:
            status = "error"
            error = str(exc)
            draw.rectangle((x + 12, y + 12, x + cell_w - 12, y + 132), outline="#F05F41", width=2)
            draw.text((x + 20, y + 62), "READ ERROR", fill="#F05F41", font=font)

        draw.rectangle(
            (x + 4, y + 4, x + cell_w - 4, y + cell_h - 4),
            outline=args.accent if status == "ok" else "#F05F41",
            width=1,
        )
        label = f"{path.name}  {width}x{height}" if status == "ok" else path.name
        draw.text((x + 9, y + 145), label, fill="#E6FFFB", font=font)
        dimensions.append(
            {
                "file": path.name,
                "width": width,
                "height": height,
                "mode": mode,
                "format": image_format,
                "bytes": path.stat().st_size,
                "status": status,
                "error": error,
            }
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".jpg", ".jpeg"}:
        sheet.save(output, quality=88, optimize=True)
    else:
        sheet.save(output)

    inventory = (
        Path(args.inventory).resolve()
        if args.inventory
        else output.parent / "image-dimensions.csv"
    )
    with inventory.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["file", "width", "height", "mode", "format", "bytes", "status", "error"],
        )
        writer.writeheader()
        writer.writerows(dimensions)

    print(f"images={len(files)} sheet={output} inventory={inventory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
