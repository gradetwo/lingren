#!/usr/bin/env python3
"""Generate 伶人 LOGO v6 from the new reference language.

Wang 2026-11-02 ~10:27 gave a horizontal red-blue wordmark reference;
~10:30 approved v6 in that language. Rules carried over:
  - v5's failure: a stroke appended on the right reads as punctuation.
    v6 drops live INSIDE the letterforms, at natural terminals and dots.
  - The v3 人 (Wang: "很不错") is the mark's base; its two paths are
    copied verbatim out of mark-v3.svg, never retyped.
  - Colours (reference): ink #131313, vermilion #C33F31, blue #2A6CE9.
  - One red drop, one blue drop per colour version. Mono = all ink.

Layout (mark space 200x200, same -30 shift as v4/v5):
  mark:  人 + red drop complementing the apex stroke + blue drop at the
         end of the right sweep stroke.
  logo:  horizontal lockup, mark left, "伶人 Lingren" right; red drop
         sits in 伶's 目, blue drop is the dot of í in Lingren.
  mono:  same layout, every fill ink.
  icon:  ink rounded rect, paper 人, colour drops.
"""
import re
from pathlib import Path

B = Path.home() / "workspace/svs-api-service/branding"

INK = "#131313"
RED = "#C33F31"
BLUE = "#2A6CE9"
PAPER = "#F7F3EC"

v3 = (B / "mark-v3.svg").read_text()
paths = re.findall(r'<path d="([^"]+)"', v3)
assert len(paths) == 2, paths
PIE, NA = paths
assert PIE.startswith("M 108 27 "), PIE[:20]
assert NA.startswith("M 109 36 "), NA[:20]

# Teardrops, hand-written. RED: tail touches the left stroke just below
# the apex, bulb rising upper-left. BLUE: tail overlaps the right
# stroke's tip, bulb continuing the sweep down-right.
RED_DROP = (
    "M 102 34 "
    "C 94 32, 84 30, 76 24 "
    "C 68 18, 68 6, 78 3 "
    "C 88 0, 97 7, 99 17 "
    "C 100 24, 101 29, 102 34 Z"
)
BLUE_DROP = (
    "M 158 170 "
    "C 162 174, 168 176, 172 180 "
    "C 177 185, 176 193, 169 195 "
    "C 162 197, 155 192, 154 184 "
    "C 153 177, 155 172, 158 170 Z"
)


def ren_group(shift, colour):
    return (
        f'  <g transform="translate({shift} 0)" fill="{colour}">\n'
        f'    <path d="{PIE}"/>\n'
        f'    <path d="{NA}"/>\n'
        '  </g>\n'
    )


def drops_group(shift, red, blue):
    return (
        f'  <g transform="translate({shift} 0)">\n'
        f'    <path d="{RED_DROP}" fill="{red}"/>\n'
        f'    <path d="{BLUE_DROP}" fill="{blue}"/>\n'
        '  </g>\n'
    )


def mark(label, ink, red, blue):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
        f'role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        + ren_group(-30, ink)
        + drops_group(-30, red, blue)
        + '</svg>\n'
    )


def icon(label):
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" '
        f'role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        f'  <rect x="4" y="4" width="232" height="232" rx="52" fill="{INK}"/>\n'
        '  <g transform="translate(20 20)">\n'
        + ren_group(-30, PAPER)
        + drops_group(-30, RED, BLUE)
        + '  </g>\n</svg>\n'
    )


# Wordmark drops are positioned after measuring the rendered text
# (see WORDMARK_DROPS below; tuned against the preview render).
WORDMARK_DROPS = {
    # (x, y, scale, rotation, colour): red 目 drop, blue í dot drop.
    # Initial guesses; tuned against the preview render.
    "red": (272, 78, 0.55, -20, RED),
    # Blue í dot: measured from Noto Serif 44 px metrics (PIL):
    # i stem centre x = 449, dot centre y = 86. Drop transform centres
    # the bulb there (path centre (85,17) minus offset (80,20)).
    "blue": (447, 87, 0.35, 0, BLUE),
}


def wordmark_drop(spec):
    x, y, s, r, colour = spec
    return (
        f'    <g transform="translate({x} {y}) rotate({r}) scale({s})">\n'
        f'      <path d="{RED_DROP}" fill="{colour}" '
        'transform="translate(-80 -20)"/>\n'
        '    </g>\n'
    )


def logo(label, mono):
    ink = INK
    red = INK if mono else RED
    blue = INK if mono else BLUE
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 200" '
        f'role="img" aria-label="{label}">\n'
        f'  <title>{label}</title>\n'
        '  <g transform="translate(10 10) scale(0.9)">\n'
        + ren_group(-30, ink)
        + drops_group(-30, red, blue)
        + '  </g>\n'
        f'  <text x="205" y="120" font-family="Noto Serif CJK SC" '
        f'font-weight="bold" font-size="96" fill="{ink}">伶人</text>\n'
        f'  <text x="415" y="118" font-family="Noto Serif" '
        f'font-size="44" fill="{ink}">Lingren</text>\n'
        + wordmark_drop((WORDMARK_DROPS["red"][0], WORDMARK_DROPS["red"][1],
                         WORDMARK_DROPS["red"][2], WORDMARK_DROPS["red"][3],
                         red))
        + wordmark_drop((WORDMARK_DROPS["blue"][0], WORDMARK_DROPS["blue"][1],
                          WORDMARK_DROPS["blue"][2], WORDMARK_DROPS["blue"][3],
                          blue))
        + '</svg>\n'
    )


FILES = {
    "mark-v6": mark("伶人标志 v6", INK, RED, BLUE),
    "logo-v6": logo("伶人标志 v6 横排", False),
    "logo-mono-v6": logo("伶人标志 v6 单色", True),
    "icon-v6": icon("伶人图标 v6"),
}

for kind, text in FILES.items():
    assert text.count("M 108 27") == 1, (kind, text.count("M 108 27"))
    assert PIE in text and NA in text, kind
    (B / f"{kind}.svg").write_text(text)
    print("wrote", f"{kind}.svg", len(text), "bytes")

print("OK: 4 files, each contains M 108 27 exactly once, both 人 paths verbatim")
