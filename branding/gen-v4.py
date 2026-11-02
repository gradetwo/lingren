#!/usr/bin/env python3
"""Generate 伶人 LOGO v4a / v4b from v3, keeping the 人 paths byte-identical.

v4a: 人字单留 (pipes removed entirely).
v4b: 人字 + one single vermilion arc (no graduated heights anywhere).

The two 人 path `d` strings are copied verbatim out of mark-v3.svg;
they are never retyped. Horizontal recentring is done with a <g>
transform so the path data itself stays byte-identical.
"""
import re
import sys
from pathlib import Path

B = Path.home() / "workspace/svs-api-service/branding"

INK = "#1C1A17"
RED = "#D9382B"
PAPER = "#F7F3EC"

v3 = (B / "mark-v3.svg").read_text()
paths = re.findall(r'<path d="([^"]+)"', v3)
assert len(paths) == 2, paths
PIE, NA = paths
assert PIE.startswith("M 108 27 "), PIE[:20]
assert NA.startswith("M 109 36 "), NA[:20]

# v4a: 人 only, span x 64..161 -> shift -12 -> 52..149, centre 100.5
# v4b: 人 + arc, span x 64..196 -> mark shift -30 -> 34..166, centre 100
#       logo shift -12 -> 52..184 (wordmark at x=230 keeps a 46px gap)
ARC = '<path d="M 176 62 Q 196 100 176 138" fill="none" stroke="{c}" stroke-width="7" stroke-linecap="round"/>'

WORDMARK = (
    '  <text x="230" y="118" font-family="Noto Serif CJK SC, Songti SC, serif" '
    'font-size="92" font-weight="700" letter-spacing="4" fill="{ink}">伶人</text>\n'
    '  <text x="234" y="162" font-family="DejaVu Sans, Helvetica, Arial, sans-serif" '
    'font-size="26" letter-spacing="8" fill="{sub}">LINGREN · SVS</text>\n'
)

def ren_group(shift, colour, arc_colour=None):
    s = f'  <g transform="translate({shift} 0)" fill="{colour}">\n'
    s += f'    <path d="{PIE}"/>\n'
    s += f'    <path d="{NA}"/>\n'
    s += '  </g>\n'
    if arc_colour is not None:
        s += f'  <g transform="translate({shift} 0)">\n'
        s += '    ' + ARC.format(c=arc_colour) + '\n'
        s += '  </g>\n'
    return s

def mark(name, label, shift, colour, arc_colour):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        + ren_group(shift, colour, arc_colour) +
        '</svg>\n'
    )

def logo(name, label, shift, colour, arc_colour, sub_colour):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 200" role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        + ren_group(shift, colour, arc_colour)
        + WORDMARK.format(ink=colour, sub=sub_colour) +
        '</svg>\n'
    )

def icon(name, label, shift, fg, arc_colour):
    s = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        f'  <rect x="4" y="4" width="232" height="232" rx="52" fill="{RED}"/>\n'
        '  <g transform="translate(20 20)">\n'
        f'    <g transform="translate({shift} 0)" fill="{fg}">\n'
        f'      <path d="{PIE}"/>\n'
        f'      <path d="{NA}"/>\n'
        '    </g>\n'
    )
    if arc_colour is not None:
        s += f'    <g transform="translate({shift} 0)">\n'
        s += '      ' + ARC.format(c=arc_colour) + '\n'
        s += '    </g>\n'
    s += '  </g>\n</svg>\n'
    return s

files = {
    "mark-v4a.svg": mark("mark-v4a", "伶人标志 v4a", -12, INK, None),
    "logo-v4a.svg": logo("logo-v4a", "伶人 LOGO v4a", -12, INK, None, RED),
    "logo-mono-v4a.svg": logo("logo-mono-v4a", "伶人 LOGO v4a 单色版", -12, INK, None, INK),
    "icon-v4a.svg": icon("icon-v4a", "伶人图标 v4a", -12, PAPER, None),
    "mark-v4b.svg": mark("mark-v4b", "伶人标志 v4b", -30, INK, RED),
    "logo-v4b.svg": logo("logo-v4b", "伶人 LOGO v4b", -12, INK, RED, RED),
    "logo-mono-v4b.svg": logo("logo-mono-v4b", "伶人 LOGO v4b 单色版", -12, INK, INK, INK),
    "icon-v4b.svg": icon("icon-v4b", "伶人图标 v4b", -30, PAPER, PAPER),
}

for name, text in files.items():
    assert text.count("M 108 27") == 1, (name, text.count("M 108 27"))
    assert PIE in text and NA in text, name
    assert "rect" not in text.split("<title>")[1].split("</title>")[0]  # sanity, no-op
    (B / name).write_text(text)
    print("wrote", name, len(text), "bytes")
print("OK: 8 files, each contains M 108 27 exactly once, both 人 paths verbatim")
