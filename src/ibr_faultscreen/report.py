"""Self-contained HTML screening report.

One file, no server, opens in any browser. Sections:

1. Plain-language summary for managers (traffic light, top risks, next steps)
2. Network map: each bus shaded by the share of RISKY faults there, with a
   slider (and Play button) to watch risk change as more IBRs are added
3. Ranked risk table (sortable), each verdict marked simulated or predicted;
   becomes a list of cards on phones, so nothing needs horizontal scrolling
4. Critical clearing times (if computed)
5. Risk versus IBR share (the "hosting curve")
6. Interactive trajectories for the riskiest scenarios (drawn in the browser,
   animated when selected, with a hover read-out)
7. Model card (if a classifier was used), provenance and limits

Style follows SCR-Screen: white background, black headings, a teal rule under
the title (ShaiLearning teal #0a9396); risk uses a single-hue orange ramp.
Animations follow SCR-Screen's map (edges fade in, buses pop, pulsing halos)
and respect the reader's "reduce motion" setting.
"""

from __future__ import annotations

import base64
import csv
import html
import io
import json
from datetime import date
from pathlib import Path

import numpy as np

TEAL, TEAL_DARK = "#0a9396", "#077a7d"
RISK_RAMP = ("#fdeee6", "#f9c9ad", "#f29b72", "#e2683f", "#b8401c")   # light -> dark
NO_DATA = "#e3e2dd"
HALO_SHARE = 0.6            # buses at or above this RISKY share get a pulsing halo

LIMITS = [
    "Screening only: it ranks contingencies for detailed study and does not replace an "
    "interconnection study.",
    "RMS positive-sequence simulation (ANDES): no EMT effects, balanced faults only.",
    "Generic WECC IBR models (REGCA1/REECA1/REPCA1), not a specific manufacturer.",
    "Results depend on the criteria file; check its sources before relying on a verdict.",
]


def _e(x):
    return html.escape("" if x is None else str(x))


# ------------------------------------------------------------------ layout

#: Fixed drawing positions for built-in cases (file in the package data folder).
FIXED_LAYOUTS = {"ieee39": "layout_ieee39.yaml"}

LAYOUT_NOTES = {
    "fixed": "Bus positions follow the usual one-line drawing of this test system; "
             "they are not geographic.",
    "coords": "Bus positions come from the case's own coordinates.",
    "auto": "Bus positions are computed automatically from the network connections "
            "(the case has no coordinates); they are not geographic.",
}


def _fixed_positions(case, buses):
    """Positions from a built-in layout file, or None if there is none for this case
    or it does not cover exactly the case's buses."""
    name = FIXED_LAYOUTS.get(str(case).lower()) if case is not None else None
    if name is None:
        return None
    import yaml
    from importlib.resources import files
    data = yaml.safe_load(files("ibr_faultscreen.data").joinpath(name).read_text(encoding="utf-8"))
    pos = {int(b): (float(x), float(y)) for b, (x, y) in data["positions"].items()}
    return pos if set(pos) == set(buses) else None


def _case_coordinates(sheets, buses):
    """Positions from the Bus table's xcoord/ycoord (y pointing up), or None."""
    bus = sheets["Bus"]
    if "xcoord" not in bus or "ycoord" not in bus:
        return None
    pos = {int(b): (float(x), -float(y)) for b, x, y in zip(bus["idx"], bus["xcoord"], bus["ycoord"])}
    if len(set(pos.values())) < len(pos):          # missing (all zero) or duplicated coordinates
        return None
    return pos if set(pos) == set(buses) else None


def _edges(sheets):
    """(bus1, bus2, is_transformer) for every branch."""
    line = sheets["Line"]
    vn = {int(b): float(v) for b, v in zip(sheets["Bus"]["idx"], sheets["Bus"]["Vn"])} \
        if "Vn" in sheets["Bus"] else {}
    out = []
    for _, r in line.iterrows():
        a, b = int(r["bus1"]), int(r["bus2"])
        if "trans" in line and str(r["trans"]) not in ("", "nan"):
            xf = bool(int(float(r["trans"])))
        else:
            xf = vn.get(a) is not None and vn.get(a) != vn.get(b)
        out.append((a, b, xf))
    return out


def _auto_positions(buses, edges, seed=7, iters=400):
    """Deterministic force-directed layout (Fruchterman-Reingold)."""
    pos = {b: i for i, b in enumerate(buses)}
    idx = [(pos[a], pos[b]) for a, b, _ in edges]
    n = len(buses)
    rng = np.random.default_rng(seed)
    P = rng.random((n, 2))
    k = 1 / np.sqrt(n)
    t = 0.1
    for _ in range(iters):
        d = P[:, None, :] - P[None, :, :]
        dist = np.linalg.norm(d, axis=2) + 1e-9
        rep = (k * k / dist ** 2)[:, :, None] * d
        disp = rep.sum(axis=1)
        for a, b in idx:
            v = P[a] - P[b]
            L = np.linalg.norm(v) + 1e-9
            f = (L / k) * v
            disp[a] -= f
            disp[b] += f
        L = np.linalg.norm(disp, axis=1)[:, None] + 1e-9
        P += disp / L * np.minimum(L, t)
        t *= 0.99
    return {b: (float(P[i, 0]), float(P[i, 1])) for b, i in pos.items()}


def network_layout(sheets, case=None):
    """Map positions in [0, 1]^2 (y downward), branches and a note on where the
    positions come from.

    Returns (positions, edges, source): edges are (bus1, bus2, is_transformer);
    source is "fixed" (built-in drawing for a known test case), "coords" (the
    case's own bus coordinates) or "auto" (force-directed layout)."""
    buses = [int(b) for b in sheets["Bus"]["idx"]]
    edges = _edges(sheets)
    pos, source = _fixed_positions(case, buses), "fixed"
    if pos is None:
        pos, source = _case_coordinates(sheets, buses), "coords"
    if pos is None:
        pos, source = _auto_positions(buses, edges), "auto"
    P = np.array([pos[b] for b in buses], dtype=float)
    P -= P.min(axis=0)
    P /= P.max(axis=0) + 1e-9
    return {b: (float(P[i, 0]), float(P[i, 1])) for i, b in enumerate(buses)}, edges, source


# ------------------------------------------------------------------ pieces

def _short(text, n=150):
    text = str(text).split(" | ")[0]
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def _risk_color(share):
    if share is None:
        return NO_DATA
    return RISK_RAMP[min(len(RISK_RAMP) - 1, int(share * len(RISK_RAMP)))]


def map_data(rows, layout):
    """Per-bus RISKY share and fault count for every IBR-count filter m = 0..max,
    plus the overall totals per m (for the caption)."""
    max_ibr = max((int(r.get("n_ibr") or 0) for r in rows), default=0)
    stats = {}
    for r in rows:
        b = r.get("fault_bus")
        if b in (None, ""):
            continue
        stats.setdefault(int(float(b)), []).append((int(r.get("n_ibr") or 0), r["risky"]))
    buses = {}
    for b in sorted(layout):
        share, count = [], []
        for m in range(max_ibr + 1):
            v = [rk for n, rk in stats.get(b, []) if n <= m]
            share.append(None if not v else round(sum(v) / len(v), 3))
            count.append(len(v))
        buses[b] = {"s": share, "c": count}
    totals = []
    for m in range(max_ibr + 1):
        v = [r["risky"] for r in rows if int(r.get("n_ibr") or 0) <= m]
        totals.append([sum(v), len(v)])
    return {"max": max_ibr, "bus": buses, "tot": totals}


def _map_svg(layout, edges, gen_buses, ibr_hv_buses, shares, *, tall=False):
    """SVG network map. `tall` rotates the drawing a quarter turn clockwise for
    portrait screens (phones), so it is turned, not mirrored."""
    W, H = (600, 860) if tall else (960, 600)
    pad = 34 if tall else 30
    k = 1.35 if tall else 1.0          # bigger buses on phones, where the map is scaled down

    def xy(b):
        x, y = layout[b]
        if tall:
            x, y = 1.0 - y, x
        return pad + x * (W - 2 * pad), pad + y * (H - 2 * pad)

    cls = "map tall" if tall else "map wide"
    out = [f'<svg viewBox="0 0 {W} {H}" class="{cls}" role="img" '
           f'aria-label="Network map shaded by share of RISKY faults at each bus"><g class="edges">']
    step_e = min(18.0, 700.0 / max(len(edges), 1))
    for i, (a, b, xf) in enumerate(edges):
        (x1, y1), (x2, y2) = xy(a), xy(b)
        ecls = "edge xf" if xf else "edge"
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" class="{ecls}" '
                   f'style="animation-delay:{i * step_e:.0f}ms"/>')
    out.append('</g><g class="buses">')
    step_b = min(25.0, 900.0 / max(len(layout), 1))
    for i, b in enumerate(sorted(layout)):
        x, y = xy(b)
        share = shares.get(b)
        delay = f"animation-delay:{300 + i * step_b:.0f}ms"
        ncls = "node ibr" if b in ibr_hv_buses else "node"
        halo = f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{13 * k:.1f}" class="halo"/>'
        if b in gen_buses:
            h = 12 * k
            shape = (f'<rect x="{x - h:.1f}" y="{y - h:.1f}" width="{2 * h:.1f}" height="{2 * h:.1f}" '
                     f'rx="4" class="{ncls}" fill="{_risk_color(share)}" style="{delay}"/>')
        else:
            shape = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{12.5 * k:.1f}" class="{ncls}" '
                     f'fill="{_risk_color(share)}" style="{delay}"/>')
        kind = " (generator)" if b in gen_buses else ""
        out.append(f'<g class="bus" data-bus="{b}" tabindex="0" role="button" '
                   f'aria-label="Bus {b}{kind}">{halo}{shape}'
                   f'<text x="{x:.1f}" y="{y + 4 * k:.1f}" class="lbl">{b}</text>'
                   f'<title>Bus {b}{kind}</title></g>')
    out.append("</g></svg>")
    return "".join(out)


def _hosting_html(rows):
    """Share of RISKY scenarios by IBR share of generation, as responsive bars."""
    bins = [0, 0.1, 0.2, 0.3, 0.4, 0.6, 1.01]
    names = ["0–10%", "10–20%", "20–30%", "30–40%", "40–60%", "60% +"]
    out = ['<div class="host" role="table" aria-label="Share of RISKY scenarios by IBR share">']
    for i, (a, b, nm) in enumerate(zip(bins[:-1], bins[1:], names)):
        v = [r["risky"] for r in rows if r.get("ibr_share") not in (None, "")
             and a <= float(r["ibr_share"]) < b]
        n = len(v)
        if n:
            s = sum(v) / n
            bar = (f'<div class="track"><div class="hbar" style="width:{max(s * 100, 0.8):.1f}%;'
                   f'animation-delay:{i * 80}ms"></div></div>')
            val = f"<b>{s * 100:.0f}%</b> RISKY <span class='muted'>({sum(v)} of {n})</span>"
        else:
            bar = '<div class="track empty"></div>'
            val = "<span class='muted'>no scenarios</span>"
        out.append(f'<div class="hrow" role="row"><span role="cell">{nm}</span>{bar}'
                   f'<span role="cell" class="hval">{val}</span></div>')
    out.append("</div>")
    return "".join(out)


def _sort_value(k, v):
    if k == "verdict":
        sv = str(v)
        return (3 if "unresolved" in sv else 2 if sv.startswith("RISKY") else
                1 if "predicted" in sv else 0)
    if isinstance(v, (int, float)):
        return v
    return str(v)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.3g}"
    return "" if v is None else str(v)


def _table(rows, cols, cls="risk", limit=None, linked=()):
    """Sortable table. On narrow screens CSS turns each row into a card, using
    each cell's data-label; nothing needs horizontal scrolling."""
    head = "".join(f'<th scope="col" data-i="{i}" class="c-{k}" aria-sort="none">'
                   f'<button type="button">{_e(c)}</button></th>'
                   for i, (c, k) in enumerate(cols))
    body = []
    for r in rows[:limit] if limit else rows:
        tds = []
        for c, k in cols:
            v = r.get(k, "")
            sv = _sort_value(k, v)
            attrs = f'class="c-{k}" data-label="{_e(c)}" data-v="{_e(sv)}"'
            if k == "verdict":
                tone = "bad" if str(v).startswith("RISKY") else ("warn" if "predicted" in str(v)
                                                                 else "good")
                cell = f'<span class="pill {tone}">{_e(v)}</span>'
            elif k == "scenario_id":
                name = _e(v).replace("_", "_<wbr>")          # break only after underscores
                cell = (f'<a href="#trajectories" class="sclink" data-sid="{_e(v)}" '
                        f'title="Show its trajectories">{name}</a>' if v in linked else name)
            elif k == "ibrs":
                parts = [p for p in str(v).split(";") if p]
                cell = "".join(f'<span class="chip">{_e(p)}</span>' for p in parts) or \
                    '<span class="muted">none</span>'
            elif k in ("reasons", "why", "text"):
                txt = str(v or "")
                parts = [p for p in txt.split(" | ") if p]
                if not parts:
                    cell = ""
                elif len(txt) <= 110 and len(parts) == 1:
                    cell = _e(txt)
                else:
                    cell = (f'<details><summary>{_e(_short(txt, 110))}</summary>'
                            + "".join(f"<p>{_e(p)}</p>" for p in parts) + "</details>")
            else:
                cell = _e(_fmt(v))
                if isinstance(v, (int, float)):
                    attrs += ' data-num="1"'
            tds.append(f"<td {attrs}>{cell}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    sort_opts = "".join(f'<option value="{i}">{_e(c)}</option>' for i, (c, _k) in enumerate(cols))
    return (f'<div class="tabletools"><label>Sort by <select class="sortsel">'
            f'<option value="">Rank</option>{sort_opts}</select></label></div>'
            f'<div class="tablewrap"><table class="rt {cls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _csv_data_uri(rows, cols):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c for c, _ in cols])
    for r in rows:
        w.writerow([r.get(k, "") for _c, k in cols])
    return "data:text/csv;base64," + base64.b64encode(buf.getvalue().encode()).decode()


def _img(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _json_script(obj):
    """JSON safe to embed inside <script>."""
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


# ------------------------------------------------------------------ main

def normalize_rows(rows):
    """Accept dataset.csv rows or screen() summaries; add numeric risky + verdict."""
    out = []
    for r in rows:
        r = dict(r)
        if "verdict" not in r:
            v = r.get("label", "")
            if r.get("resolution"):
                v += f" ({r['resolution']})"
            r["verdict"] = v
        r.setdefault("source", "simulated")
        if r.get("reasons") and "bolted_fault:" in str(r["reasons"]):
            parts = str(r["reasons"]).split(" | ")
            parts.sort(key=lambda x: not x.startswith("bolted_fault:"))   # explanation first
            r["reasons"] = " | ".join(parts)
        r["risky"] = 1 if str(r["verdict"]).startswith("RISKY") else 0
        for k in ("n_ibr", "fault_bus"):
            if r.get(k) not in (None, ""):
                r[k] = int(float(r[k]))
        for k in ("p_risky", "clear_time_s", "scr_min", "ibr_share", "load_scale"):
            if r.get(k) not in (None, ""):
                r[k] = float(r[k])
        if r.get("clear_time_s") not in (None, ""):
            r["clear_ms"] = round(r["clear_time_s"] * 1000, 1)
        if r.get("ibr_share") not in (None, ""):
            r["ibr_pct"] = round(r["ibr_share"] * 100, 1)
        if isinstance(r.get("scr_min"), float) and r["scr_min"] >= 99:
            r["scr_min"] = ""
        out.append(r)
    return out


def _trajectory_html(traces, figures, out_path):
    """Interactive trajectories (from `traces`), or PNG figures as a fallback."""
    if traces:
        ids = list(traces)
        chips = []
        for i, k in enumerate(ids):
            d = traces[k]
            tone = "bad" if d.get("risky") else "good"
            chips.append(f'<button type="button" class="tchip {tone}" data-sid="{_e(k)}" '
                         f'aria-pressed="{"true" if i == 0 else "false"}"><i></i>{_e(k)}</button>')
        png = {}
        for k, pth in (figures or {}).items():
            pth = Path(pth)
            if pth.exists() and pth.parent.resolve() == Path(out_path).parent.resolve():
                png[k] = pth.name                       # sits next to the report
        return (
            "<section id='trajectories'><h2>Trajectories</h2>"
            "<p class='muted'>Voltage, rotor-angle spread, frequency and IBR current for the "
            "riskiest scenarios. Pick a scenario to watch it play out; hover or touch a chart to "
            "read values at that moment.</p>"
            f"<div class='tchips' role='group' aria-label='Scenario'>{''.join(chips)}</div>"
            "<div class='tcard'><div id='trhead'></div>"
            "<div class='ttools'><button type='button' class='btn ghost' id='replay'>Replay</button>"
            "<a class='btn ghost' id='pnglink' download>Download PNG</a></div>"
            "<div class='readout' id='readout' aria-live='polite'></div>"
            "<div id='trpanels'></div><div id='trfoot'></div></div>"
            f"<script id='trdata' type='application/json'>{_json_script(traces)}</script>"
            f"<script id='trpng' type='application/json'>{_json_script(png)}</script>"
            "</section>")
    if figures:
        chips = "".join(f'<button type="button" class="tchip" data-fig="f{i}" '
                        f'aria-pressed="{"true" if i == 0 else "false"}"><i></i>{_e(k)}</button>'
                        for i, k in enumerate(figures))
        imgs = "".join(f'<img id="f{i}" class="fig{" on" if i == 0 else ""}" alt="{_e(k)}" '
                       f'src="{_img(p)}">' for i, (k, p) in enumerate(figures.items()))
        return (f"<section id='trajectories'><h2>Trajectories</h2><p class='muted'>Voltage, "
                f"rotor-angle spread, frequency and IBR current for the riskiest scenarios.</p>"
                f"<div class='tchips' role='group' aria-label='Scenario'>{chips}</div>"
                f"<div class='figs'>{imgs}</div></section>")
    return ""


def build_report(rows, out_path, *, title="IBR-FaultScreen report", case="ieee39", dyr=None,
                 criteria_source="", model_card_md=None, cct_rows=None, figures=None,
                 traces=None, dataset_note="", top_n=10, table_limit=500):
    """Write the HTML report. `rows`: dataset.csv rows or screen() summaries
    (merged with features). `traces`: {scenario_id: plots.trace_payload(...)} for
    interactive trajectories; `figures`: {scenario_id: png path} (offered as a
    download, or shown directly when no traces are given)."""
    from .case import read_case

    import ibr_faultscreen
    rows = normalize_rows(rows)
    has_p = any(r.get("p_risky") not in (None, "") for r in rows)
    rows.sort(key=lambda r: (-r["risky"], -(r.get("p_risky") or 0),
                             -(r.get("clear_time_s") or 0)))
    n, n_risky = len(rows), sum(r["risky"] for r in rows)
    n_unres = sum("unresolved" in str(r["verdict"]) for r in rows)
    n_pred = sum(r["source"] == "predicted" for r in rows)
    share = n_risky / n if n else 0
    light = ("red", "High") if share >= 0.3 else ("amber", "Moderate") if share >= 0.1 \
        else ("green", "Low")

    sheets = read_case(case, dyr)
    layout, edges, layout_source = network_layout(sheets, case)
    gen_buses = {int(b) for name in ("GENROU", "GENSAL", "GENCLS") if name in sheets
                 for b in sheets[name]["bus"]}
    ibr_hv = set()
    for r in rows:
        for part in str(r.get("scr_by_ibr", "")).split(";"):
            if "@" in part:
                ibr_hv.add(int(part.split("@")[1].split(":")[0]))
    mdata = map_data(rows, layout)
    max_ibr = mdata["max"]
    last = {b: d["s"][max_ibr] for b, d in mdata["bus"].items()}
    map_html = (_map_svg(layout, edges, gen_buses, ibr_hv, last)
                + _map_svg(layout, edges, gen_buses, ibr_hv, last, tall=True))

    top = [r for r in rows if r["risky"]][:5]
    top_items = "".join(
        f"<li><b>{_e(r['scenario_id'])}</b>: fault at bus {_e(r.get('fault_bus'))}, cleared in "
        f"{_e(r.get('clear_ms'))} ms with {_e(r.get('n_ibr'))} IBR(s). "
        f"<span class='muted'>{_e(_short(r.get('reasons') or r.get('why') or ''))}</span></li>"
        for r in top) or "<li>No RISKY scenarios.</li>"
    bus_risk = {}
    for r in rows:
        if r.get("fault_bus") not in (None, ""):
            bus_risk.setdefault(r["fault_bus"], []).append(r["risky"])
    worst_buses = sorted(((b, sum(v) / len(v), len(v)) for b, v in bus_risk.items() if len(v) >= 2),
                         key=lambda t: -t[1])[:3]
    worst_txt = ", ".join(f"bus {b} ({s * 100:.0f}% of {c})" for b, s, c in worst_buses) or "n/a"

    cols = [("Scenario", "scenario_id"), ("Verdict", "verdict"), ("Source", "source")]
    if has_p:
        cols.append(("P(RISKY)", "p_risky"))
    cols += [("Fault bus", "fault_bus"), ("Clearing (ms)", "clear_ms"), ("IBRs", "ibrs"),
             ("IBR share (%)", "ibr_pct"), ("Lowest SCR", "scr_min"), ("Load ×", "load_scale"),
             ("Why", "reasons")]
    table = _table(rows, cols, limit=table_limit, linked=set(traces or ()))
    if n > table_limit:
        table = (f"<p class='muted'>Showing the {table_limit} highest-ranked of {n}; the CSV "
                 f"download has all of them.</p>") + table
    csv_uri = _csv_data_uri(rows, cols)

    cct_html = ""
    if cct_rows:
        cct_html = ("<section><h2>Critical clearing times</h2><p class='muted'>Bolted faults. "
                    "\"At least\" means longer clearing times could not be solved; the milder-fault "
                    "value is an upper estimate.</p>"
                    + _table(cct_rows, [("Scenario", "scenario_id"), ("Fault bus", "fault_bus"),
                                        ("Result", "text"), ("Status", "cct_status")], "cct")
                    + "</section>")

    fig_html = _trajectory_html(traces, figures, out_path)

    mc_html = ""
    if model_card_md:
        mc_html = ("<section><h2>Model card</h2><pre class='md'>" + _e(model_card_md)
                   + "</pre></section>")

    legend = "".join(f'<span><i style="background:{c}"></i>{lab}</span>' for c, lab in
                     zip(RISK_RAMP, ("0–20%", "20–40%", "40–60%", "60–80%", "80–100%")))
    legend += f'<span><i style="background:{NO_DATA}"></i>no faults sampled</span>'
    legend += '<span><i class="sq"></i>generator bus</span><span><i class="ring"></i>IBR high-side bus</span>'
    legend += '<span><i class="xf"></i>transformer</span>'

    slider = ""
    if max_ibr > 0:
        slider = f"""<div class="mapctl">
<button type="button" class="btn" id="play" aria-label="Play: add IBRs step by step">
<svg viewBox="0 0 16 16" aria-hidden="true"><path class="ico-play" d="M4 2.5v11l9-5.5z"/><path class="ico-pause" d="M3.5 2.5h3v11h-3zM9.5 2.5h3v11h-3z"/></svg><span>Play</span></button>
<div class="slider"><label for="nibr">Scenarios with at most <b id="nibrv">{max_ibr}</b> IBR(s)</label>
<input type="range" id="nibr" min="0" max="{max_ibr}" step="1" value="{max_ibr}"></div>
</div>"""

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {{ --teal:{TEAL}; --teal-d:{TEAL_DARK}; --ink:#1d1d1b; --ink2:#55544f; --line:#e6e5e0;
        --bg:#ffffff; --soft:#f4f8f8; --card:#f6f7f7; --bad:#b8401c; --warn:#a86b00; --good:#1b7a46;
        --halo:{RISK_RAMP[4]}; }}
* {{ box-sizing:border-box; }}
html {{ -webkit-text-size-adjust:100%; }}
body {{ margin:0; background:var(--bg); color:var(--ink); overflow-wrap:break-word;
       font:15px/1.55 "DM Sans", system-ui, -apple-system, "Segoe UI", sans-serif; }}
header {{ background:var(--bg); color:var(--ink); padding:30px 16px 0; }}
header .in, main {{ max-width:1120px; margin:0 auto; }}
header .in {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px;
             flex-wrap:wrap; padding-bottom:14px; border-bottom:3px solid var(--teal); }}
h1 {{ margin:0 0 6px; font-size:clamp(21px, 4.5vw, 26px); font-weight:700; color:#111; letter-spacing:-.01em; }}
h2 {{ color:#111; font-size:19px; font-weight:700; margin:0 0 10px; }}
header p {{ margin:2px 0; color:var(--ink2); font-size:13.5px; }}
header .ver {{ text-align:right; color:var(--ink2); font-size:12.5px; line-height:1.6; }}
@media (max-width:600px) {{ header .ver {{ text-align:left; }} }}
main {{ padding:8px 16px 48px; }}
section {{ margin:32px 0; animation:rise .5s ease both; scroll-margin-top:12px; }}
.banner {{ background:var(--soft); border-left:4px solid var(--teal); padding:10px 14px; border-radius:6px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:14px 0; }}
.kpi {{ background:var(--card); border-radius:10px; padding:14px 16px; animation:rise .5s ease both; }}
.kpi:nth-child(2) {{ animation-delay:.08s; }} .kpi:nth-child(3) {{ animation-delay:.16s; }} .kpi:nth-child(4) {{ animation-delay:.24s; }}
.kpi span {{ display:block; color:var(--ink2); font-size:11.5px; font-weight:700;
             letter-spacing:.06em; text-transform:uppercase; }}
.kpi b {{ display:block; font-size:28px; margin-top:4px; }}
.kpi b.bad {{ color:var(--bad); }} .kpi b.warn {{ color:#c4621a; }}
.light {{ display:inline-flex; align-items:center; gap:8px; font-weight:700; }}
.light i {{ width:14px; height:14px; border-radius:50%; display:inline-block; flex:none; }}
.light.red i {{ background:var(--bad); }} .light.amber i {{ background:#e0a100; }} .light.green i {{ background:var(--good); }}
.muted {{ color:var(--ink2); }}
ol {{ padding-left:22px; }}
.btn {{ display:inline-flex; align-items:center; gap:7px; background:var(--teal); color:#fff; padding:8px 15px;
        border-radius:8px; border:0; text-decoration:none; font:inherit; font-weight:700; font-size:13px; cursor:pointer; }}
.btn:hover {{ background:var(--teal-d); }}
.btn:focus-visible, button:focus-visible, select:focus-visible, .bus:focus-visible, summary:focus-visible, a:focus-visible {{ outline:3px solid #9fd8d9; outline-offset:2px; }}
.btn.ghost {{ background:#fff; color:var(--teal-d); border:1px solid #bfe0e1; }}
.btn.ghost:hover {{ background:var(--soft); }}
.btn svg {{ width:14px; height:14px; fill:currentColor; }}
.btn .ico-pause, .btn.playing .ico-play {{ display:none; }} .btn.playing .ico-pause {{ display:inline; }}
select {{ font:inherit; font-size:13.5px; padding:6px 8px; border-radius:6px; border:1px solid var(--line); background:#fff; color:var(--ink); }}

/* network map (animation as in SCR-Screen) */
.maptext {{ max-width:760px; }}
.mapwrap {{ border:1px solid var(--line); border-radius:12px; padding:6px; margin:12px 0 10px; }}
svg.map {{ width:100%; height:auto; display:block; }}
svg.map.tall {{ display:none; }}
@media (max-width:640px) {{ svg.map.wide {{ display:none; }} svg.map.tall {{ display:block; max-width:440px; margin:0 auto; }} }}
.map .edge {{ stroke:#b9b8b2; stroke-opacity:.8; stroke-width:1.6; animation:fade .6s ease both; }}
.map .edge.xf {{ stroke-dasharray:5 4; }}
.map .node {{ stroke:#fff; stroke-width:2; transition:fill .45s ease; animation:pop .45s ease-out both;
              transform-box:fill-box; transform-origin:center; }}
.map .node.ibr {{ stroke:var(--teal); stroke-width:3.5; }}
.map .bus {{ cursor:pointer; outline:none; }}
.map .bus:hover .node, .map .bus.sel .node {{ stroke:#111; }}
.map .halo {{ fill:none; stroke:var(--halo); stroke-width:2.5; opacity:0; transform-box:fill-box;
              transform-origin:center; pointer-events:none; }}
.map .bus.hot .halo {{ animation:pulse 1.8s ease-out 1.2s infinite both; }}
.map .lbl {{ font-size:11.5px; font-weight:700; text-anchor:middle; fill:#1d1d1b; pointer-events:none;
             animation:fade .5s ease 1s both; }}
.map.tall .lbl {{ font-size:15.5px; }}
.map .bus.dark .lbl {{ fill:#fff; }}
.mapctl {{ display:flex; align-items:center; gap:12px 18px; flex-wrap:wrap; background:var(--card);
           border-radius:10px; padding:12px 16px; }}
.slider {{ flex:1 1 260px; min-width:0; }} .slider label {{ font-size:14px; }}
.slider input {{ width:100%; accent-color:var(--teal); margin:6px 0 0; }}
.mapcap {{ font-size:14px; margin:8px 0 6px; min-height:1.5em; }}
.businfo {{ font-size:13.5px; color:var(--ink2); min-height:1.5em; margin:4px 0; }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12px; color:var(--ink2); margin-top:6px; }}
.legend i {{ display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:5px; vertical-align:-2px; }}
.legend i.sq {{ border-radius:3px; background:#fff; border:1.5px solid #9a9993; }}
.legend i.ring {{ background:#fff; border:3px solid var(--teal); }}
p.small {{ font-size:12px; margin:6px 0 0; }}
.legend i.xf {{ width:18px; height:0; border-radius:0; border-top:2px dashed #9a9993; vertical-align:3px; }}

/* tables: fit the width on desktop, cards on phones; never scroll sideways */
.tabletools {{ display:none; margin:6px 0; font-size:13.5px; }}
.tablewrap {{ border:1px solid var(--line); border-radius:10px; max-height:640px; overflow-y:auto; overflow-x:hidden; }}
table.rt {{ border-collapse:collapse; width:100%; font-size:13px; }}
table.rt th {{ position:sticky; top:0; z-index:1; background:var(--soft); text-align:left; padding:0; vertical-align:bottom; }}
table.rt th button {{ all:unset; display:block; box-sizing:border-box; width:100%; padding:8px; font-weight:700;
                     cursor:pointer; line-height:1.3; }}
table.rt th[aria-sort="ascending"] button::after {{ content:" ▲"; font-size:9px; color:var(--teal-d); }}
table.rt th[aria-sort="descending"] button::after {{ content:" ▼"; font-size:9px; color:var(--teal-d); }}
table.rt td {{ padding:8px; border-top:1px solid var(--line); vertical-align:top; overflow-wrap:break-word; }}
table.rt td[data-num] {{ text-align:right; font-variant-numeric:tabular-nums; }}
table.rt th.c-fault_bus, table.rt th.c-clear_ms, table.rt th.c-ibr_pct, table.rt th.c-scr_min,
table.rt th.c-load_scale, table.rt th.c-p_risky {{ text-align:right; }}
table.rt td.c-scenario_id {{ font-weight:700; }}
table.rt td.c-source {{ white-space:nowrap; }}
table.rt td.c-reasons, table.rt td.c-text {{ color:var(--ink2); min-width:160px; width:30%; }}
table.rt tbody tr {{ transition:background .2s; }}
table.rt tbody tr:hover {{ background:#fafaf8; }}
.sclink {{ color:var(--teal-d); text-decoration:underline; text-decoration-color:#bfe0e1; text-underline-offset:3px; }}
.chip {{ display:inline-block; background:var(--card); border-radius:6px; padding:1px 6px; margin:0 4px 3px 0;
         font-size:12px; white-space:nowrap; }}
details summary {{ cursor:pointer; list-style:none; }}
details summary::-webkit-details-marker {{ display:none; }}
details summary::after {{ content:" more ▾"; color:var(--teal-d); font-weight:700; font-size:11.5px; white-space:nowrap; }}
details[open] summary {{ display:none; }}
details p {{ margin:0 0 6px; }}
.pill {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px; font-weight:700; white-space:nowrap; }}
.pill.bad {{ background:#fbe3d9; color:var(--bad); }} .pill.good {{ background:#e0f2e8; color:var(--good); }}
.pill.warn {{ background:#fff1cc; color:var(--warn); }}
@media (max-width:960px) {{
  .tabletools {{ display:block; }}
  .tablewrap {{ border:0; max-height:none; overflow:visible; }}
  table.rt, table.rt tbody, table.rt tr, table.rt td {{ display:block; width:auto; }}
  table.rt thead {{ display:none; }}
  table.rt tr {{ display:grid; grid-template-columns:1fr 1fr; gap:6px 14px; border:1px solid var(--line);
                border-radius:12px; padding:12px 14px; margin:0 0 10px; }}
  table.rt td {{ border:0; padding:0; text-align:left !important; min-width:0 !important; width:auto !important; }}
  table.rt td::before {{ content:attr(data-label); display:block; font-size:10.5px; font-weight:700;
                        letter-spacing:.05em; text-transform:uppercase; color:var(--ink2); }}
  table.rt td.c-scenario_id {{ font-size:15px; }}
  table.rt td.c-scenario_id::before, table.rt td.c-verdict::before {{ display:none; }}
  table.rt td.c-verdict {{ justify-self:end; }}
  table.rt td.c-reasons, table.rt td.c-ibrs, table.rt td.c-text {{ grid-column:1 / -1; }}
  table.rt td.c-ibrs {{ order:8; }} table.rt td.c-reasons, table.rt td.c-text {{ order:9; }}
  table.rt td:empty {{ display:none; }}
}}

/* hosting bars */
.host {{ max-width:760px; }}
.hrow {{ display:grid; grid-template-columns:72px 1fr minmax(120px, auto); gap:12px; align-items:center;
         margin:7px 0; font-size:14px; }}
.track {{ background:var(--card); border-radius:5px; height:16px; overflow:hidden; }}
.track.empty {{ background:repeating-linear-gradient(45deg, #f4f4f2 0 6px, #fbfbfa 6px 12px); }}
.hbar {{ height:100%; border-radius:5px; background:{RISK_RAMP[3]}; animation:grow .8s cubic-bezier(.2,.8,.2,1) both; }}
@media (max-width:480px) {{ .hrow {{ grid-template-columns:62px 1fr; }} .hval {{ grid-column:2; margin-top:-6px; font-size:13px; }} }}

/* trajectories */
.tchips {{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 12px; }}
.tchip {{ display:inline-flex; align-items:center; gap:7px; font:inherit; font-size:13px; font-weight:500;
          background:#fff; border:1px solid var(--line); border-radius:999px; padding:5px 12px; cursor:pointer; color:var(--ink); }}
.tchip i {{ width:9px; height:9px; border-radius:50%; background:var(--ink2); }}
.tchip.bad i {{ background:var(--bad); }} .tchip.good i {{ background:var(--good); }}
.tchip[aria-pressed="true"] {{ border-color:var(--teal); background:var(--soft); font-weight:700; box-shadow:inset 0 0 0 1px var(--teal); }}
.tcard {{ border:1px solid var(--line); border-radius:12px; padding:16px clamp(10px, 2.5vw, 22px) 14px; }}
#trhead h3 {{ margin:0 0 4px; font-size:17px; display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; }}
#trhead p {{ margin:2px 0; color:var(--ink2); font-size:13px; }}
#trhead .warnline {{ color:#c4501f; font-weight:700; }}
.ttools {{ display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 6px; }}
.readout {{ display:flex; flex-wrap:wrap; gap:4px 14px; font-size:12.5px; color:var(--ink2); min-height:4.4em; align-content:flex-start;
            background:var(--card); border-radius:8px; padding:6px 10px; margin:6px 0 4px; font-variant-numeric:tabular-nums; }}
.readout b {{ color:var(--ink); }}
.readout span i {{ display:inline-block; width:10px; height:3px; border-radius:2px; vertical-align:3px; margin-right:5px; }}
.panel {{ margin-top:12px; }}
.plegend {{ display:flex; flex-wrap:wrap; gap:2px 14px; font-size:12px; color:var(--ink2); margin:0 0 2px; }}
.plegend .ttl {{ color:var(--ink); font-weight:700; margin-right:4px; }}
.plegend i {{ display:inline-block; width:16px; height:0; border-top:3px solid; vertical-align:3px; margin-right:5px; }}
.plegend i.dash {{ border-top-style:dotted; }}
.plegend i.area {{ height:10px; border:0; vertical-align:-1px; border-radius:2px; }}
.plegend i.area.acc {{ background:rgba(235,104,52,.3); }}
.plegend i.area.dec {{ background:rgba(42,120,214,.3); }}
.eacp {{ margin-top:22px; padding-top:12px; border-top:1px solid var(--line, #e6e5e0); }}
.eactxt {{ font-size:13px; line-height:1.5; margin:8px 0 0; }}
.plegend i.band {{ height:10px; border:0; background:#d9d8d2; vertical-align:-1px; border-radius:2px; }}
.plegend i.shade {{ height:10px; border:0; background:#ecebe6; vertical-align:-1px; border-radius:2px; }}
.panel svg {{ display:block; width:100%; overflow:visible; touch-action:pan-y; }}
.panel .ax {{ font-size:11px; fill:var(--ink2); }}
.panel .gl {{ stroke:#eeede8; }}
.panel .lim {{ stroke:#55544f; stroke-dasharray:5 4; stroke-width:1; }}
.panel .limt {{ font-size:10.5px; fill:var(--ink2); paint-order:stroke; stroke:#fff; stroke-width:3px; }}
.panel .cur {{ stroke:var(--teal); stroke-width:1.5; }}
.panel .hov {{ stroke:#1d1d1b; stroke-width:1; stroke-dasharray:3 3; }}
.panel .empty {{ font-size:13px; fill:var(--ink2); }}
#trfoot ul {{ margin:12px 0 0; padding-left:18px; font-size:12.5px; color:var(--ink2); }}
#trfoot li {{ margin:3px 0; }}
.figs img {{ display:none; width:100%; border:1px solid var(--line); border-radius:8px; }}
.figs img.on {{ display:block; animation:rise .4s ease both; }}
pre.md {{ white-space:pre-wrap; background:var(--soft); padding:14px; border-radius:8px; font-size:12.5px; overflow-wrap:anywhere; }}
ul.limits li {{ margin:4px 0; }}
footer {{ color:var(--ink2); font-size:12px; max-width:1120px; margin:0 auto; padding:0 16px 30px; overflow-wrap:anywhere; }}
@keyframes fade {{ from {{ opacity:0; }} }}
@keyframes rise {{ from {{ opacity:0; transform:translateY(8px); }} }}
@keyframes pop {{ 0% {{ transform:scale(0); }} 70% {{ transform:scale(1.3); }} 100% {{ transform:scale(1); }} }}
@keyframes pulse {{ 0% {{ transform:scale(1); opacity:.75; }} 100% {{ transform:scale(2.4); opacity:0; }} }}
@keyframes grow {{ from {{ width:0; }} }}
@media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ animation:none !important; transition:none !important; }} }}
@media print {{ .mapctl, .ttools, .tabletools, .tchips {{ display:none; }} * {{ animation:none !important; }}
  .tablewrap {{ max-height:none; overflow:visible; }} }}
</style></head>
<body>
<header><div class="in">
<div><h1>{_e(title)}</h1>
<p>Case: {_e(case)} · {n} scenarios · Criteria: {_e(criteria_source or 'not recorded')}</p></div>
<div class="ver">IBR-FaultScreen v{_e(ibr_faultscreen.__version__)}<br>{date.today().isoformat()}</div>
</div></header>
<main>
<section>
<p class="banner"><b>Screening tool.</b> It ranks contingencies for detailed study and does
not replace an interconnection study. {_e(dataset_note)}</p>
<h2>Summary</h2>
<p class="light {light[0]}"><i></i>{light[1]} risk: {n_risky} of {n} scenarios ({share * 100:.0f}%) are RISKY.</p>
<div class="kpis">
<div class="kpi"><span>RISKY scenarios</span><b class="{'bad' if n_risky else ''}">{n_risky} of {n}</b></div>
<div class="kpi"><span>OK scenarios</span><b>{n - n_risky}</b></div>
<div class="kpi"><span>Unresolved (detailed study)</span><b class="{'warn' if n_unres else ''}">{n_unres}</b></div>
<div class="kpi"><span>Verified by simulation</span><b>{n - n_pred} of {n}</b></div>
</div>
<p><b>Riskiest fault locations:</b> {_e(worst_txt)}.</p>
<p><b>Top risks:</b></p><ol>{top_items}</ol>
<p><b>What to study next:</b> run detailed studies (commercial RMS and, where IBRs are close to the fault or the SCR is low, EMT) for the top-ranked scenarios and every "unresolved" one.</p>
</section>

<section><h2>Network map</h2>
<p class="muted maptext">Each bus is shaded by the share of faults at that bus that were RISKY. Squares are generator buses; a teal outline marks an IBR's high-side bus in at least one scenario; a pulsing ring marks buses where at least {HALO_SHARE * 100:.0f}% of faults were RISKY. Click or tap a bus for details.{' Press Play to watch risk grow as more synchronous machines are replaced by IBRs.' if max_ibr > 0 else ''}</p>
{slider}
<p class="mapcap" id="mapcap" aria-live="polite"></p>
<div class="mapwrap">{map_html}</div>
<p class="businfo" id="businfo" aria-live="polite">Select a bus to see its results.</p>
<div class="legend">{legend}</div>
<p class="muted small">{LAYOUT_NOTES[layout_source]}</p>
<script id="mapdata" type="application/json">{_json_script(mdata)}</script>
</section>

<section><h2>Ranked scenarios</h2>
<p class="muted">Riskiest first. "Source" says whether the verdict comes from a simulation or only from the classifier. IBRs are listed as generator:type@terminal bus. Click a column title to sort{'; click a scenario name to see its trajectories' if traces else ''}.</p>
<p><a class="btn" download="ibr_faultscreen_scenarios.csv" href="{csv_uri}">Download table (CSV)</a></p>
{table}
</section>
{cct_html}
<section><h2>Risk versus IBR share</h2>
<p class="muted">Share of RISKY scenarios for each band of IBR share of generation, with the number of scenarios in each band.</p>
{_hosting_html(rows)}
</section>
{fig_html}
{mc_html}
<section><h2>Limits</h2><ul class="limits">{''.join(f'<li>{_e(x)}</li>' for x in LIMITS)}</ul></section>
</main>
<footer>Generated by IBR-FaultScreen (GPL-3.0-or-later) · https://github.com/ishaibu/ibr-faultscreen</footer>
<script>
{_JS.replace("__RAMP__", json.dumps(list(RISK_RAMP))).replace("__NONE__", NO_DATA).replace("__HALO__", str(HALO_SHARE))}
</script>
</body></html>"""
    Path(out_path).write_text(page, encoding="utf-8")
    return Path(out_path)


_JS = r"""
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function $(s, r) { return (r || document).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
  function readJSON(id) { var e = document.getElementById(id); return e ? JSON.parse(e.textContent) : null; }

  /* ---------------- network map ---------------- */
  var ramp = __RAMP__, none = '__NONE__', HALO = __HALO__;
  var M = readJSON('mapdata');
  function color(s) { return (s === null || s === undefined) ? none
      : ramp[Math.min(ramp.length - 1, Math.floor(s * ramp.length))]; }
  var sl = $('#nibr'), lab = $('#nibrv'), cap = $('#mapcap'), info = $('#businfo'), selBus = null;
  function level() { return sl ? +sl.value : M.max; }
  function busText(b, m) {
    var d = M.bus[b]; if (!d) return '';
    var s = d.s[m], c = d.c[m];
    return 'Bus ' + b + ': ' + (s === null ? 'no faults sampled here'
      : Math.round(s * 100) + '% RISKY (' + Math.round(s * c) + ' of ' + c + ' fault' + (c === 1 ? '' : 's') + ')')
      + (M.max > 0 ? ', scenarios with at most ' + m + ' IBR(s).' : '.');
  }
  function paint() {
    var m = level();
    if (lab) lab.textContent = m;
    $$('.map .bus').forEach(function (g) {
      var b = g.getAttribute('data-bus'), d = M.bus[b], s = d ? d.s[m] : null;
      var node = g.querySelector('.node');
      node.setAttribute('fill', color(s));
      g.classList.toggle('hot', s !== null && s >= HALO);
      g.classList.toggle('dark', s !== null && s >= 0.6);
      g.querySelector('title').textContent = busText(b, m);
    });
    var t = M.tot[m];
    if (cap) cap.innerHTML = (M.max > 0 ? 'With at most <b>' + m + '</b> IBR(s): ' : '')
      + '<b>' + t[0] + '</b> of ' + t[1] + ' scenario' + (t[1] === 1 ? '' : 's') + ' RISKY'
      + (t[1] ? ' (' + Math.round(100 * t[0] / t[1]) + '%)' : '') + '.';
    if (selBus !== null && info) info.textContent = busText(selBus, m);
  }
  $$('.map .bus').forEach(function (g) {
    function pick() {
      selBus = g.getAttribute('data-bus');
      $$('.map .bus').forEach(function (x) { x.classList.toggle('sel', x.getAttribute('data-bus') === selBus); });
      paint();
    }
    g.addEventListener('click', pick);
    g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
  });
  var play = $('#play'), timer = null;
  function stop() { clearInterval(timer); timer = null; if (play) { play.classList.remove('playing');
      play.querySelector('span').textContent = 'Play'; } }
  if (sl) sl.addEventListener('input', function () { stop(); paint(); });
  if (play) play.addEventListener('click', function () {
    if (timer) { stop(); return; }
    play.classList.add('playing'); play.querySelector('span').textContent = 'Pause';
    if (+sl.value >= M.max) sl.value = 0;
    paint();
    timer = setInterval(function () {
      if (+sl.value >= M.max) { stop(); return; }
      sl.value = +sl.value + 1; paint();
    }, reduce ? 600 : 1100);
  });
  paint();

  /* ---------------- tables ---------------- */
  $$('.tablewrap').forEach(function (wrap) {
    var tb = $('table', wrap), body = tb.tBodies[0], ths = $$('th', tb);
    var original = Array.prototype.slice.call(body.rows);
    var sel = wrap.previousElementSibling && $('.sortsel', wrap.previousElementSibling);
    function sortBy(i, asc) {
      var rows = original.slice();
      if (i !== null) rows.sort(function (a, b) {
        var x = a.cells[i].getAttribute('data-v'), y = b.cells[i].getAttribute('data-v');
        var nx = parseFloat(x), ny = parseFloat(y), r;
        if (x === '' && y !== '') return 1; if (y === '' && x !== '') return -1;
        r = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y, undefined, {numeric: true});
        return asc ? r : -r;
      });
      rows.forEach(function (r) { body.appendChild(r); });
      ths.forEach(function (th, j) { th.setAttribute('aria-sort', j === i ? (asc ? 'ascending' : 'descending') : 'none'); });
    }
    ths.forEach(function (th, i) {
      $('button', th).addEventListener('click', function () {
        var asc = th.getAttribute('aria-sort') !== 'ascending';
        sortBy(i, asc); if (sel) sel.value = String(i);
      });
    });
    if (sel) sel.addEventListener('change', function () {
      if (sel.value === '') sortBy(null, true);
      else { var i = +sel.value, v = ths[i].className;
        var num = /fault_bus|clear_ms|ibr_pct|scr_min|load_scale|p_risky|verdict/.test(v);
        sortBy(i, !num || /scr_min/.test(v)); }
    });
  });

  /* ---------------- trajectories ---------------- */
  var TR = readJSON('trdata'), PNG = readJSON('trpng') || {};
  var chips = $$('.tchip');
  if (!TR) {   // PNG fallback
    chips.forEach(function (c) { c.addEventListener('click', function () {
      chips.forEach(function (x) { x.setAttribute('aria-pressed', x === c ? 'true' : 'false'); });
      $$('.figs img').forEach(function (im) { im.classList.toggle('on', im.id === c.getAttribute('data-fig')); });
    }); });
    return;
  }
  var NS = 'http://www.w3.org/2000/svg', cur = null, anim = null, views = [], eacView = null;
  function S(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function H(tag, cls, html, parent) {
    var e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html;
    if (parent) parent.appendChild(e); return e;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function ticks(lo, hi, n) {
    var span = hi - lo; if (!(span > 0)) return [lo];
    var step = Math.pow(10, Math.floor(Math.log10(span / n))), err = span / n / step;
    step *= err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    var out = [], v = Math.ceil(lo / step) * step;
    for (; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  }
  function fmtTick(v, step) { var d = step < 1 ? Math.min(3, Math.ceil(-Math.log10(step))) : 0; return v.toFixed(d); }
  function pathOf(t, y, X, Y) {
    var d = '', pen = false;
    for (var i = 0; i < t.length; i++) {
      if (y[i] === null) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + X(t[i]).toFixed(1) + ' ' + Y(y[i]).toFixed(1); pen = true;
    }
    return d;
  }
  function bandOf(t, lo, hi, X, Y) {
    var up = '', dn = '';
    for (var i = 0; i < t.length; i++) if (hi[i] !== null && lo[i] !== null)
      up += (up ? 'L' : 'M') + X(t[i]).toFixed(1) + ' ' + Y(hi[i]).toFixed(1);
    for (var j = t.length - 1; j >= 0; j--) if (hi[j] !== null && lo[j] !== null)
      dn += 'L' + X(t[j]).toFixed(1) + ' ' + Y(lo[j]).toFixed(1);
    return up ? up + dn + 'Z' : '';
  }
  function legendHTML(p) {
    var h = '<span class="ttl">' + esc(p.label) + '</span>', seenDash = false;
    (p.series || []).forEach(function (s) {
      if (s.dash) { if (!seenDash) { h += '<span><i class="dash" style="border-color:' + s.color + '"></i>reactive part</span>'; seenDash = true; } return; }
      h += '<span><i style="border-color:' + s.color + '"></i>' + esc(s.name) + '</span>';
    });
    if (p.band) h += '<span><i class="band"></i>' + esc(p.band_name) + '</span>';
    if (p.shade_name) h += '<span><i class="shade"></i>' + esc(p.shade_name) + '</span>';
    return h;
  }
  function render(d) {
    var root = $('#trpanels'); root.innerHTML = ''; views = []; eacView = null;
    var w = root.clientWidth || 600, small = w < 520;
    var m = {l: small ? 40 : 50, r: 10, t: 10, b: 26}, Hh = small ? 150 : 175;
    var t = d.t, t0 = t[0], t1 = t[t.length - 1];
    d.panels.forEach(function (p, pi) {
      var wrap = H('div', 'panel', '', root);
      H('div', 'plegend', legendHTML(p), wrap);
      var svg = S('svg', {width: w, height: Hh, viewBox: '0 0 ' + w + ' ' + Hh, role: 'img',
                          'aria-label': p.label + ' versus time'}, wrap);
      var pw = w - m.l - m.r, ph = Hh - m.t - m.b;
      if (p.empty) { S('text', {x: m.l, y: Hh / 2, 'class': 'empty'}, svg).textContent = p.empty; return; }
      var lo = p.ylim[0], hi = p.ylim[1];
      var X = function (v) { return m.l + (v - t0) / (t1 - t0) * pw; };
      var Y = function (v) { return m.t + (1 - (Math.min(Math.max(v, lo - (hi - lo)), hi + (hi - lo)) - lo) / (hi - lo)) * ph; };
      var yt = ticks(lo, hi, small ? 3 : 4), ys = yt.length > 1 ? yt[1] - yt[0] : 1;
      yt.forEach(function (v) {
        S('line', {x1: m.l, x2: m.l + pw, y1: Y(v), y2: Y(v), 'class': 'gl'}, svg);
        S('text', {x: m.l - 6, y: Y(v) + 4, 'class': 'ax', 'text-anchor': 'end'}, svg).textContent = fmtTick(v, ys);
      });
      var xt = ticks(t0, t1, small ? 4 : 8), xs = xt.length > 1 ? xt[1] - xt[0] : 1;
      xt.forEach(function (v) {
        S('line', {x1: X(v), x2: X(v), y1: m.t, y2: m.t + ph, 'class': 'gl'}, svg);
        S('text', {x: X(v), y: Hh - 8, 'class': 'ax', 'text-anchor': 'middle'}, svg).textContent = fmtTick(v, xs);
      });
      if (pi === d.panels.length - 1)
        S('text', {x: m.l + pw, y: Hh - 8, 'class': 'ax', 'text-anchor': 'end', dy: '-12'}, svg).textContent = 'time (s)';
      if (p.shade) S('rect', {x: X(p.shade[0]), y: m.t, width: Math.max(1, X(p.shade[1]) - X(p.shade[0])),
                               height: ph, fill: '#ecebe6'}, svg);
      (p.faint || []).forEach(function (v) { if (v > lo && v < hi)
        S('line', {x1: m.l, x2: m.l + pw, y1: Y(v), y2: Y(v), stroke: '#e3e2dc', 'stroke-width': 1}, svg); });
      var cid = 'clip' + pi + Math.random().toString(36).slice(2, 7);
      var clip = S('rect', {x: m.l - 2, y: 0, width: pw + 4, height: Hh}, S('clipPath', {id: cid}, S('defs', {}, svg)));
      var g = S('g', {'clip-path': 'url(#' + cid + ')'}, svg);
      if (p.band) S('path', {d: bandOf(t, p.band[0], p.band[1], X, Y), fill: '#d9d8d2', 'fill-opacity': 0.55}, g);
      (p.series || []).forEach(function (s) {
        var a = {d: pathOf(t, s.y, X, Y), fill: 'none', stroke: s.color, 'stroke-width': s.w,
                 'stroke-linejoin': 'round', 'stroke-linecap': 'round'};
        if (s.dash) a['stroke-dasharray'] = '1.5 3.5';
        S('path', a, g);
      });
      (p.limits || []).forEach(function (L) {
        if (L.y < lo || L.y > hi) return;
        S('line', {x1: m.l, x2: m.l + pw, y1: Y(L.y), y2: Y(L.y), 'class': 'lim'}, svg);
        S('text', {x: m.l + 5, y: Y(L.y) - 4, 'class': 'limt'}, svg).textContent = L.text;
      });
      var cline = S('line', {x1: m.l, x2: m.l, y1: m.t, y2: m.t + ph, 'class': 'cur', opacity: 0}, svg);
      var hline = S('line', {x1: m.l, x2: m.l, y1: m.t, y2: m.t + ph, 'class': 'hov', opacity: 0}, svg);
      var hit = S('rect', {x: m.l, y: m.t, width: pw, height: ph, fill: 'transparent'}, svg);
      views.push({p: p, clip: clip, cline: cline, hline: hline, X: X, pw: pw, l: m.l});
      function move(ev) {
        var r = svg.getBoundingClientRect(), x = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left;
        var tt = t0 + Math.min(Math.max((x - m.l) / pw, 0), 1) * (t1 - t0);
        hover(tt);
      }
      hit.addEventListener('pointermove', move);
      hit.addEventListener('pointerdown', move);
      hit.addEventListener('pointerleave', function () { hover(null); });
    });
    eacView = renderEac(d, root, w, small);
  }
  // Equal-area view: power against the equivalent rotor angle (first swing).
  function cummax(a) { var out = [], m = -Infinity; a.forEach(function (v) { if (v !== null && v > m) m = v; out.push(m); }); return out; }
  function renderEac(d, root, w, small) {
    var e = d.eac; if (!e) return null;
    var wrap = H('div', 'panel eacp', '', root);
    var lg = '<span class="ttl">Equal-area view (first swing)</span>';
    if (e.ok) lg += '<span><i class="area acc"></i>accelerating area ' + e.acc.toFixed(2) + '</span>' +
      (e.dec > 0.005 ? '<span><i class="area dec"></i>decelerating area ' + e.dec.toFixed(2) + '</span>' : '') +
      '<span><i style="border-color:#eb6834"></i>electrical power, fault on</span>' +
      '<span><i style="border-color:#2a78d6"></i>after clearing</span>' +
      '<span><i class="dash" style="border-color:#0b0b0b"></i>mechanical power</span>';
    H('div', 'plegend', lg, wrap);
    if (!e.ok) { H('p', 'muted eactxt', esc(e.text), wrap); return null; }
    var m = {l: small ? 40 : 50, r: 10, t: 10, b: 30}, Hh = small ? 200 : 240;
    var svg = S('svg', {width: w, height: Hh, viewBox: '0 0 ' + w + ' ' + Hh, role: 'img',
                        'aria-label': 'Equal-area view: power versus equivalent rotor angle'}, wrap);
    var pw = w - m.l - m.r, ph = Hh - m.t - m.b;
    var x0 = e.xlim[0], x1 = e.xlim[1] > e.xlim[0] ? e.xlim[1] : e.xlim[0] + 1, lo = e.ylim[0], hi = e.ylim[1];
    var X = function (v) { return m.l + (v - x0) / (x1 - x0) * pw; };
    var Y = function (v) { return m.t + (1 - (v - lo) / (hi - lo)) * ph; };
    var yt = ticks(lo, hi, small ? 3 : 4), ys = yt.length > 1 ? yt[1] - yt[0] : 1;
    yt.forEach(function (v) {
      S('line', {x1: m.l, x2: m.l + pw, y1: Y(v), y2: Y(v), 'class': 'gl'}, svg);
      S('text', {x: m.l - 6, y: Y(v) + 4, 'class': 'ax', 'text-anchor': 'end'}, svg).textContent = fmtTick(v, ys);
    });
    var xt = ticks(x0, x1, small ? 4 : 7), xs = xt.length > 1 ? xt[1] - xt[0] : 1;
    xt.forEach(function (v) {
      S('line', {x1: X(v), x2: X(v), y1: m.t, y2: m.t + ph, 'class': 'gl'}, svg);
      S('text', {x: X(v), y: Hh - 12, 'class': 'ax', 'text-anchor': 'middle'}, svg).textContent = fmtTick(v, xs);
    });
    S('text', {x: m.l + pw, y: Hh - 1, 'class': 'ax', 'text-anchor': 'end'}, svg).textContent = 'equivalent rotor angle (°)';
    S('text', {x: m.l + 4, y: m.t + 12, 'class': 'ax'}, svg).textContent = 'power (pu)';
    var cid = 'eclip' + Math.random().toString(36).slice(2, 7);
    var clip = S('rect', {x: m.l - 2, y: 0, width: pw + 4, height: Hh}, S('clipPath', {id: cid}, S('defs', {}, svg)));
    var g = S('g', {'clip-path': 'url(#' + cid + ')'}, svg);
    var n = e.d.length, kc = e.kc, i, a = '', b = '';
    for (i = 0; i <= kc; i++) a += (i ? 'L' : 'M') + X(e.d[i]).toFixed(1) + ' ' + Y(e.pm[i]).toFixed(1);
    for (i = kc; i >= 0; i--) a += 'L' + X(e.d[i]).toFixed(1) + ' ' + Y(e.acc_lo[i] === null ? e.pm[i] : e.acc_lo[i]).toFixed(1);
    if (a) S('path', {d: a + 'Z', fill: '#eb6834', 'fill-opacity': 0.22}, g);
    if (e.dec > 0.005) {
      for (i = kc; i < n; i++) b += (i > kc ? 'L' : 'M') + X(e.d[i]).toFixed(1) + ' ' + Y(e.pm[i]).toFixed(1);
      for (i = n - 1; i >= kc; i--) b += 'L' + X(e.d[i]).toFixed(1) + ' ' + Y(e.dec_hi[i] === null ? e.pm[i] : e.dec_hi[i]).toFixed(1);
      S('path', {d: b + 'Z', fill: '#2a78d6', 'fill-opacity': 0.22}, g);
    }
    var seg = function (from, to, key) { var s = ''; for (var j = from; j <= to; j++) s += (j > from ? 'L' : 'M') + X(e.d[j]).toFixed(1) + ' ' + Y(e[key][j]).toFixed(1); return s; };
    S('path', {d: seg(0, n - 1, 'pm'), fill: 'none', stroke: '#0b0b0b', 'stroke-width': 1.3, 'stroke-dasharray': '5 4'}, g);
    S('path', {d: seg(0, kc, 'pe'), fill: 'none', stroke: '#eb6834', 'stroke-width': 2.2, 'stroke-linejoin': 'round'}, g);
    S('path', {d: seg(kc, n - 1, 'pe'), fill: 'none', stroke: '#2a78d6', 'stroke-width': 2.2, 'stroke-linejoin': 'round'}, g);
    var mk = S('circle', {r: 4.5, cx: 0, cy: 0, fill: '#0b0b0b', opacity: 0}, svg);
    var tone = e.stable ? 'good' : 'bad';
    H('p', 'eactxt', '<span class="pill ' + tone + '">' + (e.stable ? 'first swing stable' : 'first swing unstable') + '</span> ' +
      esc(e.text) + '<br><span class="muted">Leading group: ' + esc(e.lead.join(', ')) + ' · against: ' + esc(e.others.join(', ')) +
      '. Orange area: energy the leading machines gain while the fault is on. Blue area: energy given back after clearing. ' +
      'The swing stops when the blue area equals the orange one; if it cannot, the machines lose step. ' +
      'This view covers rotor-angle stability only, from the simulated trajectories (no extrapolation).</span>', wrap);
    return {e: e, clip: clip, X: X, Y: Y, l: m.l, pw: pw, mk: mk, reach: cummax(e.d)};
  }
  function eacAt(tt) {   // index into the equal-area window for time tt, or -1 before it
    var e = eacView.e; if (tt < e.t[0]) return -1;
    return idxOf(e.t, Math.min(tt, e.t[e.t.length - 1]));
  }
  function idxOf(t, v) { var a = 0, b = t.length - 1;
    while (b - a > 1) { var c = (a + b) >> 1; if (t[c] <= v) a = c; else b = c; }
    return (v - t[a] <= t[b] - v) ? a : b; }
  function hover(tt) {
    var d = TR[cur], ro = $('#readout');
    if (tt === null) { views.forEach(function (v) { v.hline.setAttribute('opacity', 0); });
      if (eacView) eacView.mk.setAttribute('opacity', 0);
      ro.innerHTML = '<span>Hover or touch a chart to read values.</span>'; return; }
    var i = idxOf(d.t, tt), h = '<span><b>t = ' + d.t[i].toFixed(3) + ' s</b></span>';
    views.forEach(function (v) {
      var x = v.X(d.t[i]); v.hline.setAttribute('x1', x); v.hline.setAttribute('x2', x); v.hline.setAttribute('opacity', 1);
      (v.p.series || []).forEach(function (s) {
        if (s.y[i] === null || s.dash) return;
        h += '<span><i style="background:' + s.color + '"></i>' + esc(s.name) + ' <b>' +
             s.y[i].toFixed(v.p.nd) + (v.p.unit ? ' ' + v.p.unit : '') + '</b></span>';
      });
    });
    if (eacView) {
      var k = eacAt(d.t[i]), ev = eacView;
      if (k < 0 || d.t[i] > ev.e.t[ev.e.t.length - 1] + 1e-9) ev.mk.setAttribute('opacity', 0);
      else {
        ev.mk.setAttribute('cx', ev.X(ev.e.d[k])); ev.mk.setAttribute('cy', ev.Y(ev.e.pe[k])); ev.mk.setAttribute('opacity', 1);
        h += '<span><i style="background:#0b0b0b"></i>equivalent angle <b>' + ev.e.d[k].toFixed(1) + ' °</b>, P<sub>e</sub> <b>' +
             ev.e.pe[k].toFixed(2) + '</b>, P<sub>m</sub> <b>' + ev.e.pm[k].toFixed(2) + ' pu</b></span>';
      }
    }
    ro.innerHTML = h;
  }
  function animate() {
    if (anim) cancelAnimationFrame(anim);
    if (reduce) { views.forEach(function (v) { v.clip.setAttribute('width', v.pw + 4); });
      if (eacView) eacView.clip.setAttribute('width', eacView.pw + 4); return; }
    var d = TR[cur], ta = d.t[0], tb = d.t[d.t.length - 1];
    if (eacView) eacView.clip.setAttribute('width', 0);
    var start = null, dur = 2200;
    views.forEach(function (v) { v.clip.setAttribute('width', 0); v.cline.setAttribute('opacity', 1); });
    function step(ts) {
      if (start === null) start = ts;
      var f = Math.min((ts - start) / dur, 1);
      views.forEach(function (v) {
        var x = v.l + f * v.pw;
        v.clip.setAttribute('width', f * v.pw + 2);
        v.cline.setAttribute('x1', x); v.cline.setAttribute('x2', x);
        if (f >= 1) { v.clip.setAttribute('width', v.pw + 4); v.cline.setAttribute('opacity', 0); }
      });
      if (eacView) {
        var k = eacAt(ta + f * (tb - ta)), ev = eacView;
        ev.clip.setAttribute('width', f >= 1 ? ev.pw + 4 : k < 0 ? 0 : Math.max(0, ev.X(ev.reach[k]) - ev.l + 2));
      }
      if (f < 1) anim = requestAnimationFrame(step);
    }
    anim = requestAnimationFrame(step);
  }
  function show(id, doAnim) {
    var d = TR[id]; if (!d) return; cur = id;
    chips.forEach(function (c) { c.setAttribute('aria-pressed', c.getAttribute('data-sid') === id ? 'true' : 'false'); });
    var tone = d.risky ? 'bad' : 'good';
    var head = '<h3>' + esc(d.id) + ' <span class="pill ' + tone + '">' + esc(d.verdict) + '</span>' +
               (d.flags.length ? '<span class="muted" style="font-weight:500;font-size:13px">' + esc(d.flags.join(', ')) + '</span>' : '') + '</h3>' +
               '<p>' + esc(d.sub) + '</p><p>' + esc(d.ibrs) + '</p>' +
               (d.banner ? '<p class="warnline">' + esc(d.banner) + '</p>' : '');
    $('#trhead').innerHTML = head;
    var pl = $('#pnglink');
    if (PNG[id]) { pl.style.display = ''; pl.setAttribute('href', PNG[id]); pl.setAttribute('download', PNG[id]); }
    else pl.style.display = 'none';
    $('#replay').style.display = d.panels ? '' : 'none';
    $('#readout').style.display = d.panels ? '' : 'none';
    var foot = d.reasons.length ? '<ul>' + d.reasons.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul>' : '';
    $('#trfoot').innerHTML = foot;
    if (!d.panels) { $('#trpanels').innerHTML = '<p class="muted">No trajectories: ' + esc(d.message || 'the run did not produce results') + '</p>'; return; }
    render(d); hover(null);
    if (doAnim) animate();
  }
  chips.forEach(function (c) { c.addEventListener('click', function () { show(c.getAttribute('data-sid'), true); }); });
  $('#replay').addEventListener('click', function () { animate(); });
  $$('.sclink').forEach(function (a) { a.addEventListener('click', function () { show(a.getAttribute('data-sid'), true); }); });
  var rt = null, lastW = 0;
  window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () {
    var w = $('#trpanels').clientWidth; if (cur && w !== lastW) { lastW = w; render(TR[cur]); hover(null); } }, 150); });
  var first = chips.length ? chips[0].getAttribute('data-sid') : null;
  if (first) {
    show(first, false); lastW = $('#trpanels').clientWidth;
    // play the first scenario when the section scrolls into view
    var sec = $('#trajectories');
    if ('IntersectionObserver' in window && !reduce) {
      views.forEach(function (v) { v.clip.setAttribute('width', 0); });
      if (eacView) eacView.clip.setAttribute('width', 0);
      var io = new IntersectionObserver(function (es) { if (es[0].isIntersecting) { io.disconnect(); animate(); } }, {threshold: 0.25});
      io.observe(sec);
    }
  }
})();
"""
