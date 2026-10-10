"""Trajectory plots for reviewing one scenario.

`plot_scenario(result, labels, settings, path)` saves a four-panel PNG:

1. Bus voltages (all buses in grey; the faulted bus and each IBR's
   high-side bus highlighted), with the voltage-recovery threshold.
2. Rotor-angle spread (largest minus smallest machine angle) with the limit.
3. Bus frequency measured over `freq_window_s`, with limits; the skipped
   fault period is shaded.
4. IBR current as a fraction of its limit.

The voltage ride-through bands are drawn as faint horizontal lines on panel 1.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")                       # no screen needed
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .criteria import CriteriaSettings  # noqa: E402
from .label import ScenarioLabels  # noqa: E402
from .simulate import SimResult  # noqa: E402

# Colors from the validated reference palette (checked with the dataviz validator):
# orange marks the faulted bus; IBRs take blue, aqua, violet, magenta in a fixed
# order (a fifth IBR onward is drawn in secondary ink). Legends name every series.
C_FAULT = "#eb6834"
IBR_COLORS = ("#2a78d6", "#1baf7a", "#4a3aa7", "#e87ba4")
INK, INK2, GRID, BG_LINE = "#0b0b0b", "#52514e", "#e6e5e0", "#c9c8c2"


def _ibr_color(k):
    return IBR_COLORS[k] if k < len(IBR_COLORS) else INK2


def _style(ax, ylabel):
    ax.set_ylabel(ylabel, color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BG_LINE)


def _legend(ax):
    """Legend in one row just above the panel, clear of the data and limit labels."""
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=7, frameon=False, loc="lower right", bbox_to_anchor=(1, 1),
                  ncol=4, borderaxespad=0.2, handlelength=1.6)


def _limit(ax, y, text):
    ax.axhline(y, color=INK2, linestyle="--", linewidth=1)
    ax.annotate(text, xy=(0, y), xycoords=("axes fraction", "data"), xytext=(4, 3),
                textcoords="offset points", ha="left", fontsize=7, color=INK2)


def _round(a, nd):
    """List of rounded floats for JSON; NaN/inf become None."""
    a = np.asarray(a, dtype=float)
    return [None if not np.isfinite(x) else round(float(x), nd) for x in a]


def trace_payload(result: SimResult, labels: ScenarioLabels, settings: CriteriaSettings) -> dict:
    """The data behind `plot_scenario`, as plain JSON for the interactive report.

    Same series and limits as the PNG: faulted bus and IBR high-side buses
    highlighted; the other buses are drawn as a min-max band (the envelope of
    every bus) to keep the report small. When a bolted run failed the
    milder rerun is shown, with a banner saying so.
    """
    sc = result.scenario
    verdict = "RISKY" if labels.risky else "OK"
    if labels.resolution:
        verdict += f" ({labels.resolution})"
    fault = sc.fault
    ftxt = "no fault" if fault is None else (
        f"fault at bus {fault.bus}, cleared in {fault.clear_time * 1000:.1f} ms "
        f"({fault.clear_time * 60:.1f} cycles)")
    out = {
        "id": sc.scenario_id, "verdict": verdict, "risky": bool(labels.risky),
        "flags": [k for k, v in labels.flags.items() if v is True],
        "status": result.status,
        "sub": f"{ftxt} · load ×{sc.load_scale:g} · status: {result.status}",
        "ibrs": "IBRs: " + (", ".join(f"{c['kind']} on gen {c['gen']} (bus {c['bus']}→{c['hv_bus']})"
                                      for c in result.changes) or "none"),
        "banner": None, "reasons": list(labels.reasons), "panels": None,
        "message": result.message,
    }
    tr = result.traj
    diag = result.solved_diagnostic() if result.status == "sim_fail" else None
    if diag is not None:
        tr, fault = diag.traj, diag.scenario.fault
        resid = ("" if diag.fault_residual_pu is None
                 else f", faulted bus at {diag.fault_residual_pu:.2f} pu")
        out["banner"] = (f"Bolted run failed. Showing a MILDER fault (x = {fault.x_pu:g} pu{resid}), "
                         "NOT the bolted case.")
    if tr is None:
        return out

    t = tr.t
    shade = None if fault is None else [fault.t_start, fault.t_start + fault.clear_time]
    hv_buses = list(dict.fromkeys(tr.ibr_hv_bus))
    panels = []

    # 1. Voltage
    series = []
    if fault is not None and fault.bus in tr.bus_ids:
        series.append({"name": f"faulted bus {fault.bus}", "color": C_FAULT, "w": 3.5,
                       "y": _round(tr.bus_v(fault.bus), 4)})
    for k, hv in enumerate(hv_buses):
        series.append({"name": f"IBR bus {hv}", "color": _ibr_color(k), "w": 2,
                       "y": _round(tr.bus_v(hv), 4)})
    lim = []
    if settings.v_recovery_min_pu is not None:
        lim.append({"y": settings.v_recovery_min_pu,
                    "text": f"recovery {settings.v_recovery_min_pu:g} pu within "
                            f"{settings.v_recovery_time_s:g} s"})
    edges = sorted({e for env in settings.envelopes if env.quantity == "voltage"
                    for b in env.bands for e in (b.low, b.high) if 0 < e < 1.5})
    vmax = float(np.nanmax(tr.v)) if tr.v.size else 1.2
    panels.append({"key": "v", "label": "Voltage (pu)", "unit": "pu", "nd": 3,
                   "ylim": [0, max(1.25, vmax * 1.05)], "band": [_round(tr.v.min(axis=1), 4),
                                                                 _round(tr.v.max(axis=1), 4)],
                   "band_name": "all buses (range)", "series": series, "limits": lim,
                   "faint": edges, "shade": shade})

    # 2. Angle spread
    if tr.delta.shape[1] >= 2:
        spread = np.degrees(tr.delta.max(axis=1) - tr.delta.min(axis=1))
        top = max(settings.angle_limit_deg, float(np.nanmax(spread))) * 1.1
        panels.append({"key": "a", "label": "Rotor-angle spread (°)", "unit": "°", "nd": 1,
                       "ylim": [0, top], "series": [{"name": "angle spread", "color": INK, "w": 2,
                                                     "y": _round(spread, 2)}],
                       "limits": [{"y": settings.angle_limit_deg,
                                   "text": f"limit {settings.angle_limit_deg:g}°"}],
                       "shade": shade})

    # 3. Frequency
    win = settings.freq_window_s
    if t[-1] - t[0] > win:
        fall = tr.frequency(win)
        skip = np.zeros(t.size, dtype=bool)
        skip_span = None
        if shade is not None:
            skip_span = [shade[0], shade[1] + win]
            skip = (t >= skip_span[0]) & (t < skip_span[1])
        seen = fall[~skip]
        flims = [x for x in (settings.f_min_hz, settings.f_max_hz) if x is not None]
        lo, hi = min([float(np.nanmin(seen))] + flims), max([float(np.nanmax(seen))] + flims)
        pad = max(0.1, 0.1 * (hi - lo))
        panels.append({
            "key": "f", "label": "Frequency (Hz)", "unit": "Hz", "nd": 3,
            "ylim": [lo - pad, hi + pad],
            "band": [_round(fall.min(axis=1), 4), _round(fall.max(axis=1), 4)],
            "band_name": "all buses (range)",
            "series": [{"name": f"IBR bus {hv}", "color": _ibr_color(k), "w": 2,
                        "y": _round(tr.bus_f(hv, win), 4)} for k, hv in enumerate(hv_buses)],
            "limits": [{"y": lim_, "text": f"{nm} {lim_:g} Hz"} for lim_, nm in
                       ((settings.f_min_hz, "min"), (settings.f_max_hz, "max")) if lim_ is not None],
            "shade": skip_span, "shade_name": "not checked (fault + one window)"})

    # 4. IBR current
    if tr.ibr_imax.size:
        frac = np.hypot(tr.ibr_ip, tr.ibr_iq) / tr.ibr_imax
        series = []
        for j, _ibr in enumerate(tr.ibr_ids):
            k = hv_buses.index(tr.ibr_hv_bus[j]) if j < len(tr.ibr_hv_bus) else j
            series.append({"name": f"bus {tr.ibr_hv_bus[j]} |I|", "color": _ibr_color(k), "w": 2,
                           "y": _round(frac[:, j], 4)})
            series.append({"name": f"bus {tr.ibr_hv_bus[j]} reactive", "color": _ibr_color(k),
                           "w": 1.2, "dash": True,
                           "y": _round(np.abs(tr.ibr_iq[:, j]) / tr.ibr_imax[j], 4)})
        panels.append({"key": "i", "label": "IBR current / limit", "unit": "", "nd": 3,
                       "ylim": [0, 1.15], "series": series,
                       "limits": [{"y": 1.0, "text": "current limit"}], "shade": shade,
                       "note": "dotted = reactive part"})
    else:
        panels.append({"key": "i", "label": "IBR current / limit", "empty": "no IBRs in this scenario"})

    out["t"] = _round(t, 4)
    out["panels"] = panels
    return out


def plot_scenario(result: SimResult, labels: ScenarioLabels, settings: CriteriaSettings,
                  path: str | Path) -> Path:
    """Save the review figure for one scenario and return its path."""
    path = Path(path)
    sc = result.scenario
    fig, axes = plt.subplots(4, 1, figsize=(9, 10.5), sharex=True,
                             gridspec_kw={"hspace": 0.32})
    verdict = "RISKY" if labels.risky else "OK"
    if labels.resolution:
        verdict += f" ({labels.resolution})"
    flags = [k for k, v in labels.flags.items() if v is True]
    fault = sc.fault
    ftxt = "no fault" if fault is None else (
        f"fault at bus {fault.bus}, cleared in {fault.clear_time * 1000:.1f} ms "
        f"({fault.clear_time * 60:.1f} cycles)")
    ibrs = "IBRs: " + (", ".join(f"{c['kind']} on gen {c['gen']} (bus {c['bus']}→{c['hv_bus']})"
                                for c in result.changes) or "none")
    fig.suptitle(f"{sc.scenario_id}: {verdict}" + (f"  [{', '.join(flags)}]" if flags else ""),
                 x=0.06, ha="left", fontsize=12, color=INK, fontweight="bold")
    fig.text(0.06, 0.945, f"{ftxt} · load ×{sc.load_scale:g} · status: {result.status}\n{ibrs}",
             fontsize=8, color=INK2, va="top", linespacing=1.5)

    tr = result.traj
    diag = result.solved_diagnostic() if result.status == "sim_fail" else None
    if diag is not None:
        # The bolted run failed; show the milder rerun, clearly marked.
        tr, fault = diag.traj, diag.scenario.fault
        resid = ("" if diag.fault_residual_pu is None
                 else f", faulted bus at {diag.fault_residual_pu:.2f} pu")
        fig.text(0.06, 0.905, f"Bolted run failed. Showing a MILDER fault (x = {fault.x_pu:g} pu"
                 f"{resid}), NOT the bolted case.", fontsize=8.5, color=C_FAULT,
                 fontweight="bold", va="top")
    if tr is None:
        for ax in axes:
            ax.axis("off")
        axes[0].text(0, 0.5, f"No trajectories: {result.message}", fontsize=10, color=INK)
        fig.savefig(path, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return path

    t = tr.t
    shade = None if fault is None else (fault.t_start, fault.t_start + fault.clear_time)

    # 1. Voltages
    ax = axes[0]
    ax.plot(t, tr.v, color=BG_LINE, linewidth=0.7)
    for env in settings.envelopes:
        if env.quantity == "voltage":
            for edge in sorted({b.low for b in env.bands} | {b.high for b in env.bands}):
                if 0 < edge < 1.5:
                    ax.axhline(edge, color=GRID, linewidth=0.8, zorder=0)
    if fault is not None and fault.bus in tr.bus_ids:
        # drawn wider, so it stays visible when the faulted bus is also an IBR bus
        ax.plot(t, tr.bus_v(fault.bus), color=C_FAULT, linewidth=3.5, label=f"faulted bus {fault.bus}")
    hv_buses = list(dict.fromkeys(tr.ibr_hv_bus))
    for k, hv in enumerate(hv_buses):
        ax.plot(t, tr.bus_v(hv), color=_ibr_color(k), linewidth=2, label=f"IBR bus {hv}")
    if settings.v_recovery_min_pu is not None:
        _limit(ax, settings.v_recovery_min_pu,
               f"recovery {settings.v_recovery_min_pu:g} pu within {settings.v_recovery_time_s:g} s")
    _style(ax, "Voltage (pu)")
    ax.set_ylim(bottom=0)
    _legend(ax)

    # 2. Angle spread
    ax = axes[1]
    if tr.delta.shape[1] >= 2:
        spread = np.degrees(tr.delta.max(axis=1) - tr.delta.min(axis=1))
        ax.plot(t, spread, color=INK, linewidth=2)
        _limit(ax, settings.angle_limit_deg, f"limit {settings.angle_limit_deg:g}°")
        ax.set_ylim(0, max(settings.angle_limit_deg, float(spread.max())) * 1.1)
    _style(ax, "Rotor-angle spread (°)")

    # 3. Frequency
    ax = axes[2]
    win = settings.freq_window_s
    if t[-1] - t[0] > win:
        fall = tr.frequency(win)
        ax.plot(t, fall, color=BG_LINE, linewidth=0.7)
        for k, hv in enumerate(hv_buses):
            ax.plot(t, tr.bus_f(hv, win), color=_ibr_color(k), linewidth=2, label=f"IBR bus {hv}")
        skip = np.zeros(t.size, dtype=bool)
        if shade is not None:
            skip = (t >= shade[0]) & (t < shade[1] + win)
            ax.axvspan(shade[0], shade[1] + win, color=GRID, alpha=0.7,
                       label="not checked (fault + one window)")
        # Scale to the checked samples so a skipped phase-jump spike does not flatten the plot.
        seen = fall[~skip]
        lims = [x for x in (settings.f_min_hz, settings.f_max_hz) if x is not None]
        lo, hi = min([seen.min()] + lims), max([seen.max()] + lims)
        pad = max(0.1, 0.1 * (hi - lo))
        ax.set_ylim(lo - pad, hi + pad)
        for lim, name in ((settings.f_min_hz, "min"), (settings.f_max_hz, "max")):
            if lim is not None:
                _limit(ax, lim, f"{name} {lim:g} Hz")
        _legend(ax)
    _style(ax, "Frequency (Hz)")

    # 4. IBR current
    ax = axes[3]
    if tr.ibr_imax.size:
        frac = np.hypot(tr.ibr_ip, tr.ibr_iq) / tr.ibr_imax
        for j, ibr in enumerate(tr.ibr_ids):
            k = hv_buses.index(tr.ibr_hv_bus[j]) if j < len(tr.ibr_hv_bus) else j
            ax.plot(t, frac[:, j], linewidth=2, color=_ibr_color(k), label=f"bus {tr.ibr_hv_bus[j]} |I|")
            ax.plot(t, np.abs(tr.ibr_iq[:, j]) / tr.ibr_imax[j], linewidth=1, linestyle=":",
                    color=_ibr_color(k), label="reactive part (dotted)" if j == 0 else "_nolegend_")
        _limit(ax, 1.0, "current limit")
        ax.set_ylim(0, 1.15)
        _legend(ax)
    else:
        ax.text(0.5, 0.5, "no IBRs in this scenario", transform=ax.transAxes,
                ha="center", color=INK2, fontsize=9)
    _style(ax, "IBR current / limit")
    ax.set_xlabel("Time (s)", color=INK2, fontsize=9)

    if shade is not None:
        for ax in axes[:2]:
            ax.axvspan(*shade, color=GRID, alpha=0.5, zorder=0)

    reasons = labels.reasons
    if reasons:
        txt = "\n".join(r if len(r) < 150 else r[:147] + "…" for r in reasons[:4])
        fig.text(0.06, 0.01, txt, fontsize=7, color=INK2, va="bottom")
    fig.subplots_adjust(top=0.875 if diag is not None else 0.885, bottom=0.08 + 0.018 * min(len(reasons), 4))
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
