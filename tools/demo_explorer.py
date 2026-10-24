"""The "Build your own grid" explorer and the quiz for the demo page.

Used by tools/build_demo.py. The data come from tools/explorer_data.py
(docs/demo/explorer-data.js, loaded by the page). Nothing here runs a
simulation: every combination the visitor can pick was simulated in advance.

Shareable links: #explore=g26-b16-c6 opens the explorer with IBRs on
generators 2 and 6, a fault at bus 16, cleared in 6 cycles.
"""

from __future__ import annotations

import json

CSS = """
[hidden] { display:none !important; }
.xp-grid { display:grid; grid-template-columns:minmax(0, 1.55fr) minmax(0, 1fr); gap:18px; align-items:start; }
@media (max-width:960px) { .xp-grid { grid-template-columns:1fr; } }
.xp-ctl { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; margin:10px 0 6px; }
.xp-ctl .lbl { font-size:12px; font-weight:700; color:var(--ink2); letter-spacing:.04em; text-transform:uppercase; }
.seg { display:inline-flex; border:1px solid #bfe0e1; border-radius:8px; overflow:hidden; flex-wrap:wrap; }
.seg button { font:inherit; font-size:13px; padding:6px 10px; border:0; background:#fff; color:var(--teal-d); cursor:pointer;
  border-right:1px solid #bfe0e1; min-width:44px; }
.seg button:last-child { border-right:0; }
.seg button[aria-pressed="true"] { background:var(--teal); color:#fff; font-weight:700; }
.gens { display:flex; flex-wrap:wrap; gap:6px; }
.gens button { font:inherit; font-size:13px; padding:6px 9px; border-radius:8px; border:1px solid var(--line);
  background:#fff; color:var(--ink); cursor:pointer; min-width:44px; }
.gens button[aria-pressed="true"] { background:var(--teal); color:#fff; border-color:var(--teal); font-weight:700; }
.xmap { border:1px solid var(--line); border-radius:12px; padding:6px; background:#fff; }
.xmap svg { display:block; width:100%; height:auto; }
.xmap .e { stroke:#c9c8c2; stroke-width:1.6; }
.xmap .e.xf { stroke-dasharray:5 4; }
.xmap .b { cursor:pointer; }
.xmap .b:focus { outline:none; }
.xmap .b:focus .ring, .xmap .b:hover .ring { opacity:1; }
.xmap .ring { fill:none; stroke:var(--teal-d); stroke-width:2; opacity:0; }
.xmap .sel { fill:none; stroke:#1d1d1b; stroke-width:3; }
.xmap .bolt { fill:#1d1d1b; pointer-events:none; }
.xmap text { font:700 10.5px "DM Sans", system-ui, sans-serif; text-anchor:middle; pointer-events:none; fill:#1d1d1b; }
.xmap text.w { fill:#fff; }
.xmap .g { cursor:pointer; }
.xmap .g.slack { cursor:not-allowed; }
.xmap .flash { animation:xpflash .5s ease; }
@keyframes xpflash { from { opacity:.35; } to { opacity:1; } }
.xp-legend { display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12px; color:var(--ink2); margin-top:6px; }
.xp-legend i { display:inline-block; width:12px; height:12px; border-radius:50%; margin-right:5px; vertical-align:-2px; }
.xp-legend i.sq { border-radius:3px; }
.xp-card { border:1px solid var(--line); border-radius:12px; padding:14px 16px; background:#fff; }
.xp-card h3 { margin:0 0 6px; font-size:16px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.xp-card p { margin:6px 0; font-size:13.5px; line-height:1.5; }
.xp-sub { color:var(--ink2); font-size:13px; }
.pill.unk { background:#ecebe6; color:#55544f; }
.strip { display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:6px; margin:8px 0 2px; }
.strip button { font:inherit; font-size:12px; border-radius:8px; border:1px solid var(--line); padding:6px 2px; cursor:pointer;
  background:#fff; color:var(--ink); line-height:1.3; }
.strip button b { display:block; font-size:12.5px; }
.strip button[aria-pressed="true"] { outline:2px solid #1d1d1b; outline-offset:1px; }
.sv { background:var(--soft); border-radius:10px; padding:10px 12px; margin-top:10px; font-size:13px; }
.sv .bar { position:relative; height:10px; background:#e3e2dd; border-radius:5px; margin:8px 0 4px; }
.sv .bar span { position:absolute; left:0; top:0; bottom:0; background:var(--teal); border-radius:5px; }
.sv .bar i { position:absolute; top:-4px; bottom:-4px; width:2px; background:#1d1d1b; }
.xp-tools { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }
.xp-msg { font-size:12.5px; color:var(--bad); min-height:1.2em; margin:4px 0 0; }
.xp-sum { font-size:13.5px; margin:4px 0 0; }
.quiz .qq { font-size:15px; margin:6px 0 10px; line-height:1.5; }
.quiz .ans { display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; }
.quiz .ans .btn { min-width:110px; justify-content:center; font-size:15px; padding:10px 18px; }
.quiz .ans .btn.ok { background:#1b7a46; } .quiz .ans .btn.ok:hover { background:#14603a; }
.quiz .ans .btn.risky { background:#b8401c; } .quiz .ans .btn.risky:hover { background:#963214; }
.quiz .ans .btn[disabled] { opacity:.45; cursor:default; }
.quiz .score { display:flex; gap:18px; flex-wrap:wrap; font-size:13.5px; margin:6px 0; }
.quiz .score b { font-size:18px; }
.quiz .reveal { border-left:4px solid var(--teal); padding:8px 12px; background:var(--soft); border-radius:6px; margin-top:10px; }
.quiz .reveal.right { border-left-color:#1b7a46; } .quiz .reveal.wrong { border-left-color:#b8401c; }
.qprog { height:6px; background:#ecebe6; border-radius:3px; margin:8px 0 4px; }
.qprog span { display:block; height:100%; background:var(--teal); border-radius:3px; width:0; transition:width .3s ease; }
"""

HTML = """
<section id="explore"><h2>Build your own grid</h2>
<p class="muted">Pick which synchronous generators become IBRs (solar), where the three-phase fault happens and how fast it is
cleared. Every combination here was simulated in advance with IBR-FaultScreen: 256 IBR sets × 29 fault buses × 5 clearing
times = 37,120 runs on the IEEE 39-bus system at base load. The map colors every fault bus by its result for your
choice; tap a bus to see why. The "screen view" on each case uses a screening model trained without that fault bus
(the list of solver-hard buses that are always simulated comes from the whole training dataset).</p>
<div class="xp-ctl"><span class="lbl">IBRs replace</span><div class="gens" id="xpGens" role="group" aria-label="Generators replaced by IBRs"></div></div>
<div class="xp-ctl"><span class="lbl">Clearing time</span><div class="seg" id="xpCyc" role="group" aria-label="Fault clearing time"></div></div>
<p class="xp-msg" id="xpMsg" aria-live="polite"></p>
<div class="xp-grid">
  <div><div class="xmap" id="xpMap"></div>
    <div class="xp-legend">
      <span><i style="background:#d8efe2;border:1.5px solid #1b7a46"></i>OK</span>
      <span><i style="background:#e2683f"></i>RISKY</span>
      <span><i style="background:#e2683f;border:2px solid #7a2a10"></i>RISKY (bolted run unsolvable, milder fault RISKY)</span>
      <span><i style="background:#e3e2dd;border:1.5px dashed #8a8984"></i>unknown (solver could not decide)</span>
      <span><i class="sq" style="background:var(--teal)"></i>IBR</span>
      <span><i class="sq" style="background:#fff;border:1.5px solid #9a9993"></i>synchronous generator</span>
    </div>
    <p class="xp-sum" id="xpSum" aria-live="polite"></p>
  </div>
  <div class="xp-card" id="xpCard" aria-live="polite"></div>
</div>
<div class="xp-tools">
  <button class="btn ghost" type="button" id="xpRand">Random grid</button>
  <button class="btn ghost" type="button" id="xpReset">Reset</button>
  <button class="btn" type="button" id="xpCopy">Copy link to this case</button>
</div>
<p class="muted small">Generic WECC IBR models and illustrative limits on a test system; screening only. "Unknown" means
ANDES could not solve the bolted fault and the milder-fault check could not decide: such cases need a detailed study.</p>
</section>

<section id="quiz" class="quiz"><h2>Quiz: guess first</h2>
<p class="muted">Each question is a real simulated case from the explorer. Decide whether it is RISKY or OK, then see what
the simulation found and what the screening model (which never saw that fault bus) would have guessed.
The model is tuned to miss as few RISKY cases as possible, so it raises false alarms on purpose.</p>
<div class="xp-grid">
  <div><div class="xmap" id="qMap"></div></div>
  <div class="xp-card">
    <div class="score"><span>Question <b id="qN">1</b> of 10</span><span>You <b id="qYou">0</b></span><span>Screening model <b id="qModel">0</b></span></div>
    <div class="qprog"><span id="qProg"></span></div>
    <p class="qq" id="qText"></p>
    <div class="ans"><button class="btn ok" type="button" id="qOK">OK</button><button class="btn risky" type="button" id="qRisky">RISKY</button></div>
    <div id="qReveal" aria-live="polite"></div>
    <div class="xp-tools"><button class="btn" type="button" id="qNext" hidden>Next question</button>
      <a class="btn ghost" id="qExplore" href="#explore" hidden>Open this case in the explorer</a></div>
  </div>
</div>
</section>
"""

JS = r"""
(function () {
  var D = window.EXPLORER_DATA, L = window.EXPLORER_LAYOUT;
  if (!D || !L) { var s = document.getElementById('explore'); if (s) s.hidden = true; var q = document.getElementById('quiz'); if (q) q.hidden = true; return; }
  var NS = 'http://www.w3.org/2000/svg';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var CYC = D.cycles, BUS = D.buses, NB = BUS.length, NC = CYC.length;
  var comboIdx = {}; D.combos.forEach(function (c, i) { comboIdx[c] = i; });
  var COL = {O: ['#d8efe2', '#1b7a46', ''], R: ['#e2683f', '#b8401c', ''], C: ['#e2683f', '#7a2a10', ''],
             U: ['#e3e2dd', '#8a8984', '4 3'], T: ['#e3e2dd', '#8a8984', '4 3']};
  var NAME = {O: 'OK', R: 'RISKY', C: 'RISKY (confirmed)', U: 'Unknown', T: 'Unknown'};
  var TONE = {O: 'good', R: 'bad', C: 'bad', U: 'unk', T: 'unk'};
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function idx(gens, bus, ci) { return comboIdx[gens.slice().sort().join('')] * NB * NC + (bus - 1) * NC + ci; }
  function rec(gens, bus, ci) {
    var i = idx(gens, bus, ci), sh = D.s.substr(4 * i, 4);
    return {v: D.v[i], m: parseInt(D.m[i], 16), s: sh === '----' ? null : parseInt(sh, 16) / 65535, d: +D.d[i],
            f: D.f ? D.f[i] === '1' : null, text: D.texts[D.r[i]], thr: D.thr[String(bus)]};
  }
  function ms(c) { return Math.round(c / 60 * 1000); }
  function genBus(g) { return 29 + g; }

  // ---------------------------------------------------------------- map
  function drawMap(host, opts) {
    var w = host.clientWidth || 600, tall = w < 560;
    var W = tall ? 600 : 960, H = tall ? 860 : 600, pad = tall ? 36 : 30, k = tall ? 1.35 : 1;
    function xy(b) { var p = L.pos[b], x = p[0], y = p[1]; if (tall) { var t = x; x = 1 - y; y = t; }
      return [pad + x * (W - 2 * pad), pad + y * (H - 2 * pad)]; }
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', opts.label);
    L.edges.forEach(function (e) {
      var a = xy(e[0]), b = xy(e[1]), ln = document.createElementNS(NS, 'line');
      ln.setAttribute('x1', a[0]); ln.setAttribute('y1', a[1]); ln.setAttribute('x2', b[0]); ln.setAttribute('y2', b[1]);
      ln.setAttribute('class', e[2] ? 'e xf' : 'e'); svg.appendChild(ln);
    });
    var nodes = {};
    Object.keys(L.pos).map(Number).sort(function (a, b) { return a - b; }).forEach(function (b) {
      var p = xy(b), g = document.createElementNS(NS, 'g'), isGen = b >= 30, gen = b - 29;
      var shape, label = document.createElementNS(NS, 'text');
      if (isGen) {
        var h = 12 * k; shape = document.createElementNS(NS, 'rect');
        shape.setAttribute('x', p[0] - h); shape.setAttribute('y', p[1] - h); shape.setAttribute('width', 2 * h);
        shape.setAttribute('height', 2 * h); shape.setAttribute('rx', 4);
        g.setAttribute('class', 'g' + (b === 39 ? ' slack' : ''));
        g.setAttribute('aria-label', b === 39 ? 'Generator 10 (slack, always synchronous)' : 'Generator ' + gen);
      } else {
        shape = document.createElementNS(NS, 'circle');
        shape.setAttribute('cx', p[0]); shape.setAttribute('cy', p[1]); shape.setAttribute('r', 12.5 * k);
        g.setAttribute('class', 'b'); g.setAttribute('aria-label', 'Bus ' + b);
      }
      if (opts.interactive && !(isGen && b === 39)) { g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button'); }
      var ring = document.createElementNS(NS, 'circle');
      ring.setAttribute('cx', p[0]); ring.setAttribute('cy', p[1]); ring.setAttribute('r', 17 * k); ring.setAttribute('class', 'ring');
      g.appendChild(ring); g.appendChild(shape);
      label.setAttribute('x', p[0]); label.setAttribute('y', p[1] + 3.8 * k); label.textContent = isGen ? 'G' + gen : b;
      if (k > 1) label.style.fontSize = '13px';
      g.appendChild(label); svg.appendChild(g);
      nodes[b] = {g: g, shape: shape, label: label, p: p, k: k};
      if (opts.interactive) {
        var act = function () { opts.onClick(b); };
        g.addEventListener('click', act);
        g.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); act(); } });
      }
    });
    host.innerHTML = ''; host.appendChild(svg);
    return {svg: svg, nodes: nodes, tall: tall};
  }
  function paint(view, gens, ci, fault) {
    Object.keys(view.nodes).forEach(function (bk) {
      var b = +bk, n = view.nodes[b];
      if (b >= 30) {
        var ibr = b <= 38 && gens.indexOf(b - 29) >= 0;
        n.shape.setAttribute('fill', ibr ? '#0a9396' : '#ffffff');
        n.shape.setAttribute('stroke', ibr ? '#077a7d' : '#9a9993'); n.shape.setAttribute('stroke-width', 1.5);
        n.label.setAttribute('class', ibr ? 'w' : '');
        n.g.setAttribute('aria-pressed', ibr ? 'true' : 'false');
        n.g.querySelector('title') || n.g.appendChild(document.createElementNS(NS, 'title'));
        n.g.querySelector('title').textContent = b === 39 ? 'Generator 10: slack, always synchronous' :
          'Generator ' + (b - 29) + (ibr ? ': IBR (tap to make synchronous again)' : ': synchronous (tap to make it an IBR)');
        return;
      }
      var r = ci === null ? null : rec(gens, b, ci), c = r ? COL[r.v] : ['#ffffff', '#c9c8c2', ''];
      n.shape.setAttribute('fill', c[0]); n.shape.setAttribute('stroke', c[1]);
      n.shape.setAttribute('stroke-width', r && r.v === 'C' ? 2.6 : 1.5);
      if (c[2]) n.shape.setAttribute('stroke-dasharray', c[2]); else n.shape.removeAttribute('stroke-dasharray');
      n.label.setAttribute('class', r && (r.v === 'R' || r.v === 'C') ? 'w' : '');
      var old = n.g.querySelector('.sel'); if (old) old.remove();
      var oldb = n.g.querySelector('.bolt'); if (oldb) oldb.remove();
      if (b === fault) {
        var s = document.createElementNS(NS, 'circle');
        s.setAttribute('cx', n.p[0]); s.setAttribute('cy', n.p[1]); s.setAttribute('r', 17.5 * n.k); s.setAttribute('class', 'sel');
        n.g.insertBefore(s, n.shape);
        var bolt = document.createElementNS(NS, 'path'), x = n.p[0] + 13 * n.k, y = n.p[1] - 22 * n.k, q = n.k;
        bolt.setAttribute('d', 'M' + x + ' ' + y + 'l' + (-7 * q) + ' ' + (11 * q) + 'h' + (5 * q) + 'l' + (-4 * q) + ' ' + (10 * q) +
          'l' + (10 * q) + ' ' + (-13 * q) + 'h' + (-5 * q) + 'l' + (4 * q) + ' ' + (-8 * q) + 'z');
        bolt.setAttribute('class', 'bolt'); n.g.appendChild(bolt);
      }
      n.g.querySelector('title') || n.g.appendChild(document.createElementNS(NS, 'title'));
      n.g.querySelector('title').textContent = 'Bus ' + b + (r ? ': ' + NAME[r.v] : '');
      if (!reduce && b === fault) { n.shape.classList.remove('flash'); void n.shape.getBBox(); n.shape.classList.add('flash'); }
    });
  }

  // ---------------------------------------------------------------- explorer state
  var st = {gens: [], bus: 16, ci: 2}, view = null;
  function gensText(g) { return g.length ? 'IBRs on generator' + (g.length > 1 ? 's ' : ' ') + g.join(', ') : 'no IBRs'; }
  function pair(r) {   // score and threshold printed so the digits never contradict "above"/"below"
    for (var n = 3; n <= 6; n++) { var a = r.s.toFixed(n), b = r.thr.toFixed(n);
      if (a !== b && ((+a >= +b) === modelSaysRisky(r))) return [a, b]; }
    return [r.s.toFixed(6), r.thr.toFixed(6)];
  }
  function modelSaysRisky(r) { return r.f !== null ? r.f : (r.s !== null && r.s >= r.thr); }
  function screenHTML(r, bus) {
    if (r.s === null) return '<div class="sv"><b>Screen view:</b> no pre-fault features for this case (the run did not finish), so `screen` would simulate it.</div>';
    var pct = Math.round(r.s * 100), tpos = Math.min(100, Math.max(0, r.thr * 100));
    var above = modelSaysRisky(r);
    var pr = pair(r);
    var h = '<div class="sv"><b>Screen view</b> (a model trained without fault bus ' + bus + '): score ' + pr[0] +
      ', threshold ' + pr[1] + ' (' + (above ? 'above' : 'below') + ').<div class="bar"><span style="width:' + pct + '%"></span><i style="left:' + tpos + '%"></i></div>';
    if (r.d === 2) h += 'Bus ' + bus + ' is a solver-hard fault location (many unknown outcomes in the training data), so <code>screen</code> always simulates it, whatever the score.';
    else if (r.d === 1) h += '<code>screen</code> would send it to simulation.';
    else h += '<code>screen</code> would report it as "predicted OK" without simulating.';
    var risky = r.v === 'R' || r.v === 'C', unk = r.v === 'U' || r.v === 'T';
    if (risky && r.d === 0) h += ' <b style="color:#b8401c">Missed:</b> the simulation says RISKY. The screen aims to catch 95% of RISKY cases, which is why predictions are always labeled as predictions.';
    else if (risky) h += ' <b style="color:#1b7a46">Caught.</b>';
    else if (unk && r.d === 0) h += ' <b style="color:#b8401c">Not simulated,</b> although the outcome is unknown.';
    else if (r.v === 'O' && r.d === 1) h += ' A false alarm: it costs one simulation, which then shows OK.';
    h += '<br><span class="muted">The score ranks cases; it is not a calibrated probability.</span></div>';
    return h;
  }
  function render(push) {
    paint(view, st.gens, st.ci, st.bus);
    var r = rec(st.gens, st.bus, st.ci), c = CYC[st.ci];
    var counts = {O: 0, R: 0, C: 0, U: 0, T: 0};
    BUS.forEach(function (b) { counts[rec(st.gens, b, st.ci).v]++; });
    $('xpSum').innerHTML = 'With ' + gensText(st.gens) + ' and clearing in ' + c + ' cycles: <b>' + (counts.R + counts.C) +
      '</b> of 29 fault buses RISKY, <b>' + counts.O + '</b> OK' + (counts.U + counts.T ? ', <b>' + (counts.U + counts.T) + '</b> unknown' : '') + '.';
    var modes = [];
    if (r.m & 1) modes.push('rotor angle'); if (r.m & 2) modes.push('voltage recovery');
    if (r.m & 4) modes.push('IBR ride-through'); if (r.m & 8) modes.push('frequency');
    var strip = '<div class="strip" role="group" aria-label="Same case at each clearing time">';
    CYC.forEach(function (cy, j) {
      var rr = rec(st.gens, st.bus, j), col = COL[rr.v];
      strip += '<button type="button" data-ci="' + j + '" aria-pressed="' + (j === st.ci) + '" style="background:' + col[0] +
        ';border-color:' + col[1] + (rr.v === 'R' || rr.v === 'C' ? ';color:#fff' : '') + '"><b>' + cy + ' cyc</b>' + NAME[rr.v].replace(' (confirmed)', '') + '</button>';
    });
    strip += '</div>';
    $('xpCard').innerHTML = '<h3>Fault at bus ' + st.bus + ' <span class="pill ' + TONE[r.v] + '">' + NAME[r.v] + '</span></h3>' +
      '<p class="xp-sub">' + esc(gensText(st.gens)) + ' · cleared in ' + c + ' cycles (' + ms(c) + ' ms) · base load</p>' +
      (modes.length ? '<p><b>Failure mode:</b> ' + modes.join(', ') + '</p>' : '') +
      '<p>' + esc(r.text) + '</p><p class="xp-sub" style="margin-top:10px">Same grid and fault, other clearing times:</p>' + strip +
      screenHTML(r, st.bus);
    Array.prototype.forEach.call($('xpCard').querySelectorAll('.strip button'), function (b) {
      b.addEventListener('click', function () { st.ci = +b.getAttribute('data-ci'); syncCtl(); render(true); });
    });
    if (push) { var h = '#explore=g' + (st.gens.slice().sort().join('') || '0') + '-b' + st.bus + '-c' + CYC[st.ci];
      if (location.hash !== h) history.replaceState(null, '', h); }
  }
  function syncCtl() {
    Array.prototype.forEach.call($('xpGens').children, function (b) {
      b.setAttribute('aria-pressed', st.gens.indexOf(+b.getAttribute('data-g')) >= 0 ? 'true' : 'false'); });
    Array.prototype.forEach.call($('xpCyc').children, function (b) {
      b.setAttribute('aria-pressed', +b.getAttribute('data-ci') === st.ci ? 'true' : 'false'); });
  }
  function toggleGen(g) {
    var i = st.gens.indexOf(g); $('xpMsg').textContent = '';
    if (i >= 0) st.gens.splice(i, 1);
    else if (st.gens.length >= 4) { $('xpMsg').textContent = 'Up to 4 IBRs: the explorer covers 0 to 4. Turn one off first.'; return; }
    else st.gens.push(g);
    st.gens.sort(); syncCtl(); render(true);
  }
  function parseHash() {
    var m = /^#explore=g(\d*)-b(\d+)-c(\d+)$/.exec(location.hash || '');
    if (!m) return false;
    var gens = m[1] === '0' ? [] : m[1].split('').map(Number).filter(function (g) { return g >= 1 && g <= 9; });
    gens = gens.filter(function (g, i) { return gens.indexOf(g) === i; }).sort();
    var bus = +m[2], ci = CYC.indexOf(+m[3]);
    if (gens.length > 4 || bus < 1 || bus > 29 || ci < 0) return false;
    st = {gens: gens, bus: bus, ci: ci}; return true;
  }
  function initExplorer() {
    for (var g = 1; g <= 9; g++) (function (g) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = 'G' + g; b.setAttribute('data-g', g);
      b.setAttribute('aria-label', 'Generator ' + g + ' becomes an IBR'); b.addEventListener('click', function () { toggleGen(g); });
      $('xpGens').appendChild(b); })(g);
    CYC.forEach(function (c, j) {
      var b = document.createElement('button'); b.type = 'button'; b.textContent = c + ' cyc'; b.setAttribute('data-ci', j);
      b.title = ms(c) + ' ms'; b.addEventListener('click', function () { st.ci = j; syncCtl(); render(true); });
      $('xpCyc').appendChild(b); });
    var fromHash = parseHash();
    view = drawMap($('xpMap'), {interactive: true, label: 'IEEE 39-bus map: tap a generator to make it an IBR, tap a bus to fault it',
      onClick: function (b) { if (b >= 30) { if (b <= 38) toggleGen(b - 29); } else { st.bus = b; render(true); } }});
    syncCtl(); render(false);
    if (fromHash) setTimeout(function () { $('explore').scrollIntoView({behavior: reduce ? 'auto' : 'smooth'}); }, 50);
    $('xpRand').addEventListener('click', function () {
      var n = Math.floor(Math.random() * 5), pool = [1, 2, 3, 4, 5, 6, 7, 8, 9], g = [];
      while (g.length < n) g.push(pool.splice(Math.floor(Math.random() * pool.length), 1)[0]);
      st = {gens: g.sort(), bus: 1 + Math.floor(Math.random() * 29), ci: Math.floor(Math.random() * NC)};
      $('xpMsg').textContent = ''; syncCtl(); render(true); });
    $('xpReset').addEventListener('click', function () { st = {gens: [], bus: 16, ci: 2}; $('xpMsg').textContent = ''; syncCtl(); render(true); });
    $('xpCopy').addEventListener('click', function () {
      render(true); var url = location.href, btn = $('xpCopy');
      function done(t) { btn.textContent = t; setTimeout(function () { btn.textContent = 'Copy link to this case'; }, 1800); }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(function () { done('Link copied'); }, function () { prompt('Copy this link:', url); });
      else prompt('Copy this link:', url);
    });
    window.addEventListener('hashchange', function () { if (parseHash()) { syncCtl(); render(false); $('explore').scrollIntoView(); } });
    var rt = null, lastW = $('xpMap').clientWidth;
    window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(function () {
      var w = $('xpMap').clientWidth; if (Math.abs(w - lastW) < 2) return; lastW = w;
      view = drawMap($('xpMap'), {interactive: true, label: 'IEEE 39-bus map', onClick: function (b) {
        if (b >= 30) { if (b <= 38) toggleGen(b - 29); } else { st.bus = b; render(true); } }});
      render(false); if (q.view) { q.view = drawMap($('qMap'), {interactive: false, label: 'Quiz case'}); paintQuiz(); } }, 150); });
  }

  // ---------------------------------------------------------------- quiz
  var q = {n: 0, you: 0, model: 0, cur: null, done: false, view: null};
  var known = []; for (var i = 0; i < D.v.length; i++) if (D.v[i] === 'O' || D.v[i] === 'R' || D.v[i] === 'C') known.push(i);
  function decode(i) {
    var ci = i % NC, bus = Math.floor(i / NC) % NB + 1, combo = D.combos[Math.floor(i / (NC * NB))];
    return {gens: combo ? combo.split('').map(Number) : [], bus: bus, ci: ci};
  }
  function pick() {     // half RISKY, half OK, at random
    var wantRisky = Math.random() < 0.5;
    for (var t = 0; t < 200; t++) { var i = known[Math.floor(Math.random() * known.length)];
      if ((D.v[i] !== 'O') === wantRisky) return i; }
    return known[Math.floor(Math.random() * known.length)];
  }
  function paintQuiz() { var c = decode(q.cur); paint(q.view, c.gens, q.done ? c.ci : null, c.bus); }
  function ask() {
    q.cur = pick(); q.done = false; var c = decode(q.cur), cy = CYC[c.ci];
    $('qN').textContent = q.n + 1; $('qProg').style.width = (q.n * 10) + '%';
    $('qText').innerHTML = esc(gensText(c.gens).replace(/^./, function (s) { return s.toUpperCase(); })) + '. A bolted three-phase fault at <b>bus ' +
      c.bus + '</b> is cleared in <b>' + cy + ' cycles</b> (' + ms(cy) + ' ms), base load. <b>RISKY or OK?</b>';
    $('qReveal').innerHTML = ''; $('qNext').hidden = true; $('qExplore').hidden = true;
    $('qOK').disabled = false; $('qRisky').disabled = false; paintQuiz();
  }
  function answer(guessRisky) {
    if (q.done) return; q.done = true; $('qOK').disabled = true; $('qRisky').disabled = true;
    var c = decode(q.cur), r = rec(c.gens, c.bus, c.ci), truth = r.v !== 'O';
    var right = guessRisky === truth, modelRisky = modelSaysRisky(r), modelRight = modelRisky === truth;
    q.you += right ? 1 : 0; q.model += modelRight ? 1 : 0; q.n++;
    $('qYou').textContent = q.you; $('qModel').textContent = q.model; $('qProg').style.width = (q.n * 10) + '%';
    var h = '<div class="reveal ' + (right ? 'right' : 'wrong') + '"><b>' + (right ? 'Right!' : 'Not this time.') + '</b> The simulation says <span class="pill ' +
      TONE[r.v] + '">' + NAME[r.v] + '</span><p>' + esc(r.text) + '</p><p class="xp-sub">Screening model: score ' +
      (r.s === null ? 'n/a vs. threshold ' + r.thr.toFixed(3) : pair(r)[0] + ' vs. threshold ' + pair(r)[1]) + ', so it would have said <b>' +
      (modelRisky ? 'RISKY' : 'OK') + '</b> (' + (modelRight ? 'right' : 'wrong') + ').' +
      (modelRisky && !truth ? ' Its threshold is set low on purpose, to catch 95% of RISKY cases, so it leans toward RISKY: a false alarm only costs one simulation.' : '') +
      '</p></div>';
    if (q.n >= 10) {
      h += '<p class="qq"><b>Round over: you ' + q.you + ' of 10, the screening model ' + q.model + ' of 10.</b> ' +
        (q.you >= 8 ? 'Strong engineering intuition.' : q.you >= 6 ? 'Good. Long clearing times and many IBRs are the usual warning signs, but not always.' :
        'These cases are hard to judge by eye, which is why screening tools exist.') + '</p>';
      $('qNext').textContent = 'Play again';
    } else $('qNext').textContent = 'Next question';
    $('qReveal').innerHTML = h; $('qNext').hidden = false;
    $('qExplore').hidden = false;
    $('qExplore').setAttribute('href', '#explore=g' + (c.gens.join('') || '0') + '-b' + c.bus + '-c' + CYC[c.ci]);
    paintQuiz(); $('qNext').focus({preventScroll: true});
  }
  function initQuiz() {
    q.view = drawMap($('qMap'), {interactive: false, label: 'Quiz case on the IEEE 39-bus map'});
    $('qOK').addEventListener('click', function () { answer(false); });
    $('qRisky').addEventListener('click', function () { answer(true); });
    $('qNext').addEventListener('click', function () { if (q.n >= 10) { q.n = 0; q.you = 0; q.model = 0;
      $('qYou').textContent = 0; $('qModel').textContent = 0; } ask(); });
    ask();
  }
  initExplorer(); initQuiz();
})();
"""


def explorer_layout_json(layout, edges) -> str:
    """Map positions (0..1, y down) and branches for the page script."""
    return json.dumps({"pos": {int(b): [round(x, 4), round(y, 4)] for b, (x, y) in layout.items()},
                       "edges": [[int(a), int(b), 1 if xf else 0] for a, b, xf in edges]},
                      separators=(",", ":"))


def inject(html: str, layout, edges, data_src: str = "explorer-data.js") -> str:
    """Add the explorer and quiz sections after the network map section."""
    anchor = "<section><h2>Ranked scenarios"
    if anchor not in html or "</style>" not in html or "</body>" not in html:
        raise ValueError("unexpected report layout: cannot place the explorer")
    html = html.replace("</style>", CSS + "</style>", 1)
    html = html.replace(anchor, HTML + "\n" + anchor, 1)
    script = (f'<script>window.EXPLORER_LAYOUT={explorer_layout_json(layout, edges)};</script>\n'
              f'<script src="{data_src}"></script>\n<script>{JS}</script>\n')
    return html.replace("</body>", script + "</body>", 1)
