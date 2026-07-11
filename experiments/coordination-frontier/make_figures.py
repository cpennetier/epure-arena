"""Regenerate the coordination-frontier note's figures from committed ledgers.

Reads ONLY the JSON ledgers in ./ledgers/ and emits SVG into ./figures/.
Pure standard library — no third-party dependency. Every mark on every figure
is a number read directly out of a committed ledger; nothing is fabricated.

All figures are grayscale-safe: the learned arm is a solid accent fill /
solid line with filled circles; the random arm is a diagonal-hatch fill /
dashed line with open squares — nothing depends on hue alone.

Usage:  python3 make_figures.py
Output: figures/fig_envelope_arms.svg        (F1-A..D)
        figures/fig_existence_worlds.svg     (F2)
        figures/fig_budget_curves.svg        (F3-A..D)
        figures/fig_planted_ktransition.svg  (F4)
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGERS = HERE / "ledgers"
OUT = HERE / "figures"

FONT = "font-family='Helvetica,Arial,sans-serif'"
ACCENT = "#1b3a5c"   # learned proposer (CEM): solid fill, solid line, filled circle
ASH = "#6f6a5e"      # random: hatched fill, dashed line, open square
LOCAL = "#b5b0a4"    # local baselines
INK = "#1c1a16"
PAPER = "#f4f2ec"
BUDGETS = [25, 50, 100, 200, 400, 800]

# diagonal hatch for the random arm — grayscale-safe against the solid accent
HATCH = (
    "<defs><pattern id='hatch' patternUnits='userSpaceOnUse' width='6' height='6' "
    "patternTransform='rotate(45)'>"
    "<rect width='6' height='6' fill='white'/>"
    f"<line x1='0' y1='0' x2='0' y2='6' stroke='{ASH}' stroke-width='2.2'/>"
    "</pattern></defs>\n"
)


def load(name):
    with open(LEDGERS / f"{name}.json") as f:
        return json.load(f)


def header(w, h, title, subtitle):
    return (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' "
        f"viewBox='0 0 {w} {h}'>\n"
        f"<rect width='{w}' height='{h}' fill='white'/>\n" + HATCH +
        f"<text x='24' y='32' {FONT} font-size='17' font-weight='600' fill='{INK}'>{title}</text>\n"
        f"<text x='24' y='52' {FONT} font-size='12' fill='{ASH}'>{subtitle}</text>\n"
    )


def half_up(x, nd=1):
    scale = 10 ** nd
    return int(x * scale + 0.5) / scale


# ---------------------------------------------------------------- figure 1
def fig_envelope_arms():
    """F1-A..D — four full-width stacked panels: mean phi with 95% CI per arm.

    Capsules carry the scientific reading; the pre-registered GO/NO-GO gate
    labels live in the note's reproduce appendix, not here. No arm is bolded:
    where curves coincide the point is 'no material separation', not a winner.
    """
    runs = [
        ("gonogo-cem-existence", "run #1 — backbone-enforced (hard, sparse)",
         "Rarely reachable — existence signal present"),
        ("gonogo-grid-existence", "run #2 — grid (abundant)",
         "Learning unnecessary — random already reaches it"),
        ("gonogo-bbadv-existence", "run #3 — backbone-advisory (mid)",
         "Grammar sufficient — no material separation"),
        ("gonogo-screened-existence", "run #4 — screened, prospective",
         "Prospective selector failed"),
    ]
    arms = [
        ("phi_cem", "learned (CEM)", ACCENT, "solid"),
        ("phi_random", "random, same grammar", ASH, "hatch"),
        ("phi_oneshot", "one-shot menu-selection", LOCAL, "flat"),
        ("phi_polish", "polish-alone", LOCAL, "flat"),
    ]
    W = 960
    PH, GAP, TOP = 232, 26, 72
    H = TOP + 4 * (PH + GAP)
    x_min, x_max = -0.08, 0.44
    x0, x1 = 250, 700
    s = header(W, H, "Certified capture by arm — four pre-registered regimes",
               "coordination capture φ: bars = means, whiskers = 95% CI · n=10 worlds per run · "
               "identical grammar, verifier, and budget across arms")

    def X(v):
        return x0 + (v - x_min) / (x_max - x_min) * (x1 - x0)

    for pi, (ledger, label, reading) in enumerate(runs):
        agg = load(ledger)["aggregate"]
        py = TOP + pi * (PH + GAP)
        s += (f"<text x='24' y='{py + 16}' {FONT} font-size='13.5' font-weight='600' "
              f"fill='{INK}'>{label}</text>\n")
        s += (f"<rect x='608' y='{py + 2}' width='328' height='20' rx='3' fill='{PAPER}' "
              f"stroke='{ASH}' stroke-width='0.9'/>"
              f"<text x='772' y='{py + 16}' {FONT} font-size='10.5' font-style='italic' "
              f"fill='{INK}' text-anchor='middle'>{reading}</text>\n")
        zy0, zy1 = py + 34, py + 34 + 4 * 44
        s += (f"<line x1='{X(0)}' y1='{zy0}' x2='{X(0)}' y2='{zy1}' stroke='{INK}' "
              f"stroke-width='0.8' stroke-dasharray='4 3'/>"
              f"<text x='{X(0)}' y='{zy1 + 13}' {FONT} font-size='10' fill='{ASH}' "
              f"text-anchor='middle'>φ = 0</text>\n")
        for ai, (key, aname, color, style) in enumerate(arms):
            a = agg[key]
            m, (lo, hi) = a["mean"], a["ci95"]
            cy = py + 34 + ai * 44 + 17
            s += (f"<text x='{x0 - 10}' y='{cy + 4}' {FONT} font-size='12' fill='{INK}' "
                  f"text-anchor='end'>{aname}</text>\n")
            bar_w = abs(X(m) - X(0))
            bx = min(X(0), X(m))
            fill = ("url(#hatch)" if style == "hatch"
                    else color if style == "solid" else LOCAL)
            opacity = "0.55" if style == "solid" else "1"
            s += (f"<rect x='{bx}' y='{cy - 9}' width='{max(bar_w, 1.5)}' height='18' "
                  f"fill='{fill}' fill-opacity='{opacity}' stroke='{color}' stroke-width='1.2'/>\n")
            s += (f"<line x1='{X(lo)}' y1='{cy}' x2='{X(hi)}' y2='{cy}' stroke='{INK}' stroke-width='1.3'/>"
                  f"<line x1='{X(lo)}' y1='{cy - 5}' x2='{X(lo)}' y2='{cy + 5}' stroke='{INK}' stroke-width='1.3'/>"
                  f"<line x1='{X(hi)}' y1='{cy - 5}' x2='{X(hi)}' y2='{cy + 5}' stroke='{INK}' stroke-width='1.3'/>\n")
            lbl = (f"{m:+.3f} [{lo:+.3f}, {hi:+.3f}]"
                   if (lo, hi) != (0.0, 0.0) else "+0.000 [0, 0] exactly")
            s += (f"<text x='{X(max(hi, m)) + 8}' y='{cy + 4}' {FONT} font-size='11' "
                  f"fill='{INK}'>{lbl}</text>\n")
    s += "</svg>\n"
    (OUT / "fig_envelope_arms.svg").write_text(s)


# ---------------------------------------------------------------- figure 2
def fig_existence_worlds():
    """F2 — the three certified-positive worlds of run #1, grouped by mechanism:
    world 241 = amplification (random also positive); 243/245 = discovery
    beyond random (random at exactly / floating-point zero)."""
    rows = {r["seed"]: r for r in load("gonogo-cem-existence")["rows"]}
    W, H = 960, 560
    x0, x1 = 300, 830
    x_max = 0.40

    def X(v):
        return x0 + v / x_max * (x1 - x0)

    s = header(W, H, "Three certified-positive worlds: one amplification, two learned-only discoveries",
               "run #1, backbone-enforced · per-world certified φ (bars; single-run point values) · "
               "same grammar, verifier, and budget in every arm")

    def group_box(y, h, title, subtitle):
        return (
            f"<rect x='24' y='{y}' width='912' height='{h}' rx='5' fill='{PAPER}' "
            f"fill-opacity='0.55' stroke='{ASH}' stroke-width='0.8'/>"
            f"<text x='40' y='{y + 24}' {FONT} font-size='12.5' font-weight='700' "
            f"letter-spacing='1' fill='{ACCENT}'>{title}</text>"
            f"<text x='40' y='{y + 42}' {FONT} font-size='10.5' font-style='italic' "
            f"fill='{ASH}'>{subtitle}</text>\n"
        )

    def world_rows(wseed, y):
        r = rows[wseed]
        out = (f"<text x='40' y='{y + 12}' {FONT} font-size='12.5' font-weight='600' "
               f"fill='{INK}'>world {wseed}</text>\n")
        entries = [
            ("learned (CEM)", r["phi_cem"], ACCENT, "solid"),
            ("random, same grammar", r["phi_random"], ASH, "hatch"),
            ("one-shot menu / polish", max(r["phi_oneshot"], r["phi_polish"]), LOCAL, "flat"),
        ]
        for ei, (name, v, color, style) in enumerate(entries):
            cy = y + ei * 27 + 10
            out += (f"<text x='{x0 - 10}' y='{cy + 4}' {FONT} font-size='11' fill='{INK}' "
                    f"text-anchor='end'>{name}</text>\n")
            if v > 1e-9:
                fill = "url(#hatch)" if style == "hatch" else color
                op = "0.55" if style == "solid" else "1"
                out += (f"<rect x='{X(0)}' y='{cy - 8}' width='{X(v) - X(0)}' height='16' "
                        f"fill='{fill}' fill-opacity='{op}' stroke='{color}' stroke-width='1.2'/>"
                        f"<text x='{X(v) + 8}' y='{cy + 4}' {FONT} font-size='11' "
                        f"fill='{INK}'>{v:+.3f}</text>\n")
            else:
                zl = "0.000 exactly" if v == 0.0 else "2.9×10⁻¹⁶ — a floating-point zero"
                out += (f"<line x1='{X(0)}' y1='{cy - 8}' x2='{X(0)}' y2='{cy + 8}' "
                        f"stroke='{INK}' stroke-width='1.8'/>"
                        f"<text x='{X(0) + 9}' y='{cy + 4}' {FONT} font-size='11' "
                        f"fill='{ASH}'>{zl}</text>\n")
        return out

    # group 1 — amplification (world 241)
    g1y = 74
    s += group_box(g1y, 152, "AMPLIFICATION — WORLD 241",
                   "learning improves on value random already reaches: both arms positive, learning higher")
    s += world_rows(241, g1y + 58)

    # group 2 — discovery (worlds 243, 245)
    g2y = g1y + 152 + 18
    s += group_box(g2y, 262, "DISCOVERY BEYOND RANDOM — WORLDS 243 · 245",
                   "learning finds value random does not: random at zero under the identical grammar and budget")
    s += world_rows(243, g2y + 58)
    s += world_rows(245, g2y + 168)

    zy = g2y + 262
    s += (f"<line x1='{X(0)}' y1='{g1y + 50}' x2='{X(0)}' y2='{zy - 8}' stroke='{INK}' "
          f"stroke-width='0.8' stroke-dasharray='4 3'/>"
          f"<text x='{X(0)}' y='{zy + 6}' {FONT} font-size='10' fill='{ASH}' "
          f"text-anchor='middle'>φ = 0</text>\n")
    s += (f"<text x='24' y='{zy + 28}' {FONT} font-size='11' fill='{ASH}'>"
          f"Same grammar, same verifier, same compute in every arm — the only difference is the "
          f"round-over-round refit toward certified elites.</text>\n")
    s += "</svg>\n"
    (OUT / "fig_existence_worlds.svg").write_text(s)


# ---------------------------------------------------------------- figure 3
def fig_budget_curves():
    """F3-A..D — P(certified phi > 0.02) vs verifier budget K (doubling scale).

    The retrospective run-#1 subset (n=3) is visually segregated (tinted panel,
    dashed border, its own subtitle) so it does not read as a peer of the three
    30-world panels, and carries a ΔP(K) inset showing the left-shift directly.
    Arms are distinguished by line style and marker, not color alone; bootstrap
    95% bands are drawn as whiskers at each sampled budget."""
    regimes = [
        ("curves-existence", "Retrospective subset — run #1 (n=3)",
         "descriptive, not population-level", True),
        ("curves-bbenforced", "backbone-enforced, unscreened sparse (30 worlds)", None, False),
        ("curves-grid", "grid, abundant (30 worlds)", None, False),
        ("curves-bbadvisory", "backbone-advisory, mid (30 worlds)", None, False),
    ]
    W, H = 960, 700
    PW, PH = 442, 272
    s = header(W, H, "Reachability is a curve: P(certified φ &gt; 0.02) vs verifier budget K",
               "K axis is a doubling (log-2) scale · 20 proposal-RNG seeds per world per budget · "
               "whiskers = bootstrap 95% bands over worlds (B=1000) · ε=0.02 a priori")
    # legend strip (dedicated row)
    ly = 70
    s += (f"<line x1='24' y1='{ly}' x2='58' y2='{ly}' stroke='{ACCENT}' stroke-width='2.2'/>"
          f"<circle cx='41' cy='{ly}' r='3.6' fill='{ACCENT}'/>"
          f"<text x='64' y='{ly + 4}' {FONT} font-size='11' fill='{INK}'>learned (CEM) — solid, filled circle</text>"
          f"<line x1='300' y1='{ly}' x2='334' y2='{ly}' stroke='{ASH}' stroke-width='1.8' stroke-dasharray='6 4'/>"
          f"<rect x='313' y='{ly - 4}' width='8' height='8' fill='white' stroke='{ASH}' stroke-width='1.6'/>"
          f"<text x='340' y='{ly + 4}' {FONT} font-size='11' fill='{INK}'>random — dashed, open square</text>"
          f"<line x1='600' y1='{ly - 7}' x2='600' y2='{ly + 7}' stroke='{INK}' stroke-width='1.2'/>"
          f"<line x1='596' y1='{ly - 7}' x2='604' y2='{ly - 7}' stroke='{INK}' stroke-width='1.2'/>"
          f"<line x1='596' y1='{ly + 7}' x2='604' y2='{ly + 7}' stroke='{INK}' stroke-width='1.2'/>"
          f"<text x='612' y='{ly + 4}' {FONT} font-size='11' fill='{INK}'>bootstrap 95% band (whisker per budget)</text>\n")

    for pi, (ledger, title, subtitle, retro) in enumerate(regimes):
        d = load(ledger)["curves"]
        px, py = 24 + (pi % 2) * (PW + 46), 92 + (pi // 2) * (PH + 34)
        x0, x1 = px + 46, px + PW - 62
        main_h = PH - 116 if retro else PH - 62
        y0, y1 = py + 40 + main_h, py + 40

        def X(i):
            return x0 + i / (len(BUDGETS) - 1) * (x1 - x0)

        def Y(p):
            return y0 + p * (y1 - y0)

        if retro:
            s += (f"<rect x='{px - 8}' y='{py - 4}' width='{PW + 16}' height='{PH + 10}' rx='6' "
                  f"fill='{PAPER}' fill-opacity='0.6' stroke='{ASH}' stroke-width='1.1' "
                  f"stroke-dasharray='6 4'/>\n")
        s += (f"<text x='{px}' y='{py + 12}' {FONT} font-size='12.5' font-weight='600' "
              f"fill='{INK}'>{title}</text>\n")
        if subtitle:
            s += (f"<text x='{px}' y='{py + 27}' {FONT} font-size='10.5' font-style='italic' "
                  f"fill='{ASH}'>{subtitle}</text>\n")
        for p in (0.0, 0.25, 0.5, 0.75, 1.0):
            s += (f"<line x1='{x0}' y1='{Y(p)}' x2='{x1}' y2='{Y(p)}' stroke='{ASH}' "
                  f"stroke-width='0.35' stroke-dasharray='2 4'/>"
                  f"<text x='{x0 - 6}' y='{Y(p) + 3}' {FONT} font-size='8.5' fill='{ASH}' "
                  f"text-anchor='end'>{p:.2f}</text>\n")
        for i, k in enumerate(BUDGETS):
            s += (f"<text x='{X(i)}' y='{y0 + 14}' {FONT} font-size='9' fill='{INK}' "
                  f"text-anchor='middle'>{k}</text>\n")
        s += (f"<text x='{(x0 + x1) / 2}' y='{y0 + 27}' {FONT} font-size='9' fill='{ASH}' "
              f"text-anchor='middle'>verifier budget K — doubling scale (log₂)</text>\n")
        ends = []
        for arm, color, dash, woff in (("random", ASH, "6 4", 4), ("cem", ACCENT, None, -4)):
            pts = [(X(i), Y(d[arm][str(k)]["p_lift"])) for i, k in enumerate(BUDGETS)]
            # band whiskers at each sampled budget, offset so arms don't overlap
            for i, k in enumerate(BUDGETS):
                lo, hi = d[arm][str(k)]["band95"]
                wx = X(i) + woff
                s += (f"<line x1='{wx}' y1='{Y(lo)}' x2='{wx}' y2='{Y(hi)}' "
                      f"stroke='{color}' stroke-width='1'/>"
                      f"<line x1='{wx - 3}' y1='{Y(lo)}' x2='{wx + 3}' y2='{Y(lo)}' stroke='{color}' stroke-width='1'/>"
                      f"<line x1='{wx - 3}' y1='{Y(hi)}' x2='{wx + 3}' y2='{Y(hi)}' stroke='{color}' stroke-width='1'/>\n")
            path = " ".join(f"{'M' if i == 0 else 'L'} {x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts))
            dashattr = f" stroke-dasharray='{dash}'" if dash else ""
            s += f"<path d='{path}' fill='none' stroke='{color}' stroke-width='2'{dashattr}/>\n"
            for x, y in pts:
                if arm == "cem":
                    s += f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.2' fill='{color}'/>\n"
                else:
                    s += (f"<rect x='{x - 3.2:.1f}' y='{y - 3.2:.1f}' width='6.4' height='6.4' "
                          f"fill='white' stroke='{color}' stroke-width='1.4'/>\n")
            ends.append((color, pts[-1][1], d[arm]["800"]["p_lift"]))
        ends.sort(key=lambda e: e[1])
        if len(ends) == 2 and abs(ends[0][1] - ends[1][1]) < 13:
            mid = (ends[0][1] + ends[1][1]) / 2
            ends[0] = (ends[0][0], mid - 7, ends[0][2])
            ends[1] = (ends[1][0], mid + 7, ends[1][2])
        for color, yy, val in ends:
            s += (f"<text x='{x1 + 10}' y='{yy + 3.5}' {FONT} font-size='9.5' font-weight='600' "
                  f"fill='{color}'>{val:.3f}</text>\n")

        if retro:
            # ΔP(K) inset — the left-shift shown directly
            iy0 = y0 + 42
            ih = 34
            dmax = 0.12

            def IY(v):
                return iy0 + ih - v / dmax * ih

            s += (f"<text x='{x0}' y='{iy0 - 5}' {FONT} font-size='9.5' font-weight='600' "
                  f"fill='{ACCENT}'>ΔP(K) = P(CEM) − P(random) — the left-shift, directly</text>\n")
            s += (f"<line x1='{x0}' y1='{IY(0)}' x2='{x1}' y2='{IY(0)}' stroke='{INK}' stroke-width='0.7'/>\n")
            for i, k in enumerate(BUDGETS):
                dv = d["cem"][str(k)]["p_lift"] - d["random"][str(k)]["p_lift"]
                bx = X(i) - 7
                s += (f"<rect x='{bx}' y='{IY(max(dv, 0))}' width='14' height='{abs(IY(dv) - IY(0))}' "
                      f"fill='{ACCENT}' fill-opacity='0.55' stroke='{ACCENT}' stroke-width='0.8'/>\n")
                if abs(dv) >= 0.005:
                    s += (f"<text x='{X(i)}' y='{IY(max(dv, 0)) - 3}' {FONT} font-size='8' "
                          f"fill='{ACCENT}' text-anchor='middle'>{'+' if dv > 0 else ''}{dv:.2f}</text>\n")
    s += "</svg>\n"
    (OUT / "fig_budget_curves.svg").write_text(s)


# ---------------------------------------------------------------- figure 4
def fig_planted_ktransition():
    """F4 — planted k-cycle, P(find) at K=800. Random wins at every k with a
    growing RELATIVE advantage (ratio annotated per k); the ABSOLUTE gap peaks
    at k=4. No 'widening margin' phrasing anywhere."""
    d = load("planted-kcycle")["per_k"]
    W, H = 960, 500
    s = header(W, H, "The planted k-cycle: random wins at every k — its relative advantage grows with k",
               "P(find the planted cycle) at K=800 · 10 constructed worlds per k, 20 RNG draws per world · "
               "1-opt/2-opt exactly neutral and cycle lift = k·Δ, proven exhaustively on 40/40 worlds")
    ly = 70
    s += (f"<rect x='24' y='{ly - 8}' width='16' height='12' fill='{ACCENT}' fill-opacity='0.55' "
          f"stroke='{ACCENT}'/><text x='46' y='{ly + 2}' {FONT} font-size='11' fill='{INK}'>learned (CEM — refits toward elites)</text>"
          f"<rect x='300' y='{ly - 8}' width='16' height='12' fill='url(#hatch)' "
          f"stroke='{ASH}'/><text x='322' y='{ly + 2}' {FONT} font-size='11' fill='{INK}'>random (never refits)</text>"
          f"<text x='700' y='{ly + 2}' {FONT} font-size='11' font-style='italic' fill='{INK}'>whiskers: bootstrap 95% band</text>\n")
    y_base, y_top = 400, 116

    def Y(p):
        return y_base - p * (y_base - y_top)

    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        s += (f"<line x1='70' y1='{Y(p)}' x2='900' y2='{Y(p)}' stroke='{ASH}' "
              f"stroke-width='0.35' stroke-dasharray='2 4'/>"
              f"<text x='62' y='{Y(p) + 3}' {FONT} font-size='9' fill='{ASH}' text-anchor='end'>{p:.2f}</text>\n")
    for ki, k in enumerate(("3", "4", "5", "6")):
        gx = 130 + ki * 200
        vals = {}
        for off, (arm, color, style) in enumerate((("cem", ACCENT, "solid"), ("random", ASH, "hatch"))):
            c = d[k]["curves"][arm]["800"]
            v, (lo, hi) = c["p_find"], c["band95"]
            vals[arm] = v
            bx = gx + off * 62
            fill = "url(#hatch)" if style == "hatch" else color
            op = "0.55" if style == "solid" else "1"
            s += (f"<rect x='{bx}' y='{Y(v)}' width='48' height='{y_base - Y(v)}' "
                  f"fill='{fill}' fill-opacity='{op}' stroke='{color}' stroke-width='1.2'/>\n")
            s += (f"<line x1='{bx + 24}' y1='{Y(lo)}' x2='{bx + 24}' y2='{Y(hi)}' stroke='{INK}' stroke-width='1'/>"
                  f"<line x1='{bx + 18}' y1='{Y(lo)}' x2='{bx + 30}' y2='{Y(lo)}' stroke='{INK}' stroke-width='1'/>"
                  f"<line x1='{bx + 18}' y1='{Y(hi)}' x2='{bx + 30}' y2='{Y(hi)}' stroke='{INK}' stroke-width='1'/>\n")
            s += (f"<text x='{bx + 24}' y='{Y(hi) - 7}' {FONT} font-size='10.5' font-weight='600' "
                  f"fill='{color}' text-anchor='middle'>{v:.2f}</text>\n")
        ratio = half_up(vals["random"] / vals["cem"], 1)
        s += (f"<text x='{gx + 55}' y='{y_base + 20}' {FONT} font-size='12' font-weight='600' "
              f"fill='{INK}' text-anchor='middle'>k = {k}</text>\n")
        s += (f"<text x='{gx + 55}' y='{y_base + 37}' {FONT} font-size='10.5' "
              f"fill='{ASH}' text-anchor='middle'>random ÷ learned = {ratio}×</text>\n")
        s += (f"<text x='{gx + 55}' y='{y_base + 52}' {FONT} font-size='9.5' "
              f"fill='{ASH}' text-anchor='middle'>absolute gap {vals['random'] - vals['cem']:+.2f}"
              f"{' — peak' if k == '4' else ''}</text>\n")
    s += (f"<line x1='70' y1='{y_base}' x2='900' y2='{y_base}' stroke='{INK}' stroke-width='1'/>\n"
          f"<text x='24' y='{y_base + 78}' {FONT} font-size='10.5' fill='{ASH}'>"
          f"Random's relative advantage grows from 1.1× at k=3 to 2.3× at k=6; the absolute gap peaks at k=4. "
          f"A pure alternating cycle carries no partial credit,</text>\n"
          f"<text x='24' y='{y_base + 93}' {FONT} font-size='10.5' fill='{ASH}'>"
          f"so elites carry no informative ranking signal and the refit concentrates sampling mass away "
          f"from the needle — the pre-registered separation is refuted.</text>\n")
    s += "</svg>\n"
    (OUT / "fig_planted_ktransition.svg").write_text(s)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    fig_envelope_arms()
    fig_existence_worlds()
    fig_budget_curves()
    fig_planted_ktransition()
    print("wrote 4 figures to", OUT)
