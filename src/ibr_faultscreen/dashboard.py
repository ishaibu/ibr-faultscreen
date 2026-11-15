"""The dashboard: the same results as the report, laid out as an application.

One self-contained HTML file (works offline, no server). A sidebar of views,
filters that stay in place, summary cards, a network map with zoom, and details
for any bus or scenario. It holds no calculation of results: every verdict,
reason and number comes from the results or dataset file, exactly as the report
shows it. Filters only choose which scenarios are counted.

    build_dashboard(rows, "dashboard.html", case="ieee39")

`rows` are dataset.csv rows, results.csv rows (simulate/example) or screen()
summaries, as for report.build_report.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path

from .report import _JS as REPORT_JS
from .report import LAYOUT_NOTES, LIMITS, RISK_RAMP, network_layout, normalize_rows

#: Fault buses at or above this share of the chosen measure pulse on the map.
PULSE_SHARE = 0.6
#: Unknown-outcome share at which a fault bus counts as solver-hard (as in model.py).
SOLVER_HARD_SHARE = 0.2

_RT = re.compile(r"ride_through:[^:|]+: (?P<ibr>.+?) \(bus (?P<bus>\d+)\) (?P<q>voltage|frequency) in "
                 r"(?P<band>[\[(][^,]+, [^\])]+[\])]) for (?P<t>[\d.]+) s > (?P<lim>[\d.]+) s")


def _cls(r):
    """One-letter outcome: R RISKY, C confirmed, U unresolved, O OK (simulated),
    P OK (predicted), Q predicted RISKY not yet verified, N needs simulation."""
    v = str(r.get("verdict", ""))
    if v.startswith("RISKY"):
        return "C" if "confirmed" in v else "U" if "unresolved" in v else "R"
    if v.startswith("predicted RISKY"):
        return "Q"
    if v.startswith("needs simulation"):
        return "N"
    return "P" if r.get("source") == "predicted" else "O"


def failure_modes(reasons: str, resolution: str = "") -> str:
    """Letters for the failed checks found in a reasons text: a angle, v voltage
    recovery, r ride-through, f frequency. A confirmed scenario uses the reasons
    of its milder fault (the text after 'already RISKY:')."""
    txt = str(reasons or "")
    if resolution == "confirmed" or "already RISKY:" in txt:
        txt = txt.split("already RISKY:", 1)[-1] if "already RISKY:" in txt else ""
    out = ""
    if "angle_separation" in txt:
        out += "a"
    if "voltage_recovery" in txt:
        out += "v"
    if "ride_through" in txt:
        out += "r"
    if "frequency:" in txt or "frequency_" in txt:
        out += "f"
    return out


def band_name(band: str, quantity: str = "voltage") -> str:
    """Plain name for a band such as '(1.1, 1.2]': '1.1–1.2 pu'."""
    m = re.match(r"([\[(])([\d.]+), ([\d.]+)([\])])", band)
    if not m:
        return band
    lo, hi = float(m.group(2)), float(m.group(3))
    unit = "pu" if quantity == "voltage" else "Hz"
    if quantity == "voltage":
        if lo >= 1.0 and hi > 2:
            return f"above {lo:g} {unit}"
        if lo <= 0:
            return f"below {hi:g} {unit}"
    return f"{lo:g}–{hi:g} {unit}"


def exposures(reasons: str):
    """[(IBR, high-side bus, quantity, band, seconds, allowed seconds)] in a reasons text."""
    return [(m["ibr"], int(m["bus"]), m["q"], band_name(m["band"], m["q"]), float(m["t"]), float(m["lim"]))
            for m in _RT.finditer(str(reasons or ""))]


def _num(v, nd=None):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x):
        return None
    if math.isinf(x):
        return 1e9
    return round(x, nd) if nd is not None else x


def dashboard_data(rows, *, case="ieee39", dyr=None, title="", criteria_source="",
                   dataset_note="", model_eval=None, model_card_md=None, cct_rows=None,
                   traces=None, counting=None):
    """Everything the page needs, as plain JSON-ready values."""
    import ibr_faultscreen

    from .case import read_case
    rows = normalize_rows(rows)
    sheets = read_case(case, dyr)
    layout, edges, layout_source = network_layout(sheets, case)
    gen_buses = sorted({int(b) for name in ("GENROU", "GENSAL", "GENCLS") if name in sheets
                        for b in sheets[name]["bus"]})
    ibr_hv = set()
    texts, tix = [], {}
    rtx = []
    data = []
    for r in rows:
        for part in str(r.get("scr_by_ibr", "")).split(";"):
            if "@" in part:
                ibr_hv.add(int(part.split("@")[1].split(":")[0]))
        c = _cls(r)
        reasons = str(r.get("reasons") or "")
        ti = -1
        if reasons:
            ti = tix.setdefault(reasons, len(texts))
            if ti == len(texts):
                texts.append(reasons)
        i = len(data)
        for ibr, bus, q, band, t, lim in exposures(reasons):
            rtx.append([i, bus, q[0], band, round(t, 3), lim, ibr])
            ibr_hv.add(bus)
        ct = _num(r.get("clear_time_s"))
        data.append([
            str(r.get("scenario_id", "")),
            r.get("fault_bus") if r.get("fault_bus") not in (None, "") else None,
            None if ct is None else round(ct, 4),
            _num(r.get("load_scale"), 3),
            int(r.get("n_ibr") or 0) if str(r.get("n_ibr", "")).strip() not in ("",) else
            len([x for x in str(r.get("ibrs") or "").split(";") if x]),
            str(r.get("ibrs") or ""),
            c,
            failure_modes(reasons, str(r.get("resolution") or "")),
            _num(r.get("scr_min"), 2),
            _num(r.get("ibr_share"), 3),
            _num(r.get("p_risky"), 4),
            _num(r.get("rt_ratio_max"), 3),
            ti,
            str(r.get("status") or ""),
        ])
    andes_v = sorted({str(r.get("andes_version")) for r in rows if r.get("andes_version")})
    pos = {int(b): [round(x, 4), round(y, 4)] for b, (x, y) in layout.items()}
    return {
        "title": title, "case": str(case), "n": len(data),
        "version": ibr_faultscreen.__version__, "date": date.today().isoformat(),
        "andes": ", ".join(andes_v), "criteria": criteria_source or "not recorded",
        "counting": counting or "", "note": dataset_note,
        "layout_note": LAYOUT_NOTES[layout_source], "limits": LIMITS,
        "pos": pos, "edges": [[int(a), int(b), 1 if xf else 0] for a, b, xf in edges],
        "gens": gen_buses, "ibr_hv": sorted(ibr_hv),
        "rows": data, "texts": texts, "rtx": rtx,
        "model": _model_summary(model_eval), "card": model_card_md or "",
        "cct": [{"bus": c.get("fault_bus"), "text": c.get("text", ""), "status": c.get("cct_status", "")}
                for c in (cct_rows or [])],
        "has_traces": sorted(traces or {}),
        "has_eac": any(bool((traces[k].get("eac") or {}).get("ok")) for k in (traces or {})),
        "ramp": list(RISK_RAMP), "pulse": PULSE_SHARE, "hard": SOLVER_HARD_SHARE,
    }


def _model_summary(rep):
    """The parts of model.evaluate_models() output the Screening model view shows."""
    if not rep:
        return None
    out = {"models": {}, "target": rep.get("recall_target")}
    for name, m in (rep.get("models") or {}).items():
        out["models"][name] = {k: m.get(k) for k in ("recall", "precision", "auc", "ap", "flagged_share",
                                                     "unknown_flagged_share", "unknown_scored")}
        out["models"][name]["effort"] = [{"recall": e.get("recall_target"), "share": e.get("flagged_share")}
                                         for e in (m.get("effort") or [])]
    for k in ("n", "n_risky", "n_buses"):
        if k in rep:
            out[k] = rep[k]
    return out


def _json(obj):
    return json.dumps(obj, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")


def _traj_js():
    """The report's trajectory player (same code, so the two show the same plots)."""
    start = REPORT_JS.index("/* ---------------- trajectories ---------------- */")
    head = ("(function () {\n  'use strict';\n"
            "  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;\n"
            "  function $(s, r) { return (r || document).querySelector(s); }\n"
            "  function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }\n"
            "  function readJSON(id) { var e = document.getElementById(id); return e ? JSON.parse(e.textContent) : null; }\n  ")
    js = head + REPORT_JS[start:].strip()
    # Dashboard changes to the report's player: two charts per row on wide screens,
    # the equal-area view drawn in its own view (#eacroot), and a hook to redraw a
    # view when it is opened (a hidden view has no width).
    patches = [
        ("var root = $('#trpanels'); root.innerHTML = ''; views = []; eacView = null;\n"
         "    var w = root.clientWidth || 600, small = w < 520;",
         "var root = $('#trpanels'); root.innerHTML = ''; views = []; eacView = null;\n"
         "    var cols = window.matchMedia && window.matchMedia('(min-width: 1500px)').matches ? 2 : 1;\n"
         "    var w = Math.floor(((root.clientWidth || 600) - (cols - 1) * 24) / cols), small = w < 520;"),
        ("    eacView = renderEac(d, root, w, small);\n",
         "    var er = document.getElementById('eacroot');\n"
         "    if (er) { er.innerHTML = ''; var ew = er.clientWidth || 900;\n"
         "      var two = window.matchMedia && window.matchMedia('(min-width: 1100px)').matches;\n"
         "      var sw = two ? Math.floor((ew - 28) * 0.6) : ew;\n"
         "      eacView = renderEac(d, er, sw, sw < 520);\n"
         "      if (!er.firstChild) er.innerHTML = '<p class=\"muted\">No equal-area view for this scenario.</p>'; }\n"
         "    else eacView = renderEac(d, root, w, small);\n"),
        ("var m = {l: small ? 40 : 50, r: 10, t: 10, b: 30}, Hh = small ? 200 : 240;",
         "var m = {l: small ? 40 : 50, r: 10, t: 10, b: 30}, Hh = small ? 230 : (root.id === 'eacroot' ? 330 : 240);"),
        ("    $('#trhead').innerHTML = head;\n",
         "    $('#trhead').innerHTML = head; var eh = document.getElementById('eachead'); if (eh) eh.innerHTML = head;\n"),
        ("    if (!d.panels) { $('#trpanels').innerHTML = ",
         "    if (!d.panels) { var er0 = document.getElementById('eacroot'); eacView = null; views = [];\n"
         "      if (er0) er0.innerHTML = '<p class=\"muted\">No equal-area view: the run did not produce results.</p>';\n"
         "      $('#trpanels').innerHTML = "),
        ("  var rt = null, lastW = 0;\n",
         "  var rt = null, lastW = 0;\n"
         "  window.trRedraw = function (play) { if (!cur || !TR[cur].panels) return; render(TR[cur]); hover(null);\n"
         "    lastW = $('#trpanels').clientWidth; if (play) animate(); };\n"),
    ]
    for old, new in patches:
        if old not in js:
            raise RuntimeError("trajectory player changed; update dashboard._traj_js: " + old[:40])
        js = js.replace(old, new, 1)
    return js


def build_dashboard(rows, out_path, *, title="IBR-FaultScreen dashboard", case="ieee39", dyr=None,
                    criteria_source="", dataset_note="", model_eval=None, model_card_md=None,
                    cct_rows=None, traces=None, figures=None, counting=None, extra_head="",
                    explorer_html="", extra_body="", subtitle="", overview_note=""):
    """Write the dashboard HTML. `traces`: {scenario_id: plots.trace_payload(...)};
    `figures`: {scenario_id: png path next to the page} (offered as downloads).
    `explorer_html`, `extra_head`, `extra_body`, `overview_note` (HTML): used by the
    demo page only."""
    d = dashboard_data(rows, case=case, dyr=dyr, title=title, criteria_source=criteria_source,
                       dataset_note=dataset_note, model_eval=model_eval, model_card_md=model_card_md,
                       cct_rows=cct_rows, traces=traces, counting=counting)
    d["subtitle"] = subtitle
    d["ov_note"] = overview_note
    d["explorer"] = bool(explorer_html)
    png = {}
    for k, pth in (figures or {}).items():
        pth = Path(pth)
        if pth.exists() and pth.parent.resolve() == Path(out_path).parent.resolve():
            png[k] = pth.name
    chips = "".join(
        f'<button type="button" class="tchip {"bad" if traces[k].get("risky") else "good"}" data-sid="{_esc(k)}" '
        f'aria-pressed="{"true" if i == 0 else "false"}"><i></i>{_esc(k)}</button>'
        for i, k in enumerate(traces or {}))
    traj = eac = ""
    if traces:
        eac = (f"<div class='tchips' role='group' aria-label='Scenario'>{chips}</div>"
               "<div class='tcard'><div id='eachead'></div><div class='ttools'>"
               "<button type='button' class='btn ghost' id='eacreplay'>Replay</button></div>"
               "<div id='eacroot'></div></div>")
        traj = (f"<div class='tchips' role='group' aria-label='Scenario'>{chips}</div>"
                "<div class='tcard'><div id='trhead'></div>"
                "<div class='ttools'><button type='button' class='btn ghost' id='replay'>Replay</button>"
                "<a class='btn ghost' id='pnglink' download>Download PNG</a></div>"
                "<div class='readout' id='readout' aria-live='polite'></div>"
                "<div id='trpanels'></div><div id='trfoot'></div></div>"
                f"<script id='trdata' type='application/json'>{_json(traces)}</script>"
                f"<script id='trpng' type='application/json'>{_json(png)}</script>")
    page = (_PAGE.replace("__TITLE__", _esc(title))
            .replace("__CSS__", _CSS).replace("__TRAJ__", traj).replace("__EAC__", eac)
            .replace("__EXPLORER__", explorer_html)
            .replace("__HEAD__", extra_head).replace("__BODY__", extra_body)
            .replace("__DATA__", _json(d))
            .replace("__APPJS__", _APP_JS)
            .replace("__TRAJJS__", _traj_js() if traces else ""))
    Path(out_path).write_text(page, encoding="utf-8")
    return Path(out_path)


def _esc(x):
    return (str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ---------------------------------------------------------------- page

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>__CSS__</style>
__HEAD__
</head><body>
<a class="skip" href="#main">Skip to content</a>
<div class="app">
<aside class="side" aria-label="Views">
  <div class="brand"><b>IBR-FaultScreen</b><span>Fault risk dashboard</span></div>
  <nav class="tabs" id="tabs" aria-label="Views"></nav>
  <div class="allmenu" id="allmenu" hidden></div>
  <ul class="nav" id="nav"></ul>
  <div class="sfoot" id="sfoot"></div>
</aside>
<main id="main" tabindex="-1">
  <header class="top">
    <div class="ttl"><h1 id="title"></h1><p class="sub" id="subtitle"></p></div>
    <div class="actions"><button class="btn ghost" type="button" id="print">Print / PDF</button>
      <button class="btn ghost" type="button" id="csv">Download CSV</button></div>
  </header>
  <section class="filters" id="filters" aria-label="Filters">
    <button class="ftoggle" type="button" id="ftoggle" aria-expanded="false"><span id="fsum"></span><b>Edit</b></button>
    <div class="fgrp" id="g-ibr"><span class="flab" id="l-ibr">IBRs</span><div class="chips" id="f-ibr" role="group" aria-labelledby="l-ibr"></div></div>
    <div class="fgrp" id="g-ct"><label class="flab" for="f-ct">Clearing</label>
      <div class="range"><input type="range" id="f-ct" min="0" max="1" step="1"><output id="f-ctv" for="f-ct"></output></div></div>
    <div class="fgrp"><label class="flab" for="f-out">Outcome</label><select id="f-out"></select></div>
    <div class="fgrp"><label class="flab" for="f-mode">Failure mode</label><select id="f-mode">
      <option value="">Any</option><option value="r">Ride-through</option><option value="a">Rotor angle</option>
      <option value="v">Voltage recovery</option><option value="f">Frequency</option></select></div>
    <button class="reset" type="button" id="freset">Reset</button>
  </section>
  <section class="kpis" id="kpis" aria-label="Summary"></section>
  <p class="ovnote" id="ovnote" hidden></p>

  <section class="view" id="v-overview" aria-labelledby="h-overview">
    <h2 class="vh" id="h-overview">Overview</h2>
    <div class="grid2">
      <div class="card mapcard"><div class="chead"><h3>Network map</h3>
        <select class="mini" id="color-o" aria-label="Color the map by"></select></div>
        <p class="note" id="mapnote-o"></p>
        <div class="mapbox" id="map-o"></div><div class="mapctl" id="ctl-o"></div><div class="legend" id="legend-o"></div></div>
      <div class="card"><h3>Riskiest fault buses</h3><p class="note">With the filters above. Select a bus here or on the map.</p>
        <div class="tscroll short"><table class="tbl" id="rank"></table></div>
        <div class="busd" id="busd-o" aria-live="polite"></div></div>
    </div>
    <div class="grid2">
      <div class="card"><h3>Risk grows with IBRs</h3><p class="note">Share of scenarios that are RISKY, by number of IBRs.</p><div class="cols" id="c-nibr"></div></div>
      <div class="card"><h3>Why scenarios are RISKY</h3><p class="note">Failed checks (a scenario can fail more than one). A confirmed scenario counts with its milder fault's reasons.</p><div id="c-modes"></div></div>
    </div>
    <div class="card"><h3>Top risks</h3><ol class="toplist" id="toprisks"></ol>
      <p class="note">What to study next: detailed studies (commercial RMS and, where IBRs are close to the fault or the SCR is low, EMT) for the top-ranked scenarios and every unresolved one.</p></div>
  </section>

  <section class="view" id="v-map" aria-labelledby="h-map" hidden>
    <h2 class="vh" id="h-map">Network map</h2>
    <div class="grid2 wide">
      <div class="card mapcard"><div class="chead"><h3>Network map</h3>
        <select class="mini" id="color-m" aria-label="Color the map by"></select></div>
        <p class="note" id="mapnote-m"></p>
        <div class="mapbox big" id="map-m"></div><div class="mapctl" id="ctl-m"></div><div class="legend" id="legend-m"></div>
        <p class="note" id="layoutnote"></p></div>
      <div class="card"><h3>Selected bus</h3><div class="busd" id="busd-m" aria-live="polite"></div></div>
    </div>
  </section>

  <section class="view" id="v-scen" aria-labelledby="h-scen" hidden>
    <h2 class="vh" id="h-scen">Scenarios</h2>
    <div class="card">
      <div class="ttools2"><input type="search" id="q" placeholder="Search scenario, bus, generator or reason" aria-label="Search scenarios">
        <span class="note" id="scount"></span></div>
      <div class="tscroll"><table class="tbl sc" id="stab"></table></div>
      <div class="more"><button class="btn ghost" type="button" id="smore" hidden>Show more</button></div>
    </div>
  </section>

  <section class="view" id="v-modes" aria-labelledby="h-modes" hidden>
    <h2 class="vh" id="h-modes">Failure modes</h2>
    <div class="grid2">
      <div class="card"><h3>RISKY share by clearing time</h3><div class="cols" id="c-ct"></div></div>
      <div class="card"><h3>RISKY share by IBR share of generation</h3><div class="cols" id="c-share"></div></div>
    </div>
    <div class="card"><h3>Failure modes by number of IBRs</h3><p class="note">Share of scenarios failing each check.</p>
      <div class="tscroll"><table class="tbl" id="modetab"></table></div></div>
  </section>

  <section class="view" id="v-rt" aria-labelledby="h-rt" hidden>
    <h2 class="vh" id="h-rt">Ride-through</h2>
    <div class="card"><p class="note" id="rtnote"></p><div class="minikpis" id="rtk"></div></div>
    <div class="grid2">
      <div class="card"><h3>Scenarios exposed, by band</h3><div id="c-band"></div></div>
      <div class="card"><h3>Where IBRs are exposed</h3><p class="note">IBR high-side buses, with the filters above.</p>
        <div class="tscroll short"><table class="tbl" id="rttab"></table></div></div>
    </div>
    <div class="card" id="nearcard"><h3>Near misses</h3><p class="note">Scenarios that used 80% to 100% of an allowed ride-through time: not exposed, but close.</p>
      <div class="tscroll short"><table class="tbl" id="neartab"></table></div></div>
  </section>

  <section class="view" id="v-traj" aria-labelledby="h-traj" hidden>
    <h2 class="vh" id="h-traj">Trajectories</h2>
    <div class="card paper" id="trajectories"><p class="note pnote">Voltage, rotor-angle spread, frequency and IBR current over time. Pick a scenario to watch it play out; hover or touch a chart to read values. The equal-area view of the same scenario has its own page.</p>__TRAJ__</div>
  </section>

  <section class="view" id="v-eac" aria-labelledby="h-eac" hidden>
    <h2 class="vh" id="h-eac">Equal-area view</h2>
    <div class="card paper" id="eacview"><p class="note pnote">The first swing of the machines that separate first, drawn as power against their equivalent rotor angle. Pick a scenario; the choice is shared with Trajectories.</p>__EAC__</div>
  </section>

  <section class="view" id="v-model" aria-labelledby="h-model" hidden>
    <h2 class="vh" id="h-model">Machine-learning (ML) screening model</h2>
    <div id="modelbody"></div>
  </section>

  <section class="view" id="v-cct" aria-labelledby="h-cct" hidden>
    <h2 class="vh" id="h-cct">Critical clearing time</h2>
    <div class="card"><div class="tscroll"><table class="tbl" id="ccttab"></table></div></div>
  </section>

  <section class="view" id="v-explore" aria-labelledby="h-explore" hidden>
    <h2 class="vh" id="h-explore">Explorer &amp; quiz</h2>
    <div class="card paper"><p class="note" id="exmiss" hidden>Explorer data not found. Keep <code>explorer-data.js</code> in the same folder as this page (it is in <code>docs/demo/</code> of the repository).</p>__EXPLORER__</div>
  </section>

  <section class="view" id="v-method" aria-labelledby="h-method" hidden>
    <h2 class="vh" id="h-method">Method &amp; assumptions</h2>
    <div class="card prose" id="method"></div>
  </section>
  <footer class="foot" id="foot"></footer>
</main></div>
<script id="dashdata" type="application/json">__DATA__</script>
<script>__APPJS__</script>
<script>__TRAJJS__</script>
__BODY__
</body></html>"""


_CSS = r"""
:root{--side:#12304f;--side-on:#1b4470;--side-fg:#d3e0ee;--side-line:transparent;--accent:#0a9396;--accent-d:#077a7d;
--page:#eef1f4;--bg:#fff;--card:#f8f9fb;--fg:#1d2939;--muted:#5d6b7e;--line:#e4e7ec;--edge:#98a2b3;
--bad:#c4320a;--badbg:#fef3f2;--warn:#b54708;--warnbg:#fffaeb;--ok:#067647;--okbg:#ecfdf3;--unk:#475467;--unkbg:#f2f4f7;
--hl:#fff7e6;--focus:#7cc7c8;--shadow:0 1px 2px rgba(16,24,40,.05)}
@media (prefers-color-scheme:dark){:root{--side:#0c2238;--side-on:#163a5e;--side-line:#24405e;--page:#0b1220;--bg:#101828;
--card:#1d2939;--fg:#f2f4f7;--muted:#a4b0c0;--line:#344054;--edge:#667085;--bad:#f97066;--badbg:#3b1512;--warn:#fdb022;
--warnbg:#3a2508;--ok:#47cd89;--okbg:#0b3321;--unk:#cbd5e1;--unkbg:#1d2939;--hl:#2a2010;--shadow:none}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}html,body{margin:0}
body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;background:var(--page);color:var(--fg)}
button,select,input,output{font:inherit;color:inherit}
:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.skip{position:absolute;left:-999px;top:0;background:var(--bg);padding:8px 12px;z-index:30}.skip:focus{left:8px}
[hidden]{display:none!important}
.app{display:grid;grid-template-columns:240px minmax(0,1fr);min-height:100vh;background:linear-gradient(90deg,var(--side) 240px,transparent 240px)}.app>*{min-width:0}
/* sidebar */
.side{background:var(--side);color:var(--side-fg);position:sticky;top:0;height:100vh;display:flex;flex-direction:column;
 border-right:1px solid var(--side-line);z-index:20}
.brand{padding:20px 18px 14px}.brand b{display:block;color:#fff;font-size:19px;letter-spacing:.2px}
.brand span{font-size:12.5px;opacity:.85}
.nav{list-style:none;margin:6px 0;padding:0;flex:1;overflow:auto}
.nav button{width:100%;display:flex;align-items:center;justify-content:space-between;gap:8px;text-align:left;background:none;
 border:0;border-left:3px solid transparent;color:var(--side-fg);padding:9px 16px 9px 18px;cursor:pointer;font-size:14.5px}
.nav button:hover{background:rgba(255,255,255,.06)}
.nav button[aria-current=page]{background:var(--side-on);border-left-color:#7cc7c8;color:#fff;font-weight:600}
.cnt{min-width:22px;height:20px;border-radius:10px;background:#d92d20;color:#fff;font-size:11.5px;font-weight:700;
 display:inline-grid;place-items:center;padding:0 6px;font-variant-numeric:tabular-nums}
.cnt.w{background:#dc6803}.cnt.n{background:rgba(255,255,255,.2)}
.sfoot{padding:14px 18px 18px;font-size:12px;opacity:.82;line-height:1.5}
.tabs,.allmenu{display:none}
/* main */
main{padding:18px 24px 40px;outline:none;width:100%;max-width:1560px;margin:0 auto}
.top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px 16px;flex-wrap:wrap}
h1{font-size:20px;line-height:1.3;margin:0}.sub{color:var(--muted);font-size:13px;margin:3px 0 0}
.actions{display:flex;gap:8px}
.btn{display:inline-flex;align-items:center;gap:6px;background:var(--accent);color:#fff;border:1px solid var(--accent);border-radius:8px;
 padding:7px 13px;cursor:pointer;font-size:13.5px;font-weight:600;text-decoration:none}
.btn:hover{background:var(--accent-d)}.btn.ghost{background:var(--bg);color:var(--fg);border-color:var(--line);font-weight:500}
.btn.ghost:hover{border-color:var(--accent)}
select,input[type=search]{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:5px 9px;font-size:13.5px}
.filters{margin:14px 0;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 14px;display:flex;
 flex-wrap:wrap;align-items:center;gap:10px 24px;box-shadow:var(--shadow);position:sticky;top:0;z-index:10}
.fgrp{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.flab{font-size:11.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.chips{display:flex;gap:4px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:var(--card);border-radius:999px;min-width:34px;padding:3px 10px;cursor:pointer;font-size:13px}
.chip[aria-pressed=true]{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.range{display:flex;align-items:center;gap:8px;font-size:13px}.range input{width:120px;accent-color:var(--accent)}
.range output{min-width:9.5em;font-variant-numeric:tabular-nums}
.reset{color:var(--accent);background:none;border:0;cursor:pointer;font-weight:600;font-size:13px;padding:4px}
.ftoggle{display:none}
.kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-bottom:14px}
.kpi{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:11px 14px;box-shadow:var(--shadow);min-width:0}
.ovnote{font-size:12.5px;color:var(--muted);margin:-6px 0 14px}body:not(.ov) .ovnote{display:none}.ovnote a{color:var(--accent)}
.kpi .ls{display:none}.kpi em{font-style:normal}
.kpi>span{display:block;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.kpi>b{display:block;font-size:25px;line-height:1.25;margin-top:3px;font-variant-numeric:tabular-nums}
.kpi>i{display:block;font-style:normal;font-size:12.5px;color:var(--muted)}
body:not(.ov) .kpis{gap:8px}body:not(.ov) .kpi{padding:7px 12px}body:not(.ov) .kpi>b{font-size:19px}body:not(.ov) .kpi>i{display:none}
body.nof .filters,body.nof .kpis{display:none}.sub a{color:var(--accent)}
.bad{color:var(--bad)}.warn{color:var(--warn)}.okc{color:var(--ok)}
.vh{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
.grid2{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:14px;margin-bottom:14px}
.grid2.wide{grid-template-columns:minmax(0,2fr) minmax(280px,1fr)}
.card{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:14px 16px;min-width:0;margin-bottom:14px;box-shadow:var(--shadow)}
.grid2>.card{margin-bottom:0}
.card h3{font-size:16px;margin:0 0 4px}.note{color:var(--muted);font-size:12.5px;margin:0 0 8px}
.chead{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap}
select.mini{font-size:12.5px;padding:3px 8px}
/* map */
.mapcard{display:flex;flex-direction:column}.mapbox{position:relative;background:var(--card);border-radius:10px;overflow:hidden;touch-action:none;user-select:none;height:430px;flex:1 0 auto;min-height:430px}
.mapbox.big{height:min(72vh,680px)}
.mapbox svg{display:block;width:100%;height:100%;cursor:grab}.mapbox svg.drag{cursor:grabbing}
.mapctl{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:6px}
.zoom{display:flex;gap:6px}
.zoom button{min-width:32px;height:30px;border-radius:8px;border:1px solid var(--line);background:var(--bg);cursor:pointer;font-size:16px;line-height:1;padding:0 8px}
.zoom button.fit{font-size:12px}
.mhint{font-size:11.5px;color:var(--muted)}
.edge{stroke:var(--edge);stroke-width:1.7;vector-effect:non-scaling-stroke}.edge.xf{stroke-dasharray:5 4}
.bus{cursor:pointer}.bus .nd{stroke:var(--bg);stroke-width:2}
.bus.gen .nd{stroke:#667085;stroke-width:2}.bus.ibr .nd{stroke:var(--accent);stroke-width:3.4}
.bus:hover .nd,.bus:focus .nd{stroke:var(--fg)}.bus.sel .nd{stroke:var(--fg);stroke-width:3.6}
.bus text{font-weight:700;text-anchor:middle;dominant-baseline:central;pointer-events:none;fill:#1d2939}
.bus text.w{fill:#fff}.bus text.g{fill:var(--fg)}
.halo{fill:none;stroke-width:2.5;opacity:0;transform-box:fill-box;transform-origin:center;pointer-events:none}
.bus.hot .halo{animation:pl 1.8s ease-out infinite}
@keyframes pl{0%{transform:scale(1);opacity:.85}100%{transform:scale(2.3);opacity:0}}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin-top:8px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.legend i.sq{border-radius:3px;background:var(--bg);border:2px solid #667085}.legend i.ring{background:var(--bg);border:3px solid var(--accent)}
.legend i.xf{width:18px;height:0;border-radius:0;border-top:2px dashed var(--edge);vertical-align:3px}
/* tables */
.tscroll{max-height:520px;overflow:auto;border-radius:8px}.tscroll.short{max-height:320px}
.tbl{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums}
.tbl th{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);text-align:left;padding:7px 8px;
 border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:1;white-space:nowrap}
.tbl th button{all:unset;cursor:pointer}.tbl th[aria-sort=ascending] button::after{content:" ▲";font-size:9px}
.tbl th[aria-sort=descending] button::after{content:" ▼";font-size:9px}
.tbl td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.tbl .num{text-align:right}.tbl td.nw{white-space:nowrap}.tbl tr.click{cursor:pointer}.tbl tr.click:hover td{background:var(--card)}
.tbl tr.on td{background:var(--hl)}.tbl td.why{color:var(--muted);min-width:220px}
.tbl tr.detail td{background:var(--card);font-size:12.5px;color:var(--fg)}
.tbl tr.detail ul{margin:4px 0;padding-left:18px}
.tag{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:600;white-space:nowrap}
.tag.R,.tag.C{background:var(--badbg);color:var(--bad)}.tag.O{background:var(--okbg);color:var(--ok)}
.tag.U,.tag.N{background:var(--unkbg);color:var(--unk);border:1px dashed var(--muted)}.tag.P,.tag.Q{background:var(--warnbg);color:var(--warn)}
.gchip{display:inline-block;background:var(--card);border-radius:6px;padding:0 6px;margin:0 3px 2px 0;font-size:12px;white-space:nowrap}
.bar{height:8px;border-radius:4px;background:var(--line);overflow:hidden;min-width:56px}.bar i{display:block;height:100%;background:var(--bad)}
.ttools2{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px}.ttools2 input{flex:1 1 260px;max-width:420px}
.more{text-align:center;margin-top:10px}
.linkb{background:none;border:0;padding:0;color:var(--accent);cursor:pointer;font-weight:600;text-decoration:underline;text-underline-offset:2px}
/* bus details */
.busd h4{font-size:15.5px;margin:12px 0 6px;padding-top:10px;border-top:1px solid var(--line);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.busd>h4:first-child{border-top:0;padding-top:0;margin-top:6px}
.dgrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px 12px}
.dgrid span{display:block;font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.dgrid b{font-size:15px;font-variant-numeric:tabular-nums}
.busd ul{margin:6px 0 0;padding-left:18px;font-size:13px}.busd li{margin:3px 0}
/* charts */
.cols{display:flex;align-items:flex-end;gap:10px;height:170px;padding:8px 2px 0;border-bottom:1px solid var(--line)}
.cols .c{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;font-size:12px;min-width:0}
.cols .c i{display:block;width:100%;max-width:52px;border-radius:5px 5px 0 0;background:linear-gradient(var(--bad),#f97066);
 transition:height .45s cubic-bezier(.2,.8,.2,1)}
.cols .c small{color:var(--muted);font-variant-numeric:tabular-nums}
.colx{display:flex;gap:10px;padding:4px 2px 0}.colx span{flex:1;text-align:center;font-size:11.5px;color:var(--muted);min-width:0}
.colx span em{display:block;font-style:normal;opacity:.8}
.mrow{display:grid;grid-template-columns:140px minmax(0,1fr) 64px;gap:8px;align-items:center;font-size:13px;margin:6px 0}
.mrow .bar i{background:var(--accent)}.mrow .bar.gray i{background:#98a2b3}.mrow b{text-align:right;font-variant-numeric:tabular-nums}
.toplist{margin:4px 0 8px;padding-left:22px;font-size:13.5px}.toplist li{margin:5px 0}.toplist .muted{color:var(--muted)}
.minikpis{display:flex;gap:10px 28px;flex-wrap:wrap}.minikpis div span{display:block;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.minikpis div b{font-size:22px;font-variant-numeric:tabular-nums}
.defs{margin:10px 0 0;padding-left:18px;font-size:12.5px;color:var(--muted)}.defs li{margin:4px 0}.defs b{color:var(--fg)}
.prose{font-size:14px;max-width:880px}.prose h3{margin-top:14px}.prose li{margin:4px 0}
pre.md{white-space:pre-wrap;background:var(--card);padding:12px 14px;border-radius:8px;font-size:12.5px;overflow-wrap:anywhere;max-height:520px;overflow:auto}
.foot{color:var(--muted);font-size:12px;margin-top:10px;overflow-wrap:anywhere}
/* light "paper" panels: the report's trajectory player and the explorer */
.paper{--ink:#1d1d1b;--ink2:#55544f;--line:#e6e5e0;--soft:#f4f8f8;--card:#f6f7f7;--teal:#0a9396;--teal-d:#077a7d;
 --bad:#b8401c;--warn:#a86b00;--good:#1b7a46;background:#fff;color:#1d1d1b;color-scheme:light}
.paper .note{color:#55544f}
.paper .btn.ghost{background:#fff;color:#077a7d;border:1px solid #bfe0e1}.paper .btn.ghost:hover{background:#f4f8f8}
.paper .muted{color:#55544f}.paper h2{font-size:18px;margin:4px 0 8px}.paper section{margin:4px 0 28px}
.paper .small{font-size:12px}
.paper .pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap}
.paper .pill.bad{background:#fbe3d9;color:#b8401c}.paper .pill.good{background:#e0f2e8;color:#1b7a46}.paper .pill.warn{background:#fff1cc;color:#a86b00}
.tchips{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 12px}
.tchip{display:inline-flex;align-items:center;gap:7px;font-size:13px;background:#fff;border:1px solid #e6e5e0;border-radius:999px;padding:5px 12px;cursor:pointer;color:#1d1d1b}
.tchip i{width:9px;height:9px;border-radius:50%;background:#55544f}.tchip.bad i{background:#b8401c}.tchip.good i{background:#1b7a46}
.tchip[aria-pressed=true]{border-color:#0a9396;background:#f4f8f8;font-weight:700;box-shadow:inset 0 0 0 1px #0a9396}
.tcard{border:1px solid #e6e5e0;border-radius:12px;padding:16px clamp(10px,2.5vw,22px) 14px}
#trhead h3,#eachead h3{margin:0 0 4px;font-size:17px;display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px}
#trhead p,#eachead p{margin:2px 0;color:#55544f;font-size:13px}#trhead .warnline,#eachead .warnline{color:#c4501f;font-weight:700}
.ttools{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 6px}
.readout{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:#55544f;min-height:4.4em;align-content:flex-start;background:#f6f7f7;border-radius:8px;padding:6px 10px;margin:6px 0 4px;font-variant-numeric:tabular-nums}
.readout b{color:#1d1d1b}.readout span i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:3px;margin-right:5px}
.panel{margin-top:12px}.plegend{display:flex;flex-wrap:wrap;gap:2px 14px;font-size:12px;color:#55544f;margin:0 0 2px}
.plegend .ttl{color:#1d1d1b;font-weight:700;margin-right:4px}
.plegend i{display:inline-block;width:16px;height:0;border-top:3px solid;vertical-align:3px;margin-right:5px}
.plegend i.dash{border-top-style:dotted}.plegend i.area{height:10px;border:0;vertical-align:-1px;border-radius:2px}
.plegend i.area.acc{background:rgba(235,104,52,.3)}.plegend i.area.dec{background:rgba(42,120,214,.3)}
.plegend i.band{height:10px;border:0;background:#d9d8d2;vertical-align:-1px;border-radius:2px}
.plegend i.shade{height:10px;border:0;background:#ecebe6;vertical-align:-1px;border-radius:2px}
.eacp{margin-top:22px;padding-top:12px;border-top:1px solid #e6e5e0}.eactxt{font-size:13px;line-height:1.5;margin:8px 0 0}
.panel svg{display:block;width:100%;overflow:visible;touch-action:pan-y}
.panel .ax{font-size:11px;fill:#55544f}.panel .gl{stroke:#eeede8}.panel .lim{stroke:#55544f;stroke-dasharray:5 4;stroke-width:1}
.panel .limt{font-size:10.5px;fill:#55544f;paint-order:stroke;stroke:#fff;stroke-width:3px}
.panel .cur{stroke:#0a9396;stroke-width:1.5}.panel .hov{stroke:#1d1d1b;stroke-width:1;stroke-dasharray:3 3}
.panel .empty{font-size:13px;fill:#55544f}
@media (min-width:1500px){#trpanels{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);column-gap:24px}}
#eacroot .eacp{margin-top:6px;padding-top:0;border-top:0}#eacroot .eactxt{background:#f6f7f7;border-radius:10px;padding:12px 14px;line-height:1.6}
@media (min-width:1100px){#eacroot .eacp{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);column-gap:28px;align-items:start}
 #eacroot .plegend{grid-column:1/-1}#eacroot .eactxt{margin-top:0;font-size:13.5px}}
#trfoot ul{margin:12px 0 0;padding-left:18px;font-size:12.5px;color:#55544f}#trfoot li{margin:3px 0}
.paper .btn{background:#0a9396;border-color:#0a9396;color:#fff}.paper .btn.ghost{background:#fff;color:#077a7d;border-color:#bfe0e1}
@media (prefers-color-scheme:dark){.paper{border-color:#344054}}
/* tablet */
@media (max-width:1180px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.grid2,.grid2.wide{grid-template-columns:minmax(0,1fr)}
 .mapbox{height:min(60vh,520px)}}
@media (max-width:900px){
 .app{grid-template-columns:minmax(0,1fr);background:none}.side{height:auto;position:sticky;top:0}
 .brand{padding:10px 14px 4px;display:flex;align-items:baseline;gap:10px}.brand b{font-size:17px}.nav,.sfoot{display:none}
 .side{padding-top:env(safe-area-inset-top)}
 .tabs{display:flex;align-items:center;gap:0;padding:0 6px 6px;min-width:0}
 .tabs button{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;background:none;border:0;color:var(--side-fg);padding:7px 9px;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap;font-size:14px}
 .tabs button[aria-current=page]{color:#fff;font-weight:600;border-bottom-color:#7cc7c8}
 .tabs .all{margin-left:auto;flex:0 0 auto;border:1px solid rgba(255,255,255,.5);border-radius:999px;padding:4px 12px;max-width:48%}
 .tabs .all.on{background:var(--side-on);color:#fff;font-weight:600;border-color:#7cc7c8}
 .allmenu:not([hidden]){display:block;position:absolute;right:8px;top:100%;background:var(--bg);color:var(--fg);border:1px solid var(--line);
  border-radius:12px;box-shadow:0 12px 30px rgba(16,24,40,.22);padding:6px;min-width:250px;max-height:70vh;overflow:auto}
 .allmenu button{display:flex;width:100%;justify-content:space-between;align-items:center;gap:10px;background:none;border:0;padding:9px 12px;border-radius:8px;cursor:pointer;text-align:left}
 .allmenu button:hover,.allmenu button[aria-current=page]{background:var(--card)}
 .allmenu .sep{border-top:1px solid var(--line);margin:6px 4px}
 .filters{top:var(--sideh,88px)}
 main{padding:12px 12px 30px}
}
/* phone */
@media (max-width:640px){
 .actions{display:none}h1{font-size:17px}
 .filters{padding:8px 12px;gap:10px 16px;position:static}.filters .fgrp,.filters .reset{display:none}
 .filters.open .fgrp{display:flex}.filters.open .reset{display:inline}
 .ftoggle{display:flex;width:100%;justify-content:space-between;align-items:center;background:none;border:0;padding:2px 0;cursor:pointer;font-size:13.5px;text-align:left;gap:10px}
 .ftoggle b{color:var(--accent);flex:none}.ftoggle span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
 .kpis{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}body:not(.ov) .kpis,body:not(.ov) .ovnote{display:none}
 .kpi .lf{display:none}.kpi .ls{display:inline}
 #rank th:nth-child(5),#rank td:nth-child(5),#rttab th:nth-child(3),#rttab td:nth-child(3){display:none}
 .kpi{padding:8px 12px}.kpi>b{font-size:20px;margin-top:1px}.kpi>i{display:none}
 .dgrid{grid-template-columns:repeat(2,minmax(0,1fr))}.mrow{grid-template-columns:110px minmax(0,1fr) 56px}
 .mapbox,.mapbox.big{min-height:0}.legend{font-size:11.5px;gap:3px 10px}.mhint{font-size:11px}
 .tbl.sc thead{display:none}.tbl.sc,.tbl.sc tbody,.tbl.sc tr,.tbl.sc td{display:block}
 .tbl.sc tr.click{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:0 0 8px}
 .tbl.sc td{border:0;padding:0;text-align:left!important;min-width:0!important}
 .tbl.sc td::before{content:attr(data-l);display:block;font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
 .tbl.sc td.c-id{font-weight:700}.tbl.sc td.c-id::before,.tbl.sc td.c-v::before{display:none}.tbl.sc td.c-v{justify-self:end}
 .tbl.sc td.why,.tbl.sc td.c-ibrs{grid-column:1/-1}.tbl.sc td:empty{display:none}
 .tbl.sc tr.detail{border-radius:10px;margin:-6px 0 8px}.tbl.sc tr.detail td{padding:8px 10px}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
@media print{
 .side,.filters .ftoggle,.actions,.mapctl,.more,#v-explore,.ttools,.tchips,select.mini,.reset,.ovnote{display:none!important}
 .app{display:block}body{background:#fff;color:#000}main{padding:0}.filters{position:static;box-shadow:none;display:flex!important}
 .view{display:block!important;break-before:page}.vh{position:static;width:auto;height:auto;clip:auto;font-size:18px;margin:0 0 8px}
 .card{break-inside:avoid;box-shadow:none}.tscroll{max-height:none;overflow:visible}.mapbox{height:420px}
 .kpis{grid-template-columns:repeat(6,minmax(0,1fr))!important;display:grid!important}*{animation:none!important}
}
"""


_APP_JS = r"""
(function () {
'use strict';
var D = JSON.parse(document.getElementById('dashdata').textContent);
var NS = 'http://www.w3.org/2000/svg';
var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function $(s, r) { return (r || document).querySelector(s); }
function $$(s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); }
function esc(s) { return String(s === null || s === undefined ? '' : s).replace(/[&<>"]/g, function (c) {
  return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
function pct(a, b) { if (!b) return '–'; var x = 100 * a / b; return a && x < 1 ? '<1%' : Math.round(x) + '%'; }
function fmtN(n) { return Number(n).toLocaleString('en-US'); }
// row fields
var ID = 0, BUS = 1, CT = 2, LOAD = 3, NIBR = 4, IBRS = 5, CLS = 6, MODES = 7, SCR = 8, SHARE = 9, P = 10, RT = 11, TXT = 12, ST = 13;
var R = D.rows, N = R.length;
var RISKY = {R: 1, C: 1, U: 1};
var NAME = {R: 'RISKY', C: 'RISKY (confirmed)', U: 'RISKY (unresolved)', O: 'OK', P: 'OK (predicted)',
            Q: 'predicted RISKY, not verified', N: 'needs simulation, not verified'};
var MODEN = {a: 'Rotor angle', v: 'Voltage recovery', r: 'Ride-through', f: 'Frequency'};
var hasP = R.some(function (r) { return r[P] !== null; });
var hasRT = R.some(function (r) { return r[RT] !== null; });
var hasPred = R.some(function (r) { return 'PQN'.indexOf(r[CLS]) >= 0; });
var TR = !!D.has_traces.length, TRSET = {}; D.has_traces.forEach(function (k) { TRSET[k] = 1; });
var maxIbr = R.reduce(function (m, r) { return Math.max(m, r[NIBR]); }, 0);
var cycles = R.map(function (r) { return r[CT] === null ? null : Math.round(r[CT] * 60 * 100) / 100; });
var ctVals = cycles.filter(function (c) { return c !== null; }).map(function (c) { return Math.ceil(c - 1e-6); })
  .filter(function (v, i, a) { return a.indexOf(v) === i; }).sort(function (a, b) { return a - b; });
var GEN = {}; D.gens.forEach(function (b) { GEN[b] = 1; });
var IBRHV = {}; D.ibr_hv.forEach(function (b) { IBRHV[b] = 1; });

/* ---------------- state ---------------- */
var F = {ibr: {}, ct: ctVals.length ? ctVals.length - 1 : 0, out: '', mode: ''};
function resetF() { F.ibr = {}; for (var k = 0; k <= maxIbr; k++) F.ibr[k] = true; F.ct = ctVals.length ? ctVals.length - 1 : 0; F.out = ''; F.mode = ''; }
resetF();
var view = 'overview', selBus = null, color = {o: 'risky', m: 'risky'}, sortS = {k: null, asc: true}, shown = 100, openRow = null;
function inOut(c) {
  if (!F.out) return true;
  if (F.out === 'risky') return !!RISKY[c];
  if (F.out === 'ok') return c === 'O' || c === 'P';
  if (F.out === 'unv') return 'PQN'.indexOf(c) >= 0;
  return c === F.out;
}
function keep(r, i) {
  if (!F.ibr[r[NIBR]]) return false;
  if (ctVals.length && cycles[i] !== null && cycles[i] > ctVals[F.ct] + 1e-6) return false;
  if (!inOut(r[CLS])) return false;
  if (F.mode && r[MODES].indexOf(F.mode) < 0) return false;
  return true;
}
var FI = [];   // indices of filtered rows
function refilter() { FI = []; for (var i = 0; i < N; i++) if (keep(R[i], i)) FI.push(i); }

/* ---------------- views ---------------- */
var VIEWS = [['overview', 'Overview'], ['map', 'Network map'], ['scen', 'Scenarios'], ['modes', 'Failure modes'],
             ['rt', 'Ride-through'], ['traj', 'Trajectories'], ['eac', 'Equal-area view'], ['model', 'ML screening model'],
             ['cct', 'Critical clearing time'],
             ['explore', 'Explorer & quiz'], ['method', 'Method & assumptions']];
function available(k) {
  if (k === 'traj') return TR;
  if (k === 'eac') return TR && D.has_eac;
  if (k === 'model') return !!(D.model || D.card || hasP);
  if (k === 'cct') return D.cct.length > 0;
  if (k === 'explore') return D.explorer;
  if (k === 'map') return Object.keys(D.pos).length <= 400;
  return true;
}
var V = VIEWS.filter(function (v) { return available(v[0]); });
var SHORT = {overview: 'Overview', map: 'Map', scen: 'Scenarios', modes: 'Failure modes', rt: 'Ride-through', traj: 'Trajectories',
             eac: 'Equal-area', model: 'ML model', cct: 'CCT', explore: 'Explorer', method: 'Method'};
function badge(k) {
  var n = 0, cls = '';
  if (k === 'scen') n = FI.filter(function (i) { return RISKY[R[i][CLS]]; }).length;
  else if (k === 'rt') n = FI.filter(function (i) { return R[i][MODES].indexOf('r') >= 0; }).length;
  else if (k === 'map') { n = busStats().filter(function (b) { return b.n && b.R / b.n >= D.pulse; }).length; cls = 'w'; }
  else if (k === 'traj') { n = D.has_traces.length; cls = 'n'; }
  else return '';
  return n ? '<span class="cnt ' + cls + '">' + fmtN(n) + '</span>' : '';
}
function nav() {
  $('#nav').innerHTML = V.map(function (v) {
    return '<li><button type="button" data-v="' + v[0] + '"' + (v[0] === view ? ' aria-current="page"' : '') + '><span>' + esc(v[1]) + '</span>' + badge(v[0]) + '</button></li>'; }).join('');
  // phones and tablets: three fixed tabs and a menu button that names the current view
  var first = V.filter(function (v) { return v[0] === 'overview' || v[0] === 'map' || v[0] === 'scen'; });
  var inFirst = first.some(function (v) { return v[0] === view; });
  $('#tabs').innerHTML = first.map(function (v) {
    return '<button type="button" data-v="' + v[0] + '"' + (v[0] === view ? ' aria-current="page"' : '') + '>' + esc(SHORT[v[0]] || v[1]) + '</button>'; }).join('') +
    '<button type="button" class="all' + (inFirst ? '' : ' on') + '" id="allbtn" aria-haspopup="true" aria-expanded="' + (!$('#allmenu').hidden) +
    '" aria-label="All views">' + (inFirst ? 'More' : esc(SHORT[view] || view)) + ' ▾</button>';
  $('#allmenu').innerHTML = V.map(function (v) {
    return '<button type="button" data-v="' + v[0] + '"' + (v[0] === view ? ' aria-current="page"' : '') + '><span>' + esc(v[1]) + '</span>' + badge(v[0]) + '</button>'; }).join('') +
    '<div class="sep"></div><button type="button" id="mprint"><span>Print / PDF</span></button><button type="button" id="mcsv"><span>Download CSV</span></button>';
  $$('[data-v]').forEach(function (b) { b.onclick = function () { go(b.getAttribute('data-v')); $('#allmenu').hidden = true; }; });
  $('#allbtn').onclick = function (e) { e.stopPropagation(); var m = $('#allmenu'); m.hidden = !m.hidden; this.setAttribute('aria-expanded', !m.hidden); };
  $('#mprint').onclick = doPrint; $('#mcsv').onclick = doCsv;
}
document.addEventListener('click', function (e) { var m = $('#allmenu'); if (!m.hidden && !m.contains(e.target)) m.hidden = true; });
function go(k, noScroll) {
  if (!V.some(function (v) { return v[0] === k; })) k = 'overview';
  view = k;
  $$('.view').forEach(function (s) { s.hidden = s.id !== 'v-' + k; });
  document.body.classList.toggle('ov', k === 'overview');
  document.body.classList.toggle('nof', k === 'explore' || k === 'method');
  nav(); draw();
  if (k === 'traj' || k === 'eac') setTimeout(function () { if (window.trRedraw) window.trRedraw(true); }, 30);
  if (k === 'explore') setTimeout(function () { window.dispatchEvent(new Event('resize')); }, 30);
  if (!noScroll) { $('#main').scrollIntoView({block: 'start'}); window.scrollTo(0, 0); }
}

/* ---------------- filters ---------------- */
function initFilters() {
  $('#f-ibr').innerHTML = '';
  for (var k = 0; k <= maxIbr; k++) {
    var b = document.createElement('button'); b.type = 'button'; b.className = 'chip'; b.textContent = k;
    b.setAttribute('aria-pressed', 'true'); b.setAttribute('aria-label', k + ' IBR' + (k === 1 ? '' : 's'));
    (function (k, b) { b.onclick = function () {
      F.ibr[k] = !F.ibr[k]; if (!Object.keys(F.ibr).some(function (x) { return F.ibr[x]; })) F.ibr[k] = true;
      b.setAttribute('aria-pressed', F.ibr[k]); update(); }; })(k, b);
    $('#f-ibr').appendChild(b);
  }
  if (maxIbr === 0) $('#g-ibr').hidden = true;
  var ct = $('#f-ct');
  if (ctVals.length > 1) { ct.max = ctVals.length - 1; ct.value = F.ct; ct.oninput = function () { F.ct = +ct.value; update(); }; }
  else $('#g-ct').hidden = true;
  var o = [['', 'All'], ['risky', 'RISKY (all)'], ['R', 'RISKY (simulated)'], ['C', 'RISKY (confirmed)'], ['U', 'RISKY (unresolved)'], ['ok', 'OK']];
  if (hasPred) o.push(['unv', 'Not verified by simulation']);
  $('#f-out').innerHTML = o.map(function (x) { return '<option value="' + x[0] + '">' + x[1] + '</option>'; }).join('');
  $('#f-out').onchange = function () { F.out = this.value; update(); };
  $('#f-mode').onchange = function () { F.mode = this.value; update(); };
  $('#freset').onclick = function () { resetF(); $$('#f-ibr .chip').forEach(function (c) { c.setAttribute('aria-pressed', 'true'); });
    ct.value = F.ct; $('#f-out').value = ''; $('#f-mode').value = ''; update(); };
  $('#ftoggle').onclick = function () { var f = $('#filters'); f.classList.toggle('open'); this.setAttribute('aria-expanded', f.classList.contains('open')); };
}
function ctLabel() { if (!ctVals.length) return ''; var c = ctVals[F.ct]; return 'up to ' + c + ' cycle' + (c === 1 ? '' : 's') + ' (' + Math.round(c / 60 * 1000) + ' ms)'; }
function filterSummary() {
  var ks = Object.keys(F.ibr).filter(function (k) { return F.ibr[k]; });
  var p = [];
  if (maxIbr > 0) p.push(ks.length === maxIbr + 1 ? 'All IBRs' : 'IBRs ' + ks.join(', '));
  if (ctVals.length > 1) p.push('≤ ' + ctVals[F.ct] + ' cycles');
  if (F.out) p.push($('#f-out').selectedOptions[0].text);
  if (F.mode) p.push(MODEN[F.mode]);
  p.push(fmtN(FI.length) + ' of ' + fmtN(N));
  return p.join(' · ');
}

/* ---------------- KPI cards ---------------- */
function cnt(fn) { var n = 0; FI.forEach(function (i) { if (fn(R[i])) n++; }); return n; }
function kpis() {
  var n = FI.length, rk = cnt(function (r) { return RISKY[r[CLS]]; }), un = cnt(function (r) { return r[CLS] === 'U'; });
  var rt = cnt(function (r) { return r[MODES].indexOf('r') >= 0; }), an = cnt(function (r) { return r[MODES].indexOf('a') >= 0; });
  var six;
  if (hasPred) { var sim = cnt(function (r) { return 'PQN'.indexOf(r[CLS]) < 0; }); six = ['Simulated', fmtN(sim), pct(sim, n) + ' verified by simulation', ''];
  } else if (hasRT) { var nm = cnt(function (r) { return r[RT] !== null && r[RT] >= 0.8 && r[RT] <= 1; }); six = ['Near misses', fmtN(nm), '80–100% of an allowed time', nm ? 'warn' : ''];
  } else { var vf = cnt(function (r) { return /[vf]/.test(r[MODES]); }); six = ['Voltage / frequency', fmtN(vf), 'recovery or frequency', '', 'Volt. / freq.']; }
  var K = [['Scenarios', fmtN(n), n === N ? 'all scenarios' : 'of ' + fmtN(N) + ' (filtered)', ''],
           ['RISKY', fmtN(rk), pct(rk, n) + ' of scenarios', rk ? 'bad' : ''],
           ['Unresolved', fmtN(un), 'needs a detailed study', un ? 'warn' : ''],
           ['Ride-through', fmtN(rt), 'PRC-029 exposure', rt ? 'bad' : ''],
           ['Rotor angle', fmtN(an), 'machines lost step', an ? 'bad' : ''], six];
  $('#kpis').innerHTML = K.map(function (k) { var lab = k[4] ? '<em class="lf">' + k[0] + '</em><em class="ls">' + k[4] + '</em>' : k[0];
    return '<div class="kpi"><span>' + lab + '</span><b class="' + k[3] + '">' + k[1] + '</b><i>' + k[2] + '</i></div>'; }).join('');
  $('#fsum').textContent = filterSummary();
  $('#f-ctv').textContent = ctLabel();
}

/* ---------------- per-bus statistics ---------------- */
var _bs = null;
function busStats() {
  if (_bs) return _bs;
  var m = {};
  Object.keys(D.pos).forEach(function (b) { m[b] = {b: +b, n: 0, R: 0, U: 0, modes: {a: 0, v: 0, r: 0, f: 0}, ids: []}; });
  FI.forEach(function (i) {
    var r = R[i], s = m[r[BUS]]; if (!s) return;
    s.n++; if (RISKY[r[CLS]]) s.R++; if (r[CLS] === 'U') s.U++;
    for (var j = 0; j < r[MODES].length; j++) s.modes[r[MODES][j]]++;
    s.ids.push(i);
  });
  _bs = Object.keys(m).map(function (k) { return m[k]; });
  return _bs;
}
var ALLU = {};  // unknown-outcome share per fault bus over ALL rows (solver-hard)
(function () { var c = {}; R.forEach(function (r) { if (r[BUS] === null) return; var s = c[r[BUS]] = c[r[BUS]] || [0, 0]; s[0]++; if (r[CLS] === 'U' || /^(timeout|error)/.test(r[ST])) s[1]++; });
  Object.keys(c).forEach(function (b) { ALLU[b] = c[b][1] / c[b][0]; }); })();
function measure(s, how) {
  if (!s.n) return null;
  if (how === 'risky') return s.R / s.n;
  if (how === 'unres') return s.U / s.n;
  return s.modes[how] / s.n;
}
var MEAS = [['risky', 'RISKY share'], ['r', 'Ride-through share'], ['a', 'Rotor-angle share'], ['unres', 'Unresolved share']];
function rampColor(s) { if (s === null) return null; return D.ramp[Math.min(D.ramp.length - 1, Math.floor(s * D.ramp.length))]; }

/* ---------------- map ---------------- */
var maps = {};
function makeMap(key, host) {
  var M = {key: key, host: host, vb: null, full: null, tall: false, nodes: {}, w: host.clientWidth};
  maps[key] = M; buildMap(M); return M;
}
function buildMap(M) {
  var w = M.host.clientWidth || 800, h = M.host.clientHeight || 430, phone = w < 600;
  M.tall = phone || w / h < 0.85;
  var W = M.tall ? 600 : 960, H = M.tall ? 860 : 600, pad = 36, k = M.tall ? 1.3 : 1;
  function xy(b) { var p = D.pos[b], x = p[0], y = p[1]; if (M.tall) { var t = x; x = 1 - y; y = t; } return [pad + x * (W - 2 * pad), pad + y * (H - 2 * pad)]; }
  var svg = document.createElementNS(NS, 'svg'); svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', 'Network map');
  var g = document.createElementNS(NS, 'g'); svg.appendChild(g);
  D.edges.forEach(function (e) { var a = xy(e[0]), b = xy(e[1]), l = document.createElementNS(NS, 'line');
    l.setAttribute('x1', a[0]); l.setAttribute('y1', a[1]); l.setAttribute('x2', b[0]); l.setAttribute('y2', b[1]);
    l.setAttribute('class', 'edge' + (e[2] ? ' xf' : '')); g.appendChild(l); });
  M.nodes = {};
  Object.keys(D.pos).map(Number).sort(function (a, b) { return a - b; }).forEach(function (b) {
    var p = xy(b), gg = document.createElementNS(NS, 'g'), isGen = !!GEN[b], r = 15 * k, sh;
    gg.setAttribute('class', 'bus' + (isGen ? ' gen' : '') + (IBRHV[b] ? ' ibr' : ''));
    gg.setAttribute('tabindex', '0'); gg.setAttribute('role', 'button'); gg.setAttribute('data-bus', b);
    var halo = document.createElementNS(NS, 'circle'); halo.setAttribute('cx', p[0]); halo.setAttribute('cy', p[1]); halo.setAttribute('r', r + 2); halo.setAttribute('class', 'halo');
    if (isGen) { sh = document.createElementNS(NS, 'rect'); sh.setAttribute('x', p[0] - r); sh.setAttribute('y', p[1] - r); sh.setAttribute('width', 2 * r); sh.setAttribute('height', 2 * r); sh.setAttribute('rx', 4); }
    else { sh = document.createElementNS(NS, 'circle'); sh.setAttribute('cx', p[0]); sh.setAttribute('cy', p[1]); sh.setAttribute('r', r); }
    sh.setAttribute('class', 'nd');
    var t = document.createElementNS(NS, 'text'); t.setAttribute('x', p[0]); t.setAttribute('y', p[1]); t.textContent = b; 
    var ti = document.createElementNS(NS, 'title');
    gg.appendChild(halo); gg.appendChild(sh); gg.appendChild(t); gg.appendChild(ti); g.appendChild(gg);
    M.nodes[b] = {g: gg, sh: sh, t: t, ti: ti, halo: halo};
    function pick() { if (M.moved) return; selBus = b; drawSel(); }
    gg.addEventListener('click', pick);
    gg.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); selBus = b; drawSel(); } });
  });
  // fit the drawing tightly: the box around every bus (no empty bands)
  var xs = [], ys = [], rr = 15 * k + 6;
  Object.keys(D.pos).forEach(function (b) { var p = xy(b); xs.push(p[0]); ys.push(p[1]); });
  var x0 = Math.min.apply(null, xs) - rr, y0 = Math.min.apply(null, ys) - rr;
  M.full = [x0, y0, Math.max.apply(null, xs) + rr - x0, Math.max.apply(null, ys) + rr - y0];
  if (phone) M.host.style.height = Math.round(Math.max(300, Math.min(window.innerHeight * 0.82, w * M.full[3] / M.full[2]))) + 'px';
  else M.host.style.height = '';
  M.vb = M.vb && M.vbTall === M.tall ? M.vb : M.full.slice(); M.vbTall = M.tall;
  M.svg = svg; M.g = g; setVB(M);
  M.host.innerHTML = ''; M.host.appendChild(svg);
  var ctl = document.getElementById('ctl-' + M.key);
  ctl.innerHTML = '<span class="mhint">' + (M.key === 'o' && !phone ? 'Ctrl + scroll or pinch to zoom · drag to move' : 'Pinch or scroll to zoom · drag to move') + '</span>' +
    '<span class="zoom"><button type="button" aria-label="Zoom in">+</button><button type="button" aria-label="Zoom out">−</button>' +
    '<button type="button" class="fit" aria-label="Fit the map">Fit</button></span>';
  var zb = ctl.querySelectorAll('button');
  zb[0].onclick = function () { zoomAt(M, 0.7); }; zb[1].onclick = function () { zoomAt(M, 1 / 0.7); };
  zb[2].onclick = function () { M.vb = M.full.slice(); setVB(M); };
  panzoom(M);
}
function setVB(M) { M.svg.setAttribute('viewBox', M.vb.join(' ')); var s = M.vb[2] / M.full[2];
  // keep bus numbers readable at any zoom
  Object.keys(M.nodes).forEach(function (b) { var n = M.nodes[b]; n.t.style.fontSize = Math.max(8, (M.tall ? 17 : 14) * Math.max(s, 0.55)) + 'px'; }); }
function zoomAt(M, f, cx, cy) {
  var v = M.vb, nw = Math.min(M.full[2] * 1.3, Math.max(M.full[2] / 8, v[2] * f)), nh = nw * v[3] / v[2];
  cx = cx === undefined ? v[0] + v[2] / 2 : cx; cy = cy === undefined ? v[1] + v[3] / 2 : cy;
  M.vb = [cx - (cx - v[0]) * nw / v[2], cy - (cy - v[1]) * nh / v[3], nw, nh]; setVB(M);
}
function toSvg(M, x, y) { var r = M.svg.getBoundingClientRect(), v = M.vb; var s = Math.max(v[2] / r.width, v[3] / r.height);
  var ox = (r.width * s - v[2]) / 2, oy = (r.height * s - v[3]) / 2; return [v[0] - ox + (x - r.left) * s, v[1] - oy + (y - r.top) * s, s]; }
function panzoom(M) {
  var pts = {}, last = null, dist0 = null;
  M.svg.addEventListener('wheel', function (e) {
    if (M.key === 'o' && !e.ctrlKey && !e.metaKey) return;
    e.preventDefault(); var p = toSvg(M, e.clientX, e.clientY); zoomAt(M, e.deltaY > 0 ? 1.15 : 1 / 1.15, p[0], p[1]); }, {passive: false});
  M.svg.addEventListener('pointerdown', function (e) { pts[e.pointerId] = [e.clientX, e.clientY]; M.moved = false; last = [e.clientX, e.clientY];
    if (Object.keys(pts).length === 2) { var a = Object.values(pts); dist0 = Math.hypot(a[0][0] - a[1][0], a[0][1] - a[1][1]); } });
  M.svg.addEventListener('pointermove', function (e) {
    if (!pts[e.pointerId]) return; pts[e.pointerId] = [e.clientX, e.clientY]; var k = Object.keys(pts);
    if (k.length === 2) { var a = Object.values(pts), d = Math.hypot(a[0][0] - a[1][0], a[0][1] - a[1][1]);
      if (dist0) { var c = toSvg(M, (a[0][0] + a[1][0]) / 2, (a[0][1] + a[1][1]) / 2); zoomAt(M, dist0 / d, c[0], c[1]); } dist0 = d; M.moved = true; return; }
    var dx = e.clientX - last[0], dy = e.clientY - last[1];
    if (!M.moved && Math.hypot(dx, dy) < 4) return;
    if (!M.moved) { M.moved = true; try { M.svg.setPointerCapture(e.pointerId); } catch (err) {} }
    M.svg.classList.add('drag'); var s = toSvg(M, 0, 0)[2]; M.vb[0] -= dx * s; M.vb[1] -= dy * s; setVB(M); last = [e.clientX, e.clientY]; });
  function up(e) { delete pts[e.pointerId]; dist0 = null; M.svg.classList.remove('drag'); setTimeout(function () { M.moved = false; }, 0); }
  M.svg.addEventListener('pointerup', up); M.svg.addEventListener('pointercancel', up);
}
function paintMap(M) {
  var how = color[M.key], st = {};
  busStats().forEach(function (s) { st[s.b] = s; });
  Object.keys(M.nodes).forEach(function (b) {
    var n = M.nodes[b], s = st[b], m = s ? measure(s, how) : null, c = rampColor(m);
    if (GEN[b] && m === null) { n.sh.setAttribute('fill', 'var(--bg)'); }
    else n.sh.setAttribute('fill', c || 'var(--line)');
    n.t.setAttribute('class', m === null ? 'g' : m >= 0.6 ? 'w' : '');
    n.g.classList.toggle('hot', m !== null && m >= D.pulse);
    n.halo.setAttribute('stroke', c || 'transparent');
    n.g.classList.toggle('sel', +b === selBus);
    n.ti.textContent = 'Bus ' + b + (GEN[b] ? ' (generator)' : '') + (IBRHV[b] ? ' (IBR high side)' : '') +
      (s && s.n ? ': ' + s.R + ' of ' + s.n + ' faults RISKY' : ': no faults here with these filters');
    n.g.setAttribute('aria-label', n.ti.textContent);
  });
  var lab = MEAS.filter(function (x) { return x[0] === how; })[0][1];
  $('#mapnote-' + M.key).textContent = 'Each fault bus is shaded by its ' + lab.replace(/^R(?!ISKY)/, 'r').replace(/^([A-Z])([a-z])/, function (m, a, b) { return a.toLowerCase() + b; }) + ' (with the filters above); buses at ' +
    Math.round(D.pulse * 100) + '% or more pulse. Squares are generator buses' + (D.ibr_hv.length ? '; a teal outline marks an IBR high-side bus' : '') + '. Select a bus for details.';
  $('#legend-' + M.key).innerHTML = D.ramp.map(function (c, i) { return '<span><i style="background:' + c + '"></i>' + (i * 20) + '–' + (i * 20 + 20) + '%</span>'; }).join('') +
    '<span><i style="background:var(--line)"></i>no faults</span><span><i class="sq"></i>generator bus</span>' +
    (D.ibr_hv.length ? '<span><i class="ring"></i>IBR high-side bus</span>' : '') + '<span><i class="xf"></i>transformer</span>';
}
function initMapSelect(key) {
  var s = $('#color-' + key);
  s.innerHTML = MEAS.map(function (x) { return '<option value="' + x[0] + '">' + x[1] + '</option>'; }).join('');
  s.onchange = function () { color[key] = s.value; paintMap(maps[key]); };
}

/* ---------------- bus details ---------------- */
function busHTML(b) {
  var s = busStats().filter(function (x) { return x.b === b; })[0];
  if (!s) return '<p class="note">Select a bus.</p>';
  var hard = ALLU[b] !== undefined && ALLU[b] >= D.hard;
  var kind = GEN[b] ? '<span class="tag P">generator bus</span>' : '';
  if (IBRHV[b]) kind += ' <span class="tag O">IBR high side</span>';
  if (hard) kind += ' <span class="tag U">solver-hard</span>';
  var h = '<h4>Bus ' + b + ' ' + kind + '</h4>';
  if (!s.n) return h + '<p class="note">No fault at this bus with the filters above' + (GEN[b] ? ' (generator buses are not faulted in the demo)' : '') + '.</p>';
  h += '<div class="dgrid"><div><span>RISKY</span><b class="bad">' + pct(s.R, s.n) + '</b></div><div><span>Faults</span><b>' + fmtN(s.n) + '</b></div>' +
       '<div><span>Unresolved</span><b class="warn">' + s.U + '</b></div><div><span>Unknown outcome (all)</span><b>' + Math.round(100 * (ALLU[b] || 0)) + '%</b></div>' +
       'avrf'.split('').map(function (m) { return '<div><span>' + MODEN[m] + '</span><b>' + s.modes[m] + '</b></div>'; }).join('') + '</div>';
  var ex = s.ids.filter(function (i) { return RISKY[R[i][CLS]]; }).sort(function (a, b2) { return (R[b2][CT] || 0) - (R[a][CT] || 0); }).slice(0, 4);
  if (ex.length) h += '<ul>' + ex.map(function (i) { var r = R[i];
    return '<li><span class="tag ' + r[CLS] + '">' + NAME[r[CLS]] + '</span> ' + scLink(r[ID]) + ' · ' + cyc(i) + ' · ' + (r[IBRS] ? r[NIBR] + ' IBR' + (r[NIBR] === 1 ? '' : 's') : 'no IBRs') +
      (r[MODES] ? ' · ' + r[MODES].split('').map(function (m) { return MODEN[m].toLowerCase(); }).join(', ') : '') + '</li>'; }).join('') + '</ul>';
  if (hard) h += '<p class="note">Solver-hard: at least ' + Math.round(D.hard * 100) + '% of all faults here had an unknown outcome, so <code>screen</code> always simulates this bus.</p>';
  return h;
}
function cyc(i) { var c = cycles[i]; return c === null ? '' : (Math.round(c * 10) / 10) + ' cycles'; }
function scLink(id) { return TRSET[id] ? '<button type="button" class="linkb" data-traj="' + esc(id) + '">' + esc(id) + '</button>'
  : '<button type="button" class="linkb" data-scen="' + esc(id) + '">' + esc(id) + '</button>'; }
function wireLinks(root) {
  $$('[data-traj]', root).forEach(function (a) { a.onclick = function () { openTraj(a.getAttribute('data-traj')); }; });
  $$('[data-scen]', root).forEach(function (a) { a.onclick = function () { $('#q').value = a.getAttribute('data-scen'); shown = 100; openRow = a.getAttribute('data-scen'); go('scen'); }; });
}
function openTraj(id) { go('traj'); var c = $('.tchip[data-sid="' + id.replace(/"/g, '') + '"]'); if (c) setTimeout(function () { c.click(); }, 60); }
function drawSel() {
  ['o', 'm'].forEach(function (k) { var el = $('#busd-' + k); if (el) { el.innerHTML = busHTML(selBus); wireLinks(el); } if (maps[k]) paintMap(maps[k]); });
  $$('#rank tr[data-b]').forEach(function (t) { t.classList.toggle('on', +t.getAttribute('data-b') === selBus); });
}

/* ---------------- overview ---------------- */
function rankTable() {
  var bs = busStats().filter(function (s) { return s.n; }).sort(function (a, b) { return (b.R / b.n - a.R / a.n) || (b.n - a.n) || (a.b - b.b); });
  if (selBus === null && bs.length) selBus = bs[0].b;
  $('#rank').innerHTML = '<thead><tr><th>Bus</th><th class="num">RISKY</th><th>Share</th><th>Main reason</th><th class="num">Unresolved</th></tr></thead><tbody>' +
    bs.map(function (s) { var mm = Object.keys(s.modes).sort(function (a, b) { return s.modes[b] - s.modes[a]; })[0];
      return '<tr class="click' + (s.b === selBus ? ' on' : '') + '" data-b="' + s.b + '" tabindex="0"><td class="nw"><b>Bus ' + s.b + '</b></td><td class="num">' + s.R + '/' + s.n +
        '</td><td><div class="bar"><i style="width:' + (100 * s.R / s.n).toFixed(1) + '%"></i></div></td><td>' + (s.modes[mm] ? MODEN[mm] : (s.U ? 'Unresolved' : '–')) +
        '</td><td class="num">' + s.U + '</td></tr>'; }).join('') + '</tbody>';
  if (!bs.length) $('#rank').innerHTML = '<tbody><tr><td class="note">No fault bus with these filters.</td></tr></tbody>';
  $$('#rank tr[data-b]').forEach(function (t) { var f = function () { selBus = +t.getAttribute('data-b'); drawSel(); };
    t.onclick = f; t.onkeydown = function (e) { if (e.key === 'Enter') f(); }; });
}
function colChart(host, groups) {
  $(host).innerHTML = groups.map(function (g) { var s = g.n ? g.r / g.n : 0;
    return '<div class="c" title="' + esc(g.label) + ': ' + g.r + ' of ' + g.n + ' RISKY"><small>' + (g.n ? Math.round(100 * s) + '%' : '–') + '</small><i style="height:' + (s * 100).toFixed(1) + '%"></i></div>'; }).join('') ;
  var x = document.createElement('div'); x.className = 'colx';
  x.innerHTML = groups.map(function (g) { return '<span>' + esc(g.label) + '<em>' + fmtN(g.n) + '</em></span>'; }).join('');
  var old = $(host).nextElementSibling; if (old && old.className === 'colx') old.remove();
  $(host).after(x);
}
function group(fn, labels) { var out = labels.map(function (l) { return {label: l, n: 0, r: 0}; });
  FI.forEach(function (i) { var k = fn(R[i], i); if (k === null || k < 0 || !out[k]) return; out[k].n++; if (RISKY[R[i][CLS]]) out[k].r++; }); return out; }
function modeBars(host) {
  var c = {a: 0, v: 0, r: 0, f: 0}, u = 0; FI.forEach(function (i) { var r = R[i]; for (var j = 0; j < r[MODES].length; j++) c[r[MODES][j]]++; if (r[CLS] === 'U') u++; });
  var mx = Math.max(1, c.a, c.v, c.r, c.f, u);
  $(host).innerHTML = ['r', 'a', 'f', 'v'].map(function (m) { return '<div class="mrow"><span>' + MODEN[m] + '</span><div class="bar"><i style="width:' + (100 * c[m] / mx).toFixed(1) + '%"></i></div><b>' + fmtN(c[m]) + '</b></div>'; }).join('') +
    '<div class="mrow"><span>Unresolved (unknown)</span><div class="bar gray"><i style="width:' + (100 * u / mx).toFixed(1) + '%"></i></div><b>' + fmtN(u) + '</b></div>';
}
function topRisks() {
  var t = FI.filter(function (i) { return RISKY[R[i][CLS]]; }).sort(function (a, b) {
    var pa = R[a][P] === null ? 0 : R[a][P], pb = R[b][P] === null ? 0 : R[b][P];
    return (pb - pa) || ((R[b][CT] || 0) - (R[a][CT] || 0)); }).slice(0, 5);
  $('#toprisks').innerHTML = t.length ? t.map(function (i) { var r = R[i];
    return '<li>' + scLink(r[ID]) + ': fault at bus ' + r[BUS] + ', ' + cyc(i) + ', ' + (r[NIBR] ? r[NIBR] + ' IBR' + (r[NIBR] === 1 ? '' : 's') : 'no IBRs') +
      '. <span class="muted">' + esc(shortReason(r)) + '</span></li>'; }).join('') : '<li>No RISKY scenario with these filters.</li>';
  wireLinks($('#toprisks'));
}
function shortReason(r) {
  var t = r[TXT] >= 0 ? D.texts[r[TXT]] : ''; if (!t) return '';
  if (r[CLS] === 'U') return 'Bolted fault could not be solved; the milder fault did not settle it: needs a detailed study.';
  var ex = [];
  (D.rtByRow[R.indexOf(r)] || []).slice(0, 1).forEach(function (x) { ex.push('Ride-through: IBR bus ' + x[1] + ' ' + x[3] + ' for ' + Math.round(x[4] * 1000) + ' ms (allowed ' + (x[5] ? x[5] + ' s' : '0 s') + ')'); });
  if (r[MODES].indexOf('a') >= 0) ex.push('rotor angle beyond the limit');
  if (r[MODES].indexOf('v') >= 0) ex.push('slow voltage recovery');
  if (r[MODES].indexOf('f') >= 0) ex.push('frequency out of limits');
  var s = ex.join('; ');
  if (r[CLS] === 'C') s = 'Bolted fault unsolvable; a milder fault is already RISKY' + (s ? ': ' + s : '') ;
  s = s || t.split(' | ')[0].slice(0, 160);
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/* ---------------- scenarios table ---------------- */
var COLS = [['id', 'Scenario'], ['v', 'Verdict']];
if (hasPred) COLS.push(['src', 'Source']);
if (hasP) COLS.push(['p', 'P(RISKY)']);
COLS = COLS.concat([['bus', 'Bus'], ['ct', 'Clearing'], ['ibrs', 'IBRs'], ['share', 'IBR share'], ['scr', 'Lowest SCR']]);
if (hasRT) COLS.push(['rt', 'Ride-through use']);
COLS = COLS.concat([['load', 'Load'], ['why', 'Why']]);
var ORDER = {U: 0, C: 1, R: 2, Q: 3, N: 4, P: 5, O: 6};
function sval(i, k) { var r = R[i];
  switch (k) { case 'id': return r[ID]; case 'v': return ORDER[r[CLS]]; case 'src': return 'PQN'.indexOf(r[CLS]) >= 0 ? 1 : 0;
    case 'p': return r[P]; case 'bus': return r[BUS]; case 'ct': return r[CT]; case 'ibrs': return r[NIBR]; case 'share': return r[SHARE];
    case 'scr': return r[SCR]; case 'rt': return r[RT]; case 'load': return r[LOAD]; default: return ''; } }
function scenRows() {
  var q = ($('#q').value || '').trim().toLowerCase();
  var idx = FI.filter(function (i) { if (!q) return true; var r = R[i];
    var hay = (r[ID] + ' bus ' + r[BUS] + ' ' + r[IBRS] + ' ' + NAME[r[CLS]] + ' ' + (r[TXT] >= 0 ? D.texts[r[TXT]] : '')).toLowerCase();
    return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; }); });
  if (sortS.k) idx.sort(function (a, b) { var x = sval(a, sortS.k), y = sval(b, sortS.k);
    if (x === null || x === '') return 1; if (y === null || y === '') return -1;
    var c = (typeof x === 'number' && typeof y === 'number') ? x - y : String(x).localeCompare(String(y), undefined, {numeric: true});
    return sortS.asc ? c : -c; });
  else idx.sort(function (a, b) { return (ORDER[R[a][CLS]] - ORDER[R[b][CLS]]) || ((R[b][P] || 0) - (R[a][P] || 0)) || ((R[b][CT] || 0) - (R[a][CT] || 0)); });
  return idx;
}
function cell(i, k) { var r = R[i];
  switch (k) {
    case 'id': return esc(r[ID]).replace(/_/g, '_<wbr>');
    case 'v': return '<span class="tag ' + r[CLS] + '">' + NAME[r[CLS]] + '</span>';
    case 'src': return 'PQN'.indexOf(r[CLS]) >= 0 ? 'predicted' : 'simulated';
    case 'p': return r[P] === null ? '' : r[P].toFixed(3);
    case 'bus': return r[BUS] === null ? '' : r[BUS];
    case 'ct': return r[CT] === null ? '' : Math.round(r[CT] * 1000) + ' ms <span class="note">(' + (Math.round(cycles[i] * 10) / 10) + ' c)</span>';
    case 'ibrs': return r[IBRS] ? r[IBRS].split(';').map(function (x) { return '<span class="gchip">' + esc(x) + '</span>'; }).join('') : '<span class="note">none</span>';
    case 'share': return r[SHARE] === null ? '' : Math.round(r[SHARE] * 100) + '%';
    case 'scr': return r[SCR] === null || r[SCR] >= 99 ? '' : r[SCR].toFixed(2);
    case 'rt': return r[RT] === null ? '' : (r[RT] >= 1e8 ? '<b class="bad">exposed</b>' : '<span class="' + (r[RT] > 1 ? 'bad' : r[RT] >= 0.8 ? 'warn' : '') + '">' + Math.round(r[RT] * 100) + '%</span>');
    case 'load': return r[LOAD] === null ? '' : r[LOAD].toFixed(2) + '×';
    case 'why': return esc(shortReason(r));
  } return ''; }
function scenTable() {
  var idx = scenRows(), n = idx.length;
  $('#scount').textContent = fmtN(n) + ' scenario' + (n === 1 ? '' : 's') + (n > shown ? ' · showing ' + fmtN(Math.min(shown, n)) : '');
  var head = '<thead><tr>' + COLS.map(function (c) { var s = sortS.k === c[0] ? (sortS.asc ? 'ascending' : 'descending') : 'none';
    return '<th class="' + (/^(p|bus|ct|share|scr|rt|load)$/.test(c[0]) ? 'num' : '') + '" aria-sort="' + s + '">' + (c[0] === 'why' ? c[1] : '<button type="button" data-k="' + c[0] + '">' + c[1] + '</button>') + '</th>'; }).join('') + '</tr></thead>';
  var body = idx.slice(0, shown).map(function (i) { var r = R[i];
    var tr = '<tr class="click" data-i="' + i + '" tabindex="0">' + COLS.map(function (c) {
      return '<td data-l="' + c[1] + '" class="c-' + c[0] + (/^(p|bus|ct|share|scr|rt|load)$/.test(c[0]) ? ' num' : '') + (c[0] === 'why' ? ' why' : '') + '">' + cell(i, c[0]) + '</td>'; }).join('') + '</tr>';
    if (openRow === r[ID]) tr += detailRow(i);
    return tr; }).join('');
  $('#stab').innerHTML = head + '<tbody>' + body + '</tbody>';
  $('#smore').hidden = n <= shown;
  $$('#stab th button').forEach(function (b) { b.onclick = function () { var k = b.getAttribute('data-k');
    if (sortS.k === k) { if (!sortS.asc) { sortS.k = null; } else sortS.asc = false; } else { sortS.k = k; sortS.asc = !/^(v|p|ct|share|rt)$/.test(k); }
    scenTable(); }; });
  $$('#stab tr.click').forEach(function (t) { var f = function () { var id = R[+t.getAttribute('data-i')][ID]; openRow = openRow === id ? null : id; scenTable(); };
    t.onclick = function (e) { if (e.target.closest('button')) return; f(); }; t.onkeydown = function (e) { if (e.key === 'Enter') f(); }; });
  wireLinks($('#stab'));
}
function detailRow(i) { var r = R[i], t = r[TXT] >= 0 ? D.texts[r[TXT]] : '';
  var parts = t ? t.split(' | ') : [];
  return '<tr class="detail"><td colspan="' + COLS.length + '"><b>' + esc(r[ID]) + '</b> · status ' + esc(r[ST] || '–') +
    (TRSET[r[ID]] ? ' · <button type="button" class="linkb" data-traj="' + esc(r[ID]) + '">open trajectories</button>' : '') +
    (parts.length ? '<ul>' + parts.map(function (p) { return '<li>' + esc(p) + '</li>'; }).join('') + '</ul>' : '<p class="note">All checks passed.</p>') + '</td></tr>'; }

/* ---------------- failure modes ---------------- */
function modesView() {
  var cb = [[0, 4, '< 4'], [4, 6.5, '4–6'], [6.5, 9.5, '7–9'], [9.5, 12.5, '10–12'], [12.5, 15.5, '13–15'], [15.5, 99, '16 +']];
  colChart('#c-ct', group(function (r, i) { var c = cycles[i]; if (c === null) return null;
    for (var k = 0; k < cb.length; k++) if (c >= cb[k][0] && c < cb[k][1]) return k; return null; }, cb.map(function (x) { return x[2] + ' c'; })));
  var sb = [0, 0.1, 0.2, 0.3, 0.4, 0.6, 1.01], sl = ['0–10%', '10–20%', '20–30%', '30–40%', '40–60%', '60%+'];
  var shg = group(function (r) { if (r[SHARE] === null) return null; for (var k = 0; k < sb.length - 1; k++) if (r[SHARE] >= sb[k] && r[SHARE] < sb[k + 1]) return k; return null; }, sl);
  while (shg.length > 1 && !shg[shg.length - 1].n) shg.pop();
  colChart('#c-share', shg);
  var rows = []; for (var k = 0; k <= maxIbr; k++) rows.push(k);
  $('#modetab').innerHTML = '<thead><tr><th>IBRs</th><th class="num">Scenarios</th><th class="num">RISKY</th>' + ['r', 'a', 'v', 'f'].map(function (m) { return '<th class="num">' + MODEN[m] + '</th>'; }).join('') + '<th class="num">Unresolved</th></tr></thead><tbody>' +
    rows.map(function (k) { var g = FI.filter(function (i) { return R[i][NIBR] === k; }), n = g.length;
      function c(fn) { return g.filter(function (i) { return fn(R[i]); }).length; }
      return '<tr><td>' + k + '</td><td class="num">' + fmtN(n) + '</td><td class="num">' + pct(c(function (r) { return RISKY[r[CLS]]; }), n) + '</td>' +
        ['r', 'a', 'v', 'f'].map(function (m) { return '<td class="num">' + pct(c(function (r) { return r[MODES].indexOf(m) >= 0; }), n) + '</td>'; }).join('') +
        '<td class="num">' + pct(c(function (r) { return r[CLS] === 'U'; }), n) + '</td></tr>'; }).join('') + '</tbody>';
}

/* ---------------- ride-through ---------------- */
D.rtByRow = {}; D.rtx.forEach(function (x) { (D.rtByRow[x[0]] = D.rtByRow[x[0]] || []).push(x); });
function rtView() {
  var inF = {}; FI.forEach(function (i) { inF[i] = 1; });
  var ex = D.rtx.filter(function (x) { return inF[x[0]]; });
  var scen = {}; ex.forEach(function (x) { scen[x[0]] = 1; });
  var buses = {}, bands = {};
  ex.forEach(function (x) { var b = buses[x[1]] = buses[x[1]] || {n: {}, bands: {}, worst: 0, lim: 0, worstBand: '', r: -1};
    b.n[x[0]] = 1; (b.bands[x[3]] = b.bands[x[3]] || {})[x[0]] = 1; (bands[x[3]] = bands[x[3]] || {})[x[0]] = 1;
    var ratio = x[5] > 0 ? x[4] / x[5] : Infinity;   // share of the allowed time (0 s allowed: infinite)
    if (ratio > b.r || (ratio === b.r && x[4] > b.worst)) { b.r = ratio; b.worst = x[4]; b.lim = x[5]; b.worstBand = x[3]; } });
  var nm = hasRT ? FI.filter(function (i) { var r = R[i][RT]; return r !== null && r >= 0.8 && r <= 1; }) : [];
  $('#rtnote').innerHTML = 'Time an IBR spent outside the region where NERC PRC-029 requires it to ride through, measured at its high-side bus. ' +
    'Exposure means the standard may permit it to trip: a loss-of-IBR risk, not a prediction that it trips. ' +
    (D.counting ? 'Counting: <b>' + esc(D.counting.replace('_', ' ')) + '</b>. ' : '') +
    'Confirmed scenarios count with their milder fault.';
  $('#rtk').innerHTML = '<div><span>Scenarios exposed</span><b class="bad">' + fmtN(Object.keys(scen).length) + '</b></div>' +
    '<div><span>IBR buses involved</span><b>' + Object.keys(buses).length + '</b></div>' +
    (hasRT ? '<div><span>Near misses</span><b class="warn">' + fmtN(nm.length) + '</b></div>' : '') +
    '<div><span>Exposures</span><b>' + fmtN(ex.length) + '</b></div>';
  Object.keys(bands).forEach(function (k) { bands[k] = Object.keys(bands[k]).length; });
  var mx = Math.max(1, Math.max.apply(null, Object.keys(bands).map(function (k) { return bands[k]; }).concat([0])));
  var bk = Object.keys(bands).sort(function (a, b) { return bands[b] - bands[a]; });
  $('#c-band').innerHTML = bk.length ? bk.map(function (k) { return '<div class="mrow"><span>' + esc(k) + '</span><div class="bar"><i style="width:' + (100 * bands[k] / mx).toFixed(1) + '%;background:var(--bad)"></i></div><b>' + fmtN(bands[k]) + '</b></div>'; }).join('')
    + '<p class="note">Scenarios with exposure in each band (one scenario can count in more than one band).</p>' : '<p class="note">No ride-through exposure with these filters.</p>';
  var bl = Object.keys(buses).sort(function (a, b) { return Object.keys(buses[b].n).length - Object.keys(buses[a].n).length; });
  $('#rttab').innerHTML = bl.length ? '<thead><tr><th>IBR bus</th><th class="num">Scenarios</th><th>Bands</th><th>Longest against allowed</th></tr></thead><tbody>' +
    bl.map(function (b) { var s = buses[b]; return '<tr class="click" data-b="' + b + '"><td><b>Bus ' + b + '</b></td><td class="num">' + Object.keys(s.n).length + '</td><td>' +
      Object.keys(s.bands).map(function (k) { return esc(k) + ' (' + Object.keys(s.bands[k]).length + ')'; }).join(', ') + '</td><td>' + Math.round(s.worst * 1000) + ' ms in ' + esc(s.worstBand) + ' (allowed ' + (s.lim ? s.lim + ' s' : '0 s') + ')</td></tr>'; }).join('') + '</tbody>'
    : '<tbody><tr><td class="note">No exposure with these filters.</td></tr></tbody>';
  $$('#rttab tr[data-b]').forEach(function (t) { t.onclick = function () { selBus = +t.getAttribute('data-b'); go('map'); drawSel(); }; });
  $('#nearcard').hidden = !hasRT;
  if (hasRT) $('#neartab').innerHTML = nm.length ? '<thead><tr><th>Scenario</th><th class="num">Use of allowed time</th><th class="num">Bus</th><th>Clearing</th><th>IBRs</th></tr></thead><tbody>' +
    nm.sort(function (a, b) { return R[b][RT] - R[a][RT]; }).slice(0, 200).map(function (i) { var r = R[i];
      return '<tr><td>' + scLink(r[ID]) + '</td><td class="num warn">' + Math.round(r[RT] * 100) + '%</td><td class="num">' + r[BUS] + '</td><td>' + cell(i, 'ct') + '</td><td>' + cell(i, 'ibrs') + '</td></tr>'; }).join('') + '</tbody>'
    : '<tbody><tr><td class="note">No near miss with these filters.</td></tr></tbody>';
  wireLinks($('#neartab'));
}

/* ---------------- screening model ---------------- */
function modelView() {
  var h = '';
  if (hasP) {
    var sim = FI.filter(function (i) { return 'PQN'.indexOf(R[i][CLS]) < 0; }).length, n = FI.length;
    var unv = FI.filter(function (i) { return 'QN'.indexOf(R[i][CLS]) >= 0; }).length;
    h += '<div class="card"><h3>This screening run</h3><div class="minikpis"><div><span>Scenarios</span><b>' + fmtN(n) + '</b></div><div><span>Simulated</span><b>' + fmtN(sim) + ' (' + pct(sim, n) + ')</b></div>' +
      '<div><span>Predicted OK</span><b>' + fmtN(FI.filter(function (i) { return R[i][CLS] === 'P'; }).length) + '</b></div>' +
      '<div><span>Not yet verified</span><b class="' + (unv ? 'warn' : '') + '">' + unv + '</b></div></div>' +
      '<p class="note">Every RISKY verdict comes from a simulation. Predicted-RISKY, borderline and solver-hard scenarios are simulated; "not yet verified" appears only when a simulation budget ran out.</p></div>';
  }
  if (D.model && D.model.models) {
    var ms = D.model.models, names = Object.keys(ms);
    h += '<div class="card"><h3>Tested on fault buses it never saw</h3><p class="note">The machine-learning (ML) screening model ranks scenarios so the riskiest are simulated first; it never decides a verdict. ' +
      'Each model below is trained without the tested fault buses (grouped by bus) and set to catch ' +
      Math.round(100 * (D.model.target || 0.95)) + '% of RISKY scenarios.</p><div class="tscroll"><table class="tbl"><thead><tr><th>Model</th><th class="num">Recall</th><th class="num">Precision</th><th class="num">AUC</th><th class="num">AP</th><th class="num">Share simulated</th></tr></thead><tbody>' +
      names.map(function (k) { var m = ms[k]; return '<tr><td><b>' + esc(MNAME[k] || k) + '</b></td><td class="num">' + f2(m.recall) + '</td><td class="num">' + f2(m.precision) + '</td><td class="num">' + f3(m.auc) + '</td><td class="num">' + f3(m.ap) + '</td><td class="num">' + (m.flagged_share === null || m.flagged_share === undefined ? '' : Math.round(m.flagged_share * 100) + '%') + '</td></tr>'; }).join('') +
      '</tbody></table></div>' +
      '<ul class="defs"><li><b>Rule</b>: a fixed two-threshold rule (clearing time, lowest IBR SCR); the baseline an ML model must beat. <b>Logistic regression</b> and <b>GBM</b> (gradient-boosting machine: many small decision trees, each correcting the last) are the ML models.</li>' +
      '<li><b>Recall</b>: share of RISKY scenarios the model sends to simulation. <b>Precision</b>: share of those sent that are RISKY. ' +
      '<b>AUC</b> (area under the ROC curve, receiver operating characteristic): chance that a random RISKY scenario is ranked above a random OK one (0.5 = guessing, 1 = perfect). ' +
      '<b>AP</b> (average precision): precision averaged over all recall levels. <b>SCR</b>: short-circuit ratio.</li></ul>';
    var eff = names.filter(function (k) { return ms[k].effort && ms[k].effort.length; });
    if (eff.length) {
      h += '<h3 style="margin-top:14px">Study effort</h3><p class="note">Share of scenarios a model sends to simulation to reach a recall.</p><div class="tscroll"><table class="tbl"><thead><tr><th>Recall</th>' +
        eff.map(function (k) { return '<th class="num">' + esc(MSHORT[k] || k) + '</th>'; }).join('') + '</tr></thead><tbody>' +
        ms[eff[0]].effort.map(function (e, j) { return '<tr><td>' + f2(e.recall) + '</td>' + eff.map(function (k) { var x = ms[k].effort[j]; return '<td class="num">' + (x ? Math.round(x.share * 100) + '%' : '') + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody></table></div>';
    }
    h += '</div>';
  }
  var hard = Object.keys(ALLU).filter(function (b) { return ALLU[b] >= D.hard; }).sort(function (a, b) { return a - b; });
  if (hard.length && !hasP) h += '<div class="card"><h3>Solver-hard fault buses</h3><p>' + hard.map(function (b) { return 'bus ' + b + ' (' + Math.round(ALLU[b] * 100) + '% unknown)'; }).join(', ') +
    '</p><p class="note">At these fault locations ANDES often cannot solve the bolted fault. The screen always simulates them, because solver failure cannot be predicted from the pre-fault features.</p></div>';
  if (D.card) h += '<div class="card"><h3>Model card (ML screening model)</h3><pre class="md">' + esc(D.card) + '</pre></div>';
  $('#modelbody').innerHTML = h || '<div class="card"><p class="note">No screening model in these results.</p></div>';
}
var MNAME = {rule: 'Rule (baseline, not ML)', logistic: 'Logistic regression (ML)', gbm: 'GBM, gradient boosting (ML)'};
var MSHORT = {rule: 'Rule', logistic: 'Logistic', gbm: 'GBM'};
function f2(x) { return x === null || x === undefined ? '' : (+x).toFixed(2); }
function f3(x) { return x === null || x === undefined ? '' : (+x).toFixed(3); }

/* ---------------- CCT + method ---------------- */
function cctView() { $('#ccttab').innerHTML = '<thead><tr><th>Fault bus</th><th>Result</th><th>Status</th></tr></thead><tbody>' +
  D.cct.map(function (c) { return '<tr><td>' + esc(c.bus) + '</td><td>' + esc(c.text) + '</td><td>' + esc(c.status) + '</td></tr>'; }).join('') + '</tbody>'; }
function methodView() {
  $('#method').innerHTML = '<h3>What the numbers mean</h3><ul>' +
    '<li><b>RISKY</b>: at least one check failed: rotor angle (largest angle difference between machines above the limit), voltage recovery, IBR ride-through exposure under NERC PRC-029, or frequency.</li>' +
    '<li><b>RISKY (confirmed)</b>: ANDES could not solve the bolted fault, and a slightly milder fault is already RISKY. <b>RISKY (unresolved)</b>: the milder fault did not settle it; the outcome is unknown and needs a detailed study (commercial RMS or EMT). The tool never turns a case it could not solve into OK.</li>' +
    '<li><b>Ride-through exposure</b>: time an IBR spent outside the must-ride-through region at its high-side bus. PRC-029-1 and PRC-029-2 have the same Tables 2 and 3. ' + (D.counting ? 'Counting: ' + esc(D.counting.replace('_', ' ')) + '.' : '') + '</li>' +
    (hasRT ? '<li><b>Ride-through use</b>: the largest share of an allowed time used in any band. 80% to 100% is a near miss.</li>' : '') +
    '<li><b>Filters</b> only choose which scenarios are counted; no result is recalculated.</li></ul>' +
    (D.ov_note ? '<h3>Datasets</h3><p>' + D.ov_note + '</p>' : '') +
    '<h3>This run</h3><ul><li>Case: ' + esc(D.case) + ' · ' + fmtN(N) + ' scenarios</li><li>Criteria: ' + esc(D.criteria) + '</li>' +
    (D.andes ? '<li>ANDES ' + esc(D.andes) + '</li>' : '') + '<li>IBR-FaultScreen ' + esc(D.version) + ' · ' + esc(D.date) + '</li>' +
    '<li>' + esc(D.layout_note) + '</li>' + (D.note ? '<li>' + D.note + '</li>' : '') + '</ul>' +
    '<h3>Limits</h3><ul>' + D.limits.map(function (l) { return '<li>' + esc(l) + '</li>'; }).join('') + '</ul>';
}

/* ---------------- CSV + print ---------------- */
function doCsv() {
  var idx = view === 'scen' ? scenRows() : FI;
  var head = ['scenario_id', 'verdict', 'fault_bus', 'clear_time_s', 'ibrs', 'n_ibr', 'ibr_share', 'scr_min', 'load_scale', 'p_risky', 'rt_ratio_max', 'failure_modes', 'reasons'];
  var lines = [head.join(',')].concat(idx.map(function (i) { var r = R[i];
    return [r[ID], NAME[r[CLS]], r[BUS], r[CT], r[IBRS], r[NIBR], r[SHARE], r[SCR], r[LOAD], r[P], r[RT] !== null && r[RT] >= 1e8 ? 'inf' : r[RT],
            r[MODES].split('').map(function (m) { return MODEN[m]; }).join('; '), r[TXT] >= 0 ? D.texts[r[TXT]] : '']
      .map(function (v) { v = v === null || v === undefined ? '' : String(v); return /[",\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }).join(','); }));
  var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([lines.join('\n')], {type: 'text/csv'}));
  a.download = 'ibr_faultscreen_scenarios.csv'; document.body.appendChild(a); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
function doPrint() { ['overview', 'map', 'scen', 'modes', 'rt', 'model', 'cct', 'method'].forEach(function (k) { if (available(k)) drawView(k); }); window.print(); }

/* ---------------- draw ---------------- */
var drawn = {};
function drawView(k) {
  if (k === 'overview') { rankTable(); if (!maps.o) makeMap('o', $('#map-o')); paintMap(maps.o); drawSel();
    colChart('#c-nibr', group(function (r) { return r[NIBR]; }, Array.apply(null, {length: maxIbr + 1}).map(function (_, k) { return k + ' IBR' + (k === 1 ? '' : 's'); })));
    modeBars('#c-modes'); topRisks(); }
  else if (k === 'map') { if (selBus === null) { var top = busStats().filter(function (x) { return x.n; }).sort(function (a, b) { return b.R / b.n - a.R / a.n; })[0]; if (top) selBus = top.b; }
    if (!maps.m) makeMap('m', $('#map-m')); paintMap(maps.m); drawSel(); $('#layoutnote').textContent = D.layout_note; }
  else if (k === 'scen') scenTable();
  else if (k === 'modes') modesView();
  else if (k === 'rt') rtView();
  else if (k === 'model') modelView();
  else if (k === 'cct') cctView();
  else if (k === 'method') methodView();
}
function draw() { kpis(); drawView(view); }
function update() { refilter(); _bs = null; shown = 100; draw(); nav(); }

/* ---------------- start ---------------- */
$('#title').textContent = D.title || 'IBR-FaultScreen dashboard';
$('#subtitle').innerHTML = D.subtitle || (esc(D.case) + ' · ' + fmtN(N) + ' scenarios · ' + esc(D.criteria));
$('#sfoot').innerHTML = 'IBR-FaultScreen ' + esc(D.version) + '<br>Screening only: results do not replace interconnection studies.';
$('#foot').innerHTML = 'Generated by IBR-FaultScreen ' + esc(D.version) + ' (GPL-3.0-or-later) on ' + esc(D.date) + ' · https://github.com/ishaibu/ibr-faultscreen';
$('#print').onclick = doPrint; $('#csv').onclick = doCsv;
if (D.ov_note) { $('#ovnote').innerHTML = D.ov_note; $('#ovnote').hidden = false; }
var er = $('#eacreplay'); if (er) er.onclick = function () { var r = $('#replay'); if (r) r.click(); };
window.addEventListener('load', function () { var m = $('#exmiss'); if (m && D.explorer && !window.EXPLORER_DATA) m.hidden = false; });
$('#q').oninput = function () { shown = 100; scenTable(); };
$('#smore').onclick = function () { shown += 200; scenTable(); };
initFilters(); initMapSelect('o'); initMapSelect('m'); refilter();
function setSideH() { var s = $('.side'); document.documentElement.style.setProperty('--sideh', (s.getBoundingClientRect().height || 0) + 'px'); }
var mapT = null;
window.addEventListener('resize', function () { setSideH(); clearTimeout(mapT); mapT = setTimeout(function () {
  Object.keys(maps).forEach(function (k) { var M = maps[k], w = M.host.clientWidth; if (!w || Math.abs(w - (M.w || 0)) < 30) return;
    M.w = w; buildMap(M); paintMap(M); }); }, 150); });
function route() { var h = location.hash || '';
  if (/^#(explore|quiz)/.test(h) && D.explorer) { go('explore', true);
    if (/^#quiz/.test(h)) setTimeout(function () { var q = document.getElementById('quiz'); if (q) q.scrollIntoView(); }, 80);
    return true; }
  var m = /^#view=(\w+)/.exec(h); if (m) { go(m[1], true); return true; } return false; }
if (!route()) go('overview', true);
window.addEventListener('hashchange', function () {
  if (/^#(explore|quiz)/.test(location.hash) && view !== 'explore' && D.explorer) go('explore', true);
  else { var m = /^#view=(\w+)/.exec(location.hash); if (m && m[1] !== view) go(m[1], true); } });
setSideH();
if (window.ResizeObserver) new ResizeObserver(setSideH).observe($('.side'));
window.addEventListener('load', setSideH);
})();
"""
