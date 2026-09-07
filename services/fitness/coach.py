"""System prompt for the fitness coach chat mode.

Lived twice, verbatim, in routes/chat_routes.py. Beyond the drift risk, it
described the data purely as a list of files and left the model to open,
parse and mentally aggregate fourteen days of markdown before it could say
anything — so every conversation started from raw numbers with no frame of
reference. The digest below front-loads the computed indicators, and the files
remain available for the detail.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Dict, List, Optional

from services.fitness.analytics import analyze, notable

_BASE = """You are a world-class fitness coach with file I/O tools for the user's local fitness data directory.

Files available to you:
- messwerte_log.md — vitals and Apple Watch data for the last 14 days
- temporaere_notizen.md — short-lived context (injury, poor sleep, travel, stress)
- ziele.md — long-term goals
- wochenplan.md / trainingsplan.md — current routines
- fitness_metrics.json — dashboard state and the current Condition Score
- analyse.md — your own written analyses, if you keep any

How to work:
- The indicator digest below is already computed from the history. Trust it and
  interpret it; do not recompute averages from the raw log.
- A z-score is the deviation from this person's OWN 28-day baseline in standard
  deviations, so it already accounts for individual normal ranges. |z| under 1
  is unremarkable; 1.5 and above deserves comment.
- The acute:chronic workload ratio compares the last 7 days of load to the last
  28. Roughly 0.8-1.3 is the sweet spot, above 1.5 marks a load spike with
  elevated injury risk, below 0.8 means load has dropped away.
- Weigh hard vitals against the soft factors in the notes. A poor recovery
  reading after a night out is not the same finding as one without explanation.
- Say when the data is too thin to support a conclusion. Recommending a hard
  session off three data points is worse than saying you cannot tell yet.
- Be specific and actionable: name sets, intensities, durations or a rest day,
  not general encouragement.
- You are not a physician. For anything suggesting injury or illness, say so
  plainly and recommend professional care instead of training through it.

Answer in German by default as the user prefers German."""


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build_digest(workspace: str, *, today: Optional[date] = None) -> str:
    """Compact, model-readable summary of the current indicators."""
    history = _read_json(os.path.join(workspace, "fitness_history.json"), {})
    if not isinstance(history, dict) or not history:
        return "INDIKATOREN: noch keine Messwerte erfasst."

    clean = {
        d: {k: v for k, v in e.items() if k != "_samples"}
        for d, e in history.items() if isinstance(e, dict)
    }
    bundle = analyze(clean, today=today or date.today())
    highlights = notable(bundle)

    quality = bundle.get("data_quality", {})
    lines: List[str] = [
        f"INDIKATOREN (Stand {bundle.get('as_of')}, "
        f"{quality.get('days_with_any_data', 0)}/{quality.get('window_days', 14)} Tage mit Daten):"
    ]

    for metric, info in sorted((bundle.get("metrics") or {}).items()):
        z = info.get("z")
        z_txt = f"z={z:+.1f}" if isinstance(z, (int, float)) else "z=n/a"
        base = info.get("baseline_mean")
        base_txt = f"Ø{base:g}" if isinstance(base, (int, float)) else "Ø n/a"
        tr = info.get("trend_7d") or {}
        run = tr.get("run") or 0
        run_txt = f", {abs(run)}d {'steigend' if run > 0 else 'fallend'}" if abs(run) >= 3 else ""
        stale = info.get("stale_days") or 0
        stale_txt = f", zuletzt vor {stale}d" if stale >= 2 else ""
        lines.append(
            f"- {metric}: {info.get('latest')} ({base_txt}, {z_txt}{run_txt}{stale_txt})"
        )

    wl = bundle.get("workload") or {}
    if wl.get("ratio") is not None:
        lines.append(
            f"- Workload (acute:chronic auf {wl.get('metric')}): {wl['ratio']} [{wl.get('band')}]"
            + (f", Monotonie {wl['monotony']}" if wl.get("monotony") else "")
        )

    if highlights:
        lines.append("AUFFÄLLIG:")
        lines.extend(f"- [{h['severity']}] {h['text']}" for h in highlights)

    return "\n".join(lines)


def build_system_prompt(workspace: Optional[str], *, today: Optional[date] = None) -> str:
    """The coach prompt, with the live indicator digest appended."""
    if not workspace or not os.path.isdir(workspace):
        return _BASE
    try:
        digest = build_digest(workspace, today=today)
    except Exception:
        return _BASE
    return f"{_BASE}\n\n{digest}"
