#!/usr/bin/env python3
"""Generate 伶人 LOGO v5 from v3, keeping the 人 paths byte-identical.

Wang 2026-11-02 ~09:11: v4b (single arc) is a bit monotonous.
Wang 2026-11-02 ~09:12: take inspiration from traditional opera (伶人 is
itself an opera term). v5 = opera-led, mark + icon only (logo/mono after
the direction is settled).

Schemes (element coordinates live in the same -30 group space as v4b,
mark viewBox 200x200, icon 240x240 with an outer translate(20 20)):
  feather: 人 + one 翎子 — a tall, slender, gently S-curved plume rising
           beside the figure (M 170 152 C 200 118, 204 74, 186 26),
           stroke 6. No feather-eye detail, no helmet complexity.
  sleeve:  人 + one 水袖一抹 — a flowing, more horizontal wave that rises
           then falls (M 164 104 C 186 82, 204 104, 190 138), stroke 8.
  double:  人 + two concentric arcs, same width and colour, clear gap
           (control: the v4b single arc + one outer arc, no graduation,
           no bamboo nodes).

The two 人 path `d` strings are copied verbatim out of mark-v3.svg;
they are never retyped. All elements are single strokes with round caps.
Mark elements are vermilion; icon elements are paper (same as v4b).
"""
import re
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

# Single-stroke elements: (d, stroke-width). All in -30 group space.
ELEMENTS = {
    "feather": [
        ('M 170 152 C 200 118, 204 74, 186 26', 6),
    ],
    "sleeve": [
        ('M 164 104 C 186 82, 204 104, 190 138', 8),
    ],
    "double": [
        ('M 176 62 Q 196 100 176 138', 7),
        ('M 194 50 Q 220 100 194 150', 7),
    ],
}

LABELS = {
    "feather": "翎子",
    "sleeve": "水袖",
    "double": "双弧",
}


def element_group(shift, colour, scheme):
    s = f'  <g transform="translate({shift} 0)">\n'
    for d, w in ELEMENTS[scheme]:
        s += (f'    <path d="{d}" fill="none" stroke="{colour}" '
              f'stroke-width="{w}" stroke-linecap="round"/>\n')
    s += '  </g>\n'
    return s


def ren_group(shift, colour):
    return (
        f'  <g transform="translate({shift} 0)" fill="{colour}">\n'
        f'    <path d="{PIE}"/>\n'
        f'    <path d="{NA}"/>\n'
        '  </g>\n'
    )


def mark(scheme, label):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
        f'role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        + ren_group(-30, INK)
        + element_group(-30, RED, scheme)
        + '</svg>\n'
    )


def icon(scheme, label):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" '
        f'role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        f'  <rect x="4" y="4" width="232" height="232" rx="52" fill="{RED}"/>\n'
        '  <g transform="translate(20 20)">\n'
        f'    <g transform="translate(-30 0)" fill="{PAPER}">\n'
        f'      <path d="{PIE}"/>\n'
        f'      <path d="{NA}"/>\n'
        '    </g>\n'
        f'    <g transform="translate(-30 0)">\n'
        + ''.join(
            f'      <path d="{d}" fill="none" stroke="{PAPER}" '
            f'stroke-width="{w}" stroke-linecap="round"/>\n'
            for d, w in ELEMENTS[scheme]
        )
        + '    </g>\n'
        '  </g>\n</svg>\n'
    )


for scheme in ("feather", "sleeve", "double"):
    tag = LABELS[scheme]
    for kind, text in (
        (f"mark-v5-{scheme}", mark(scheme, f"伶人标志 v5{tag}")),
        (f"icon-v5-{scheme}", icon(scheme, f"伶人图标 v5{tag}")),
    ):
        assert text.count("M 108 27") == 1, (kind, text.count("M 108 27"))
        assert PIE in text and NA in text, kind
        (B / f"{kind}.svg").write_text(text)
        print("wrote", f"{kind}.svg", len(text), "bytes")
print("OK: 6 files, each contains M 108 27 exactly once, both 人 paths verbatim")
