"""Fitness coach API.

Data flow: the client (iOS / Apple Watch) posts metrics to /dashboard, which
merges them into today's entry and appends to a per-day history. The coach
then reads that history — but not raw. `services.fitness.analytics` turns it
into indicators with a personal frame of reference (z-scores against the
user's own trailing baseline, acute:chronic workload, direction runs, data
coverage), and those are what the model sees. Asking a model to invent a score
from fourteen days of prose produced numbers that were unanchored and not
reproducible; interpreting named indicators is a job it can actually do.
"""

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.atomic_io import atomic_write_json, atomic_write_text
from core.constants import DATA_DIR
from src.auth_helpers import effective_user
from services.fitness.analytics import analyze, notable

# A note about "yesterday was a heavy move" should not still be shaping the
# score eight months later. temporaere_notizen.md was append-only forever.
NOTE_TTL_DAYS = int(os.getenv("ODYSSEUS_FITNESS_NOTE_TTL_DAYS", "21"))
HISTORY_KEEP_DAYS = int(os.getenv("ODYSSEUS_FITNESS_HISTORY_DAYS", "400"))
LOG_WINDOW_DAYS = 14
MAX_FILE_BYTES = 256 * 1024          # these files land in every coach prompt
MAX_PAYLOAD_BYTES = 128 * 1024
MAX_SAMPLES_PER_DAY = 48

ALLOWED_FITNESS_FILES = {
    "ziele.md", "wochenplan.md", "trainingsplan.md",
    "temporaere_notizen.md", "messwerte_log.md", "analyse.md",
}


def active_notes(notes: List[Dict[str, str]], today: date,
                 ttl_days: int = NOTE_TTL_DAYS) -> List[Dict[str, str]]:
    """Notes still inside their time-to-live, oldest first.

    Without an expiry a note about one bad night keeps colouring the score
    months later, because the file was only ever appended to.
    """
    cutoff = today - timedelta(days=ttl_days)
    live = []
    for n in notes:
        try:
            when = datetime.strptime(n["date"], "%Y-%m-%d").date()
        except Exception:
            continue
        if when >= cutoff:
            live.append(n)
    return sorted(live, key=lambda n: n["date"])


def coerce_score(value: Any) -> Optional[int]:
    """Accept 85, "85", "85%", 85.4, " 85 " — reject anything else.

    int(...) straight on the model's value raised on every one of those but the
    first, turning a slightly chatty model into a 500, and let an out-of-range
    number through unchecked.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        m = re.search(r"-?\d+(?:[.,]\d+)?", value)
        if not m:
            return None
        num = float(m.group(0).replace(",", "."))
    else:
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return max(0, min(100, int(round(num))))


def setup_fitness_routes() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    #  Workspace
    # ------------------------------------------------------------------

    def _workspace(request: Request) -> str:
        user = effective_user(request) or "default"
        # Usernames are normalised to lowercase on creation and never contain a
        # separator, but this path is built from a request-derived value, so
        # constrain it rather than trusting that invariant from a distance.
        safe = os.path.basename(str(user).strip()) or "default"
        if safe in (".", ".."):
            safe = "default"
        path = os.path.join(DATA_DIR, "users", safe, "fitness_data")
        os.makedirs(path, exist_ok=True)
        return path

    def _p(request: Request, name: str) -> str:
        return os.path.join(_workspace(request), name)

    def _read_json(path: str, default: Any) -> Any:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _read_text(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""

    def default_metrics() -> Dict[str, Any]:
        waiting = "Waiting for Apple Watch Data"
        return {
            "recovery": {"score": "-", "trend": "neutral", "text": waiting},
            "condition": {"score": "-", "trend": "neutral", "text": waiting},
            "movement": {"current": 0, "goal": 0, "unit": "kcal", "text": waiting},
        }

    def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
        defaults = default_metrics()
        for key, sub in defaults.items():
            if key not in data or not isinstance(data[key], dict):
                data[key] = sub
            else:
                for subkey, val in sub.items():
                    data[key].setdefault(subkey, val)
        return data

    # ------------------------------------------------------------------
    #  Notes (with an expiry, and stored structured)
    # ------------------------------------------------------------------

    def _load_notes(request: Request) -> List[Dict[str, str]]:
        notes = _read_json(_p(request, "notes.json"), None)
        if isinstance(notes, list):
            return [n for n in notes if isinstance(n, dict) and n.get("text")]
        # One-time import of the legacy append-only markdown file.
        legacy = _read_text(_p(request, "temporaere_notizen.md"))
        out: List[Dict[str, str]] = []
        for line in legacy.splitlines():
            m = re.match(r"^\s*-\s*(\d{4}-\d{2}-\d{2})\s*:\s*(.+)$", line)
            if m:
                out.append({"date": m.group(1), "text": m.group(2).strip()})
        return out

    def _save_notes(request: Request, notes: List[Dict[str, str]], today: date) -> List[Dict[str, str]]:
        live = active_notes(notes, today)
        atomic_write_json(_p(request, "notes.json"), live, indent=2)
        # Keep the markdown mirror so the coach's file tools still find it.
        body = f"# Temporäre Notizen (letzte {NOTE_TTL_DAYS} Tage)\n\n"
        body += "\n".join(f"- {n['date']}: {n['text']}" for n in live) or "_(keine)_"
        atomic_write_text(_p(request, "temporaere_notizen.md"), body + "\n")
        return live

    # ------------------------------------------------------------------
    #  History
    # ------------------------------------------------------------------

    def _write_history(request: Request, history: Dict[str, Any]) -> None:
        if len(history) > HISTORY_KEEP_DAYS:
            for stale in sorted(history)[:-HISTORY_KEEP_DAYS]:
                history.pop(stale, None)
        atomic_write_json(_p(request, "fitness_history.json"), history, indent=2)

        recent = sorted(history)[-LOG_WINDOW_DAYS:]
        log = f"# Fitness Historie (Letzte {LOG_WINDOW_DAYS} Tage)\n\n"
        for day in recent:
            entry = {k: v for k, v in history[day].items() if k != "_samples"}
            log += f"### Messwerte vom {day}\n```json\n{json.dumps(entry, indent=2, ensure_ascii=False)}\n```\n\n"
        atomic_write_text(_p(request, "messwerte_log.md"), log)

    # ------------------------------------------------------------------
    #  Dashboard
    # ------------------------------------------------------------------

    @router.get("/api/fitness_coach/dashboard")
    async def get_dashboard(request: Request):
        data = _read_json(_p(request, "fitness_metrics.json"), None)
        if not isinstance(data, dict):
            return JSONResponse(content=default_metrics())
        return JSONResponse(content=_merge_defaults(data))

    @router.post("/api/fitness_coach/dashboard")
    async def update_dashboard(request: Request):
        raw = await request.body()
        if len(raw) > MAX_PAYLOAD_BYTES:
            raise HTTPException(413, f"Payload exceeds {MAX_PAYLOAD_BYTES} bytes")
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            raise HTTPException(400, "Invalid JSON")
        if not isinstance(payload, dict):
            raise HTTPException(400, "Payload must be a JSON object")

        data = _read_json(_p(request, "fitness_metrics.json"), None)
        if not isinstance(data, dict):
            data = default_metrics()

        for key, val in payload.items():
            if isinstance(val, dict) and isinstance(data.get(key), dict):
                data[key].update(val)
            else:
                data[key] = val
        atomic_write_json(_p(request, "fitness_metrics.json"), data, indent=2)

        if payload:
            today = datetime.now().strftime("%Y-%m-%d")
            history = _read_json(_p(request, "fitness_history.json"), {})
            if not isinstance(history, dict):
                history = {}
            day = history.setdefault(today, {})

            for key, val in payload.items():
                if isinstance(val, dict) and isinstance(day.get(key), dict):
                    day[key].update(val)
                else:
                    day[key] = val

            # The merged daily entry is what the analytics read, and for
            # accumulating metrics (steps, energy) last-write-wins is correct.
            # For point-in-time readings it is not, so keep the raw submissions
            # too rather than discarding them silently.
            samples = day.setdefault("_samples", [])
            if isinstance(samples, list):
                samples.append({"at": datetime.now().isoformat(timespec="seconds"),
                                "data": payload})
                del samples[:-MAX_SAMPLES_PER_DAY]

            _write_history(request, history)

        return JSONResponse(content={"status": "success", "data": data})

    # ------------------------------------------------------------------
    #  Analysis
    # ------------------------------------------------------------------

    def _bundle(request: Request) -> Tuple[Dict[str, Any], List[str]]:
        history = _read_json(_p(request, "fitness_history.json"), {})
        if not isinstance(history, dict):
            history = {}
        clean = {d: {k: v for k, v in e.items() if k != "_samples"}
                 for d, e in history.items() if isinstance(e, dict)}
        bundle = analyze(clean, today=date.today())
        return bundle, notable(bundle)

    @router.get("/api/fitness_coach/analysis")
    async def get_analysis(request: Request):
        """The indicator bundle: baselines, z-scores, workload, coverage.

        Deterministic — no model involved. The client can render it directly
        and it is also what /recalculate feeds to the coach.
        """
        bundle, highlights = _bundle(request)
        return JSONResponse(content={**bundle, "notable": highlights})

    @router.get("/api/fitness_coach/history")
    async def get_history(request: Request, days: int = 90, metric: Optional[str] = None):
        """Per-day series for charting. The history existed but nothing read it."""
        days = max(1, min(int(days), HISTORY_KEEP_DAYS))
        history = _read_json(_p(request, "fitness_history.json"), {})
        if not isinstance(history, dict):
            history = {}
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()

        from services.fitness.analytics import flatten_numeric
        series: Dict[str, List[Dict[str, Any]]] = {}
        for day in sorted(history):
            if day < cutoff or not isinstance(history[day], dict):
                continue
            entry = {k: v for k, v in history[day].items() if k != "_samples"}
            for path, value in flatten_numeric(entry).items():
                if metric and path != metric:
                    continue
                series.setdefault(path, []).append({"date": day, "value": value})
        return JSONResponse(content={"days": days, "series": series})

    # ------------------------------------------------------------------
    #  Notes
    # ------------------------------------------------------------------

    class NotePayload(BaseModel):
        note: str = Field(min_length=1, max_length=2000)

    @router.post("/api/fitness_coach/note")
    async def add_note(request: Request, payload: NotePayload):
        today = date.today()
        notes = _load_notes(request)
        notes.append({"date": today.isoformat(), "text": payload.note.strip()})
        live = _save_notes(request, notes, today)
        return JSONResponse(content={"status": "success", "active_notes": len(live)})

    @router.get("/api/fitness_coach/notes")
    async def list_notes(request: Request):
        today = date.today()
        live = _save_notes(request, _load_notes(request), today)
        return JSONResponse(content={"ttl_days": NOTE_TTL_DAYS, "notes": live})

    # ------------------------------------------------------------------
    #  Plan files
    # ------------------------------------------------------------------

    class FilePayload(BaseModel):
        content: str

    @router.get("/api/fitness_coach/files/{filename}")
    async def get_file(request: Request, filename: str):
        if filename not in ALLOWED_FITNESS_FILES:
            raise HTTPException(400, "Invalid filename")
        return JSONResponse(content={"content": _read_text(_p(request, filename))})

    @router.post("/api/fitness_coach/files/{filename}")
    async def save_file(request: Request, filename: str, payload: FilePayload):
        if filename not in ALLOWED_FITNESS_FILES:
            raise HTTPException(400, "Invalid filename")
        # These files are injected into every coach prompt, so an unbounded
        # write here is an unbounded prompt later.
        if len(payload.content.encode("utf-8")) > MAX_FILE_BYTES:
            raise HTTPException(413, f"File exceeds {MAX_FILE_BYTES} bytes")
        atomic_write_text(_p(request, filename), payload.content)
        return JSONResponse(content={"status": "success"})

    # ------------------------------------------------------------------
    #  Score recalculation
    # ------------------------------------------------------------------

    class RecalculateRequest(BaseModel):
        model: Optional[str] = None
        endpoint_id: Optional[str] = None
        session_id: Optional[str] = None

    def _resolve_llm(request: Request, payload: Optional["RecalculateRequest"], user: str):
        """First configured endpoint, most specific first. Unchanged behaviour."""
        from core.database import ModelEndpoint, SessionLocal
        from src.endpoint_resolver import (
            _endpoint_enabled_models, _first_chat_model, build_chat_url, build_headers,
            resolve_endpoint, resolve_endpoint_by_id, resolve_endpoint_runtime,
        )

        if payload and payload.endpoint_id:
            res = resolve_endpoint_by_id(payload.endpoint_id, model=payload.model, owner=user)
            if res:
                return res

        sid = (payload.session_id if payload else None) or request.query_params.get("session_id")
        if sid:
            sm = getattr(request.app.state, "session_manager", None)
            if sm:
                try:
                    s = sm.get_session(sid)
                    if s and getattr(s, "endpoint_url", None) and getattr(s, "model", None):
                        return s.endpoint_url, s.model, getattr(s, "auth_headers", {}) or {}
                except Exception:
                    pass

        for role, owner in (("utility", user), ("default", user), ("default", None)):
            u, m, h = resolve_endpoint(role, owner=owner)
            if u and m:
                return u, m, h

        db = SessionLocal()
        try:
            q = db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True)  # noqa: E712
            if user:
                from src.auth_helpers import owner_filter
                q = owner_filter(q, ModelEndpoint, user)
            ep = q.first()
            if ep:
                base, api_key = resolve_endpoint_runtime(ep, owner=user)
                model = (payload.model if payload else None) or _first_chat_model(
                    _endpoint_enabled_models(ep)
                )
                return build_chat_url(base), model, build_headers(api_key, base)
        except Exception:
            pass
        finally:
            db.close()
        return None, None, None

    @router.post("/api/fitness_coach/recalculate")
    async def recalculate(request: Request, payload: Optional[RecalculateRequest] = None):
        user = effective_user(request)
        if not user:
            raise HTTPException(401, "Not authenticated")

        from src.llm_core import llm_call_async

        bundle, highlights = _bundle(request)
        today = date.today()
        notes = active_notes(_load_notes(request), today)
        goals = _read_text(_p(request, "ziele.md"))[:4000]

        quality = bundle.get("data_quality", {})
        if quality.get("days_with_any_data", 0) == 0:
            raise HTTPException(400, "No metrics recorded yet — nothing to score.")

        prompt = f"""Du bewertest den heutigen Condition-Score (0-100) einer Person.

Die Kennzahlen sind bereits ausgewertet. Interpretiere sie, rechne nichts neu.

Wie die Werte zu lesen sind:
- "z" ist die Abweichung von der persönlichen 28-Tage-Baseline in Standardabweichungen.
  |z| < 1 ist normal, |z| >= 1.5 ist auffällig. "direction_good" sagt, welche Richtung gut ist.
- "trend_7d.run" ist die Anzahl aufeinanderfolgender Tage in dieselbe Richtung.
- "workload.ratio" ist das Verhältnis 7-Tage-Last zu 28-Tage-Last (Acute:Chronic).
  0.8-1.3 optimal, >1.5 deutlich erhöhtes Überlastungsrisiko.
- "monotony" >= 2.0 heisst sehr gleichförmiges Training.
- "data_quality" sagt, auf wie vielen Tagen das beruht. Bei dünner Datenlage
  bewerte vorsichtig und sage es im Text.

INDIKATOREN:
{json.dumps(bundle, indent=2, ensure_ascii=False)}

AUFFÄLLIGKEITEN:
{chr(10).join('- [' + h['severity'] + '] ' + h['text'] for h in highlights) if highlights else '- keine'}

SUBJEKTIVE NOTIZEN (letzte {NOTE_TTL_DAYS} Tage):
{chr(10).join('- ' + n['date'] + ': ' + n['text'] for n in notes) if notes else '- keine'}

ZIELE:
{goals or '- nicht hinterlegt'}

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{{"score": 85, "text": "Kurzlabel, max 3 Woerter", "tooltip": "1-2 Saetze Begruendung, nenne die konkreten Kennzahlen", "recommendation": "Konkrete Trainingsempfehlung fuer heute", "confidence": "high|medium|low"}}
Kein anderer Text."""

        url, model, headers = _resolve_llm(request, payload, user)
        if not url or not model:
            raise HTTPException(503, "Kein aktiver KI-Endpunkt konfiguriert")

        try:
            response_text = await llm_call_async(
                url, model, messages=[{"role": "user", "content": prompt}], headers=headers
            )
        except Exception as e:
            raise HTTPException(502, f"Modellaufruf fehlgeschlagen: {e}")

        match = re.search(r"\{.*\}", response_text or "", re.DOTALL)
        if not match:
            raise HTTPException(502, "Das Modell hat kein JSON zurückgegeben.")
        try:
            parsed = json.loads(match.group(0))
        except Exception as e:
            raise HTTPException(502, f"Antwort war kein gültiges JSON: {e}")

        score = coerce_score(parsed.get("score"))
        if score is None:
            raise HTTPException(502, f"Kein verwertbarer Score in der Antwort: {parsed.get('score')!r}")

        confidence = str(parsed.get("confidence", "")).lower()
        if confidence not in ("high", "medium", "low"):
            # Thin data cannot support a confident score regardless of what the
            # model claims.
            confidence = "low" if quality.get("days_with_any_data", 0) < 5 else "medium"

        condition = {
            "score": score,
            "text": str(parsed.get("text", "Normal"))[:40],
            "tooltip": str(parsed.get("tooltip", ""))[:500],
            "recommendation": str(parsed.get("recommendation", ""))[:500],
            "confidence": confidence,
            "computed_at": datetime.now().isoformat(timespec="seconds"),
            "based_on_days": quality.get("days_with_any_data", 0),
        }

        metrics = _read_json(_p(request, "fitness_metrics.json"), None)
        if not isinstance(metrics, dict):
            metrics = default_metrics()

        # Direction of travel against the previous score — the dashboard has
        # always had a `trend` field and nothing ever filled it.
        history = _read_json(_p(request, "fitness_history.json"), {})
        if not isinstance(history, dict):
            history = {}
        previous = [history[d].get("condition", {}).get("score")
                    for d in sorted(history)
                    if isinstance(history[d], dict) and isinstance(history[d].get("condition"), dict)]
        previous = [p for p in previous if isinstance(p, (int, float))]
        if previous:
            delta = score - previous[-1]
            condition["trend"] = "up" if delta >= 3 else "down" if delta <= -3 else "neutral"
            condition["delta"] = delta
        else:
            condition["trend"] = "neutral"

        metrics["condition"] = condition
        atomic_write_json(_p(request, "fitness_metrics.json"), metrics, indent=2)

        # The computed score was never written back to the history, so no score
        # trend could ever exist.
        day = history.setdefault(today.isoformat(), {})
        day["condition"] = {k: condition[k] for k in ("score", "text", "confidence")}
        _write_history(request, history)

        return JSONResponse(content={
            "status": "success",
            "metrics": _merge_defaults(metrics),
            "notable": highlights,
            "data_quality": quality,
        })

    return router
