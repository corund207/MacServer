#!/usr/bin/env python3
"""Render the MacServer dashboard to a PNG exactly as the Mac's console draws it:
the same Lat15-Terminus 32x16 font and the 16 Linux console colours.

    python3 tests/tui_preview.py OUT.png [scenario] [COLS ROWS]

Scenarios: healthy (default), setup, trouble. Needs the console font
(apt install console-setup-linux). Uses sample data, never the real machine.
"""
import gzip
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tui_fixture  # noqa: E402  (sets up sample /proc, /sys and status.json)
import tui  # noqa: E402

FONT = "/usr/share/consolefonts/Lat15-Terminus32x16.psf.gz"
PALETTE = [(0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0), (0, 0, 170), (170, 0, 170), (0, 170, 170),
           (170, 170, 170), (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85), (85, 85, 255),
           (255, 85, 255), (85, 255, 255), (255, 255, 255)]


def load_font(path=FONT):
    data = gzip.open(path).read()
    _, _, hsize, _, count, size, height, width = struct.unpack("<IIIIIIII", data[:32])
    glyphs = [data[hsize + i * size: hsize + (i + 1) * size] for i in range(count)]
    table, index = {}, 0
    for entry in data[hsize + count * size:].split(b"\xff")[:count]:
        for part in entry.split(b"\xfe"):
            for ch in part.decode("utf-8", "ignore"):
                table.setdefault(ch, index)
        index += 1
    return glyphs, table, width, height


def render_png(canvas, path, font=None):
    glyphs, table, gw, gh = font or load_font()
    row_bytes = (gw + 7) // 8
    width, height = canvas.w * gw, canvas.h * gh
    pixels = bytearray(width * height * 3)
    for cy in range(canvas.h):
        for cx in range(canvas.w):
            ch, style = canvas.cells[cy][cx]
            fg, bold = tui.STYLES[style]
            fg_rgb = PALETTE[fg + (8 if bold else 0)]
            bg = canvas.bg[cy][cx]
            bg_rgb = PALETTE[bg] if bg is not None else PALETTE[0]   # the console has no bright backgrounds
            glyph = glyphs[table.get(ch, table.get("?", 0))]
            for gy in range(gh):
                line = glyph[gy * row_bytes:(gy + 1) * row_bytes]
                base = ((cy * gh + gy) * width + cx * gw) * 3
                for gx in range(gw):
                    on = line[gx // 8] & (0x80 >> (gx % 8))
                    pixels[base + gx * 3: base + gx * 3 + 3] = bytes(fg_rgb if on else bg_rgb)
    raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    Path(path).write_bytes(png)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "dashboard.png"
    scenario = sys.argv[2] if len(sys.argv) > 2 else "healthy"
    cols, rows = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (160, 50)
    canvas = tui_fixture.screen(scenario, cols, rows)
    render_png(canvas, out)
    print(f"wrote {out} ({cols}x{rows} characters, scenario {scenario})")


if __name__ == "__main__":
    main()
