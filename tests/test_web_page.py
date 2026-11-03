"""The admin page (admin/static): what its Content-Security-Policy and its design rules need to stay true.
The page may load nothing from outside, run no inline script, style nothing inline, and write values only
through textContent. The colours must stay readable. No browser needed."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "admin/static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
CODE = re.sub(r"^\s*//.*$", "", JS, flags=re.M)        # the script without its comment lines


def luminance(hex_colour):
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block):
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))


class PageRulesTests(unittest.TestCase):
    def test_nothing_is_loaded_from_outside_or_run_inline(self):
        """default-src 'self' (admin/server.py): no <style>, no style="", no inline <script>, no outside URL."""
        self.assertNotRegex(HTML, r"<style\b")
        self.assertNotRegex(HTML, r"\sstyle\s*=")
        self.assertNotRegex(HTML, r"<script(?![^>]*\ssrc=)[^>]*>")             # every script has a src
        self.assertNotRegex(HTML, r"\son\w+\s*=")                              # no onclick= and friends
        self.assertEqual(re.findall(r"""(?:src|href)=["'](https?:)?//""", HTML), [])
        self.assertNotRegex(CSS, r"@import|url\(\s*[\"']?(https?:)?//")
        # the only addresses in the script: claude.ai/code (a link it checks for) and https://${domain} (text it shows)
        self.assertNotRegex(CODE, r"https?://(?!claude\.ai|\$\{)")

    def test_values_are_written_as_text_never_as_markup(self):
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(banned, CODE, banned)

    def test_every_id_the_script_uses_exists_and_ids_are_unique(self):
        ids = re.findall(r'\bid="([^"]+)"', HTML)
        self.assertEqual(sorted({i for i in ids if ids.count(i) > 1}), [])
        wanted = set(re.findall(r"\$\('([\w-]+)'\)", JS))
        self.assertEqual(sorted(i for i in wanted if i not in ids), [])
        tabs = re.search(r"const TABS = \[([^\]]*)\]", JS).group(1)
        for name in re.findall(r"'(\w+)'", tabs):                              # every page has its button and its panel
            self.assertIn(f'id="view-{name}"', HTML)
            self.assertIn(f'data-tab="{name}"', HTML)
        for kpi in re.findall(r"'(\w+)'", re.search(r"const KPIS = \[([^\]]*)\]", JS).group(1)):
            self.assertRegex(JS, r"\b" + kpi + r": .*\blabel: '")          # each stat tile has its definition

    def test_markup_is_balanced(self):
        for tag in ("div", "section", "table", "header", "nav", "main", "select", "pre"):
            self.assertEqual(len(re.findall(rf"<{tag}\b", HTML)), len(re.findall(rf"</{tag}>", HTML)), tag)

    def test_text_stays_readable_in_both_themes(self):
        """WCAG AA, 4.5:1, for the text colours on the surfaces they sit on."""
        dark = tokens(CSS[CSS.index(":root"):CSS.index("@media (prefers-color-scheme: light)")])
        light = tokens(CSS[CSS.index("@media (prefers-color-scheme: light)"):CSS.index("* { box-sizing")])
        for name, t in (("dark", dark), ("light", {**dark, **light})):
            for fg in ("text", "muted", "accent", "ok", "warn", "bad"):
                for bg in ("surface", "bg", "surface-2"):
                    if fg in ("ok", "warn", "bad", "accent") and bg != "surface":
                        continue
                    self.assertGreaterEqual(contrast(t[fg], t[bg]), 4.5, f"{name}: {fg} on {bg}")
            self.assertGreaterEqual(contrast(t["accent-ink"], t["accent"]), 4.5, f"{name}: primary button")

    def test_it_is_a_real_tab_list_and_honours_reduced_motion(self):
        self.assertIn('role="tablist"', HTML)
        self.assertEqual(HTML.count('role="tab"'), HTML.count('role="tabpanel"'))
        self.assertIn("prefers-reduced-motion", CSS)
        self.assertIn(":focus-visible", CSS)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_the_script_parses(self):
        done = subprocess.run(["node", "--check", str(STATIC / "app.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
