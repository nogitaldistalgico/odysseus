"""Deterministic analysis of the fitness history.

The coach used to hand fourteen days of raw markdown to a language model and
ask it for a number. That has no anchor: an HRV of 45 ms is excellent for one
person and a warning sign for another, so without a personal baseline every
score is invented, and the same data produces a different answer tomorrow.

This module turns the raw history into indicators that mean something on their
own — z-scores against the user's own trailing baseline, workload ratios,
direction runs, data coverage. The model's job then shifts from "guess a
number" to "interpret these indicators", which is a job it is actually good at.

Everything here is pure and dependency-free so it can be unit-tested against
constructed series with known answers.

References for the sports-science parts:
  * Acute:Chronic Workload Ratio — Gabbett, Br J Sports Med 2016
  * Training monotony / strain — Foster, Med Sci Sports Exerc 1998
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

# How a metric should be read when judging "better" or "worse". Matched as a
# substring against the flattened metric path, longest match wins.
_HIGHER_IS_BETTER = (
    "hrv", "rmssd", "sdnn", "sleep", "vo2", "recovery", "condition",
    "readiness", "steps", "spo2", "oxygen",
)
_LOWER_IS_BETTER = (
    "resting", "rhr", "restingheartrate", "stress", "respiratory", "bodyfat",
)
# Series treated as training load for the workload ratios.
_LOAD_HINTS = ("movement.current", "active_energy", "activeenergy", "kcal",
               "calories", "load", "training_load", "exertion")

MIN_BASELINE_DAYS = 5      # below this a baseline is not meaningful
ACUTE_WINDOW = 7
CHRONIC_WINDOW = 28


# ---------------------------------------------------------------------------
#  Flattening
# ---------------------------------------------------------------------------

def flatten_numeric(entry: Any, prefix: str = "") -> Dict[str, float]:
    """Collect every numeric leaf of one history entry as ``path -> value``.

    The dashboard endpoint accepts arbitrary JSON, so the metric set is
    whatever the client sends. Discovering it rather than hardcoding a schema
    means a new Apple Watch field starts being analysed the day it appears.
    """
    out: Dict[str, float] = {}
    if isinstance(entry, dict):
        for key, val in entry.items():
            out.update(flatten_numeric(val, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(entry, bool):
        pass  # booleans are flags, not measurements
    elif isinstance(entry, (int, float)) and math.isfinite(entry):
        out[prefix] = float(entry)
    elif isinstance(entry, str):
        # "72", "72.5 bpm" — the client is not schema-checked, so tolerate it.
        cleaned = entry.strip().replace(",", ".")
        head = cleaned.split(" ")[0]
        try:
            val = float(head)
            if math.isfinite(val):
                out[prefix] = val
        except ValueError:
            pass
    return out


def build_series(history: Dict[str, Any]) -> Dict[str, List[Tuple[date, float]]]:
    """Turn ``{date: entry}`` into ``{metric: [(date, value), ...]}``, sorted."""
    series: Dict[str, List[Tuple[date, float]]] = {}
    for day, entry in (history or {}).items():
        try:
            when = datetime.strptime(str(day), "%Y-%m-%d").date()
        except ValueError:
            continue
        for metric, value in flatten_numeric(entry).items():
            series.setdefault(metric, []).append((when, value))
    for values in series.values():
        values.sort(key=lambda pair: pair[0])
    return series


# ---------------------------------------------------------------------------
#  Statistics
# ---------------------------------------------------------------------------

def _mean(values: Iterable[float]) -> Optional[float]:
    vals = list(values)
    return sum(vals) / len(vals) if vals else None


def _stdev(values: Iterable[float]) -> Optional[float]:
    vals = list(values)
    if len(vals) < 2:
        return None
    mu = sum(vals) / len(vals)
    return math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))


def _window(series: List[Tuple[date, float]], end: date, days: int,
            *, exclude_end: bool = False) -> List[float]:
    """The values falling in the *days* calendar days ending at *end*.

    With ``exclude_end`` the window ends the day before, so a 28-day baseline
    really covers 28 days and not 27 — deriving the start from ``end`` and then
    dropping the last day silently loses one.
    """
    last = end - timedelta(days=1) if exclude_end else end
    first = last - timedelta(days=days - 1)
    return [v for d, v in series if first <= d <= last]


def direction_for(metric: str) -> str:
    """'higher', 'lower' or 'unknown' — which way is an improvement."""
    name = metric.lower()
    hi = max((len(k) for k in _HIGHER_IS_BETTER if k in name), default=0)
    lo = max((len(k) for k in _LOWER_IS_BETTER if k in name), default=0)
    if hi == lo == 0:
        return "unknown"
    return "higher" if hi >= lo else "lower"


def baseline(series: List[Tuple[date, float]], end: date,
             days: int = CHRONIC_WINDOW) -> Dict[str, Any]:
    """Trailing mean/sd for a metric, excluding the day being judged.

    Excluding today matters: including it drags the baseline toward the value
    being measured and flattens exactly the deviation we want to see.
    """
    vals = _window(series, end, days, exclude_end=True)
    return {
        "mean": _mean(vals),
        "sd": _stdev(vals),
        "n": len(vals),
        "sufficient": len(vals) >= MIN_BASELINE_DAYS,
    }


def zscore(value: float, base: Dict[str, Any]) -> Optional[float]:
    """Standard score against a baseline, or None when it cannot be computed."""
    if not base.get("sufficient"):
        return None
    mean, sd = base.get("mean"), base.get("sd")
    if mean is None or sd is None or sd <= 1e-9:
        return None
    return round((value - mean) / sd, 2)


def trend(series: List[Tuple[date, float]], end: date, days: int = 7) -> Dict[str, Any]:
    """Least-squares slope per day plus the current run of same-direction moves.

    The run length is the part a 14-day prose log hides: a resting heart rate
    climbing five days straight is the classic early signal for illness or
    accumulated fatigue, and it is invisible in a list of numbers.
    """
    start = end - timedelta(days=days)
    points = [(d, v) for d, v in series if start < d <= end]
    if len(points) < 3:
        return {"slope_per_day": None, "direction": "unknown", "run": 0, "n": len(points)}

    x0 = points[0][0]
    xs = [(d - x0).days for d, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    slope = None if denom <= 1e-9 else sum(
        (x - mx) * (y - my) for x, y in zip(xs, ys)
    ) / denom

    run = 0
    for i in range(len(ys) - 1, 0, -1):
        delta = ys[i] - ys[i - 1]
        if abs(delta) < 1e-9:
            break
        step = 1 if delta > 0 else -1
        if run == 0:
            run = step
        elif (run > 0) == (step > 0):
            run += step
        else:
            break

    if slope is None or abs(slope) < 1e-9:
        heading = "flat"
    else:
        heading = "rising" if slope > 0 else "falling"

    return {
        "slope_per_day": None if slope is None else round(slope, 4),
        "direction": heading,
        "run": run,
        "n": len(points),
    }


def acwr(load: List[Tuple[date, float]], end: date) -> Dict[str, Any]:
    """Acute:chronic workload ratio and its risk band.

    Ratio of the last 7 days' average load to the last 28 days' average. The
    widely used interpretation puts 0.8–1.3 in the 'sweet spot', below 0.8 as
    detraining and above 1.5 as a sharp spike in injury risk.
    """
    acute = _window(load, end, ACUTE_WINDOW)
    chronic = _window(load, end, CHRONIC_WINDOW)
    if len(acute) < 3 or len(chronic) < MIN_BASELINE_DAYS:
        return {"ratio": None, "band": "insufficient_data",
                "acute_days": len(acute), "chronic_days": len(chronic)}

    acute_mean, chronic_mean = _mean(acute), _mean(chronic)
    if not chronic_mean:
        return {"ratio": None, "band": "insufficient_data",
                "acute_days": len(acute), "chronic_days": len(chronic)}

    ratio = acute_mean / chronic_mean
    if ratio < 0.8:
        band = "undertraining"
    elif ratio <= 1.3:
        band = "optimal"
    elif ratio <= 1.5:
        band = "elevated"
    else:
        band = "high_risk"
    return {
        "ratio": round(ratio, 2),
        "band": band,
        "acute_days": len(acute),
        "chronic_days": len(chronic),
    }


def monotony_strain(load: List[Tuple[date, float]], end: date,
                    days: int = ACUTE_WINDOW) -> Dict[str, Any]:
    """Foster's training monotony and strain over the acute window.

    Monotony is mean daily load over its standard deviation; sustained values
    above ~2.0 mark training that never varies, which correlates with
    staleness and illness. Strain is weekly load times monotony.
    """
    vals = _window(load, end, days)
    if len(vals) < 3:
        return {"monotony": None, "strain": None, "n": len(vals)}
    mu, sd = _mean(vals), _stdev(vals)
    if not sd or sd <= 1e-9:
        return {"monotony": None, "strain": None, "n": len(vals)}
    mono = mu / sd
    return {
        "monotony": round(mono, 2),
        "strain": round(sum(vals) * mono, 1),
        "n": len(vals),
    }


def coverage(series: List[Tuple[date, float]], end: date, days: int = 14) -> Dict[str, Any]:
    """How much of the window actually has data.

    A score derived from three days should not look as confident as one
    derived from fourteen, so the count travels with the indicators.
    """
    start = end - timedelta(days=days)
    have = {d for d, _ in series if start < d <= end}
    return {
        "days_with_data": len(have),
        "window_days": days,
        "ratio": round(len(have) / days, 2) if days else 0.0,
    }


# ---------------------------------------------------------------------------
#  Bundle
# ---------------------------------------------------------------------------

def is_load_metric(metric: str) -> bool:
    name = metric.lower()
    return any(hint in name for hint in _LOAD_HINTS)


def analyze(history: Dict[str, Any], *, today: Optional[date] = None,
            window_days: int = 14) -> Dict[str, Any]:
    """Compact indicator bundle for one user's history.

    Shaped to be small enough to paste into a prompt whole: per-metric latest
    value, baseline, z-score and trend, plus workload ratios for whichever
    series looks like training load.
    """
    end = today or date.today()
    series = build_series(history)

    metrics: Dict[str, Any] = {}
    for metric, points in sorted(series.items()):
        recent = [(d, v) for d, v in points if d <= end]
        if not recent:
            continue
        last_day, last_value = recent[-1]
        base = baseline(recent, end)
        metrics[metric] = {
            "latest": round(last_value, 3),
            "latest_date": last_day.isoformat(),
            "stale_days": (end - last_day).days,
            "direction_good": direction_for(metric),
            "baseline_mean": None if base["mean"] is None else round(base["mean"], 3),
            "baseline_sd": None if base["sd"] is None else round(base["sd"], 3),
            "baseline_days": base["n"],
            "z": zscore(last_value, base),
            "trend_7d": trend(recent, end, 7),
            "coverage": coverage(recent, end, window_days),
        }

    load_metric = next((m for m in sorted(series) if is_load_metric(m)), None)
    workload: Dict[str, Any] = {"metric": load_metric}
    if load_metric:
        load_points = [(d, v) for d, v in series[load_metric] if d <= end]
        workload.update(acwr(load_points, end))
        workload.update(monotony_strain(load_points, end))

    all_days = {d for points in series.values() for d, _ in points if d <= end}
    start = end - timedelta(days=window_days)

    return {
        "as_of": end.isoformat(),
        "window_days": window_days,
        "metrics": metrics,
        "workload": workload,
        "data_quality": {
            "days_with_any_data": len({d for d in all_days if start < d <= end}),
            "window_days": window_days,
            "metric_count": len(metrics),
            "history_span_days": (end - min(all_days)).days if all_days else 0,
        },
    }


def notable(bundle: Dict[str, Any], z_threshold: float = 1.5) -> List[Dict[str, str]]:
    """Highlights, as ``{"text", "severity", "code"}``.

    Structured rather than plain sentences: the UI colours by severity, and
    matching on substrings of a human sentence breaks the moment the wording
    changes (or the language does). Text is German because both consumers —
    the coach prompt and the dashboard — are.
    """
    out: List[Dict[str, str]] = []

    for metric, info in (bundle.get("metrics") or {}).items():
        z = info.get("z")
        if z is not None and abs(z) >= z_threshold:
            good = info.get("direction_good")
            if good == "unknown":
                severity, verdict = "warn", "ungewöhnlich"
            elif (z > 0) == (good == "higher"):
                severity, verdict = "good", "besser als sonst"
            else:
                severity, verdict = "bad", "schlechter als sonst"
            out.append({
                "code": "zscore",
                "severity": severity,
                "text": f"{metric}: {z:+.1f} SD von der 28-Tage-Baseline ({verdict})",
            })

        run = (info.get("trend_7d") or {}).get("run") or 0
        if abs(run) >= 4:
            heading = "steigt" if run > 0 else "fällt"
            out.append({
                "code": "run",
                "severity": "warn",
                "text": f"{metric} {heading} den {abs(run)}. Tag in Folge",
            })

        stale = info.get("stale_days") or 0
        if stale >= 3:
            out.append({
                "code": "stale",
                "severity": "neutral",
                "text": f"{metric}: seit {stale} Tagen kein neuer Wert",
            })

    wl = bundle.get("workload") or {}
    band, ratio = wl.get("band"), wl.get("ratio")
    if band == "high_risk":
        out.append({"code": "acwr", "severity": "bad",
                    "text": f"Acute:Chronic-Workload {ratio} — deutlich erhöhtes Überlastungsrisiko"})
    elif band == "elevated":
        out.append({"code": "acwr", "severity": "warn",
                    "text": f"Acute:Chronic-Workload {ratio} — Last steigt schneller als die Anpassung"})
    elif band == "undertraining":
        out.append({"code": "acwr", "severity": "neutral",
                    "text": f"Acute:Chronic-Workload {ratio} — Last ist deutlich abgefallen"})

    if (wl.get("monotony") or 0) >= 2.0:
        out.append({"code": "monotony", "severity": "warn",
                    "text": f"Trainingsmonotonie {wl.get('monotony')} — kaum Variation zwischen den Tagen"})

    quality = bundle.get("data_quality") or {}
    days, window = quality.get("days_with_any_data", 0), quality.get("window_days", 14)
    if window and days < max(3, window // 3):
        out.append({"code": "coverage", "severity": "neutral",
                    "text": f"Dünne Datenlage: nur {days} von {window} Tagen mit Messwerten"})

    return out
