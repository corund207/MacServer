#!/usr/bin/env python3
"""Render the MacServer dashboard to a PNG as the Mac's screen shows it.

    python3 tests/tui_preview.py OUT.png [scenario] [--console] [--px N] [COLSxROWS] [--page NAME]

  rich (default)  the kiosk terminal: JetBrains Mono at N pixels (default 18) on the
                  2560x1600 panel, 24-bit colour; braille and box lines are drawn
                  the way foot draws them. Needs python3-pil and fonts-jetbrains-mono.
  --console       the text-console fallback: Lat15-Terminus 16x32 and the 16 console
                  colours. Needs console-setup-linux.

Scenarios: healthy (default), setup, trouble. Sample data only, never the real machine.
"""
import gzip
from pathlib import Path
import struct
import sys
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tui_fixture  # noqa: E402  (sets up sample /proc, /sys and status.json)
import tui  # noqa: E402

PSF_FONT = "/usr/share/consolefonts/Lat15-Terminus32x16.psf.gz"
TTF_DIR = Path("/usr/share/fonts/truetype/jetbrains-mono")
SCREEN = (2560, 1600)


def load_font(path=PSF_FONT):
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


def write_png(path, width, height, pixels):
    raw = b"".join(b"\x00" + bytes(pixels[y * width * 3:(y + 1) * width * 3]) for y in range(height))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def render_console(canvas, path, font=None):
    glyphs, table, gw, gh = font or load_font()
    row_bytes = (gw + 7) // 8
    width, height = canvas.w * gw, canvas.h * gh
    pixels = bytearray(width * height * 3)
    for cy in range(canvas.h):
        for cx in range(canvas.w):
            ch, fg, bg, bold = canvas.cells[cy][cx]
            i = tui.nearest16(fg)
            fg_rgb = tui.CONSOLE_PALETTE[i + 8 if bold and i < 8 else i]
            bg_rgb = tui.CONSOLE_PALETTE[tui.nearest16(bg, True)] if bg else (0, 0, 0)
            glyph = glyphs[table.get(ch, table.get("?", 0))]
            for gy in range(gh):
                line = glyph[gy * row_bytes:(gy + 1) * row_bytes]
                base = ((cy * gh + gy) * width + cx * gw) * 3
                for gx in range(gw):
                    on = line[gx // 8] & (0x80 >> (gx % 8))
                    pixels[base + gx * 3: base + gx * 3 + 3] = bytes(fg_rgb if on else bg_rgb)
    write_png(path, width, height, pixels)


def rich_fonts(px):
    from PIL import ImageFont
    regular = ImageFont.truetype(str(TTF_DIR / "JetBrainsMono-Regular.ttf"), px)
    bold = ImageFont.truetype(str(TTF_DIR / "JetBrainsMono-Bold.ttf"), px)
    ascent, descent = regular.getmetrics()
    return regular, bold, round(regular.getlength("M")), ascent + descent


def grid_for(px):
    """Columns and rows the Mac's panel holds at this font size."""
    _, _, cw, ch = rich_fonts(px)
    return SCREEN[0] // cw, SCREEN[1] // ch


def render_rich(canvas, path, px=18):
    from PIL import Image, ImageDraw
    regular, bold, cw, chh = rich_fonts(px)
    img = Image.new("RGB", SCREEN, tui.C["bg"])
    d = ImageDraw.Draw(img)
    stroke = max(1, round(px / 14))
    for cy in range(canvas.h):
        for cx in range(canvas.w):
            ch, fg, bg, is_bold = canvas.cells[cy][cx]
            x0, y0 = cx * cw, cy * chh
            if bg:
                d.rectangle([x0, y0, x0 + cw - 1, y0 + chh - 1], fill=bg)
            if ch == " ":
                continue
            code = ord(ch)
            mx, my = x0 + cw // 2, y0 + chh // 2
            if ch == "█":                                  # foot draws full blocks edge to edge
                d.rectangle([x0, y0, x0 + cw - 1, y0 + chh - 1], fill=fg)
            elif 0x2800 <= code <= 0x28ff:                 # braille: draw the dots
                bits = code - 0x2800
                r = max(1.2, cw * 0.16)
                spots = [(0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20),
                         (0, 3, 0x40), (1, 3, 0x80)]
                for col, row, bit in spots:
                    if bits & bit:
                        px_ = x0 + cw * (0.28 + 0.44 * col)
                        py_ = y0 + chh * (0.125 + 0.25 * row)
                        d.ellipse([px_ - r, py_ - r, px_ + r, py_ + r], fill=fg)
            elif ch in "─│╭╮╰╯├┤┬┴":                       # box lines, joined across cells
                rad = min(cw, chh) // 2
                if ch in "─├┤┬┴":
                    left = x0 if ch != "├" else mx
                    right = x0 + cw if ch != "┤" else mx
                    d.line([left, my, right, my], fill=fg, width=stroke)
                if ch in "│├┤":
                    d.line([mx, y0, mx, y0 + chh], fill=fg, width=stroke)
                if ch in "┬":
                    d.line([mx, my, mx, y0 + chh], fill=fg, width=stroke)
                if ch in "┴":
                    d.line([mx, y0, mx, my], fill=fg, width=stroke)
                corners = {"╭": (mx, my + rad, mx + rad, my, 180), "╮": (mx - rad, my, mx, my + rad, 270),
                           "╰": (mx, my - rad, mx + rad, my, 90), "╯": (mx - rad, my, mx, my - rad, 0)}
                if ch in corners:
                    ax, ay, bx, by, start = corners[ch]
                    cxc = mx + rad if ch in "╭╰" else mx - rad
                    cyc = my + rad if ch in "╭╮" else my - rad
                    d.arc([cxc - rad, cyc - rad, cxc + rad, cyc + rad], start, start + 90, fill=fg, width=stroke)
                    if ch in "╭╮":
                        d.line([mx, cyc, mx, y0 + chh], fill=fg, width=stroke)
                    else:
                        d.line([mx, y0, mx, cyc], fill=fg, width=stroke)
                    if ch in "╭╰":
                        d.line([cxc, my, x0 + cw, my], fill=fg, width=stroke)
                    else:
                        d.line([x0, my, cxc, my], fill=fg, width=stroke)
            else:
                d.text((x0, y0), ch, font=bold if is_bold else regular, fill=fg)
    img.save(path)


def main():
    args = sys.argv[1:]
    out = args[0] if args else "dashboard.png"
    scenario = next((a for a in args[1:] if a in ("healthy", "warning", "trouble", "setup")), "healthy")
    console = "--console" in args
    px = int(args[args.index("--px") + 1]) if "--px" in args else 18
    size = next((a for a in args if "x" in a and a.replace("x", "").isdigit()), None)
    if console:
        cols, rows = map(int, size.split("x")) if size else (160, 50)
        render_console(tui_fixture.screen(scenario, cols, rows, rich=False), out)
    else:
        cols, rows = map(int, size.split("x")) if size else grid_for(px)
        page = args[args.index("--page") + 1] if "--page" in args else None
        render_rich(tui_fixture.screen(scenario, cols, rows, rich=True, overview="--overview" in args, page=page), out, px)
    print(f"wrote {out}: {cols}x{rows} characters, {scenario}, {'console' if console else f'rich {px}px'}")


if __name__ == "__main__":
    main()
