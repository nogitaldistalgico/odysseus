import os
import json
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.auth_helpers import effective_user
from core.constants import DATA_DIR

def setup_fitness_routes() -> APIRouter:
    router = APIRouter()

    def get_fitness_metrics_path(request: Request) -> str:
        user = effective_user(request) or "default"
        workspace = os.path.join(DATA_DIR, "users", user, "fitness_data")
        os.makedirs(workspace, exist_ok=True)
        return os.path.join(workspace, "fitness_metrics.json")

    def get_default_metrics():
        return {
            "recovery": {
                "score": "-",
                "trend": "neutral",
                "text": "Waiting for Apple Watch Data"
            },
            "condition": {
                "score": "-",
                "trend": "neutral",
                "text": "Waiting for Apple Watch Data"
            },
            "movement": {
                "current": 0,
                "goal": 0,
                "unit": "kcal",
                "text": "Waiting for Apple Watch Data"
            }
        }

    @router.get("/api/fitness_coach/dashboard")
    async def get_dashboard(request: Request):
        path = get_fitness_metrics_path(request)
        if not os.path.exists(path):
            return JSONResponse(content=get_default_metrics())
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults to prevent JS crashes
            defaults = get_default_metrics()
            for key in defaults:
                if key not in data or not isinstance(data[key], dict):
                    data[key] = defaults[key]
                else:
                    for subkey in defaults[key]:
                        if subkey not in data[key]:
                            data[key][subkey] = defaults[key][subkey]
            return JSONResponse(content=data)
        except Exception:
            return JSONResponse(content=get_default_metrics())

    @router.post("/api/fitness_coach/dashboard")
    async def update_dashboard(request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON")
            
        path = get_fitness_metrics_path(request)
        workspace = os.path.dirname(path)
        
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = get_default_metrics()
        else:
            data = get_default_metrics()

        for key, val in payload.items():
            if isinstance(val, dict) and key in data and isinstance(data[key], dict):
                data[key].update(val)
            else:
                data[key] = val

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                
            if payload:
                from datetime import datetime
                now_date = datetime.now().strftime("%Y-%m-%d")
                history_path = os.path.join(workspace, "fitness_history.json")
                
                history = {}
                if os.path.exists(history_path):
                    try:
                        with open(history_path, "r", encoding="utf-8") as f:
                            history = json.load(f)
                    except Exception:
                        history = {}
                        
                if now_date not in history:
                    history[now_date] = {}
                    
                for k, v in payload.items():
                    if isinstance(v, dict) and k in history[now_date] and isinstance(history[now_date][k], dict):
                        history[now_date][k].update(v)
                    else:
                        history[now_date][k] = v
                        
                with open(history_path, "w", encoding="utf-8") as f:
                    json.dump(history, f, indent=2)
                    
                log_path = os.path.join(workspace, "messwerte_log.md")
                sorted_dates = sorted(history.keys())[-14:]
                
                log_content = "# Fitness Historie (Letzte 14 Tage)\n\n"
                for date_key in sorted_dates:
                    log_content += f"### Messwerte vom {date_key}\n```json\n{json.dumps(history[date_key], indent=2)}\n```\n\n"
                    
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(log_content)
                    
            return JSONResponse(content={"status": "success", "data": data})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    class NotePayload(BaseModel):
        note: str

    @router.post("/api/fitness_coach/note")
    async def add_temporary_note(request: Request, payload: NotePayload):
        path = get_fitness_metrics_path(request)
        workspace = os.path.dirname(path)
        notes_path = os.path.join(workspace, "temporaere_notizen.md")
        
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d")
        
        try:
            with open(notes_path, "a", encoding="utf-8") as f:
                f.write(f"- {now_str}: {payload.note}\n")
            return JSONResponse(content={"status": "success"})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    class FilePayload(BaseModel):
        content: str

    ALLOWED_FITNESS_FILES = {"ziele.md", "wochenplan.md", "trainingsplan.md", "temporaere_notizen.md", "messwerte_log.md"}

    @router.get("/api/fitness_coach/files/{filename}")
    async def get_fitness_file(request: Request, filename: str):
        if filename not in ALLOWED_FITNESS_FILES:
            raise HTTPException(status_code=400, detail="Invalid filename")
            
        path = get_fitness_metrics_path(request)
        workspace = os.path.dirname(path)
        file_path = os.path.join(workspace, filename)
        
        if not os.path.exists(file_path):
            return JSONResponse(content={"content": ""})
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            return JSONResponse(content={"content": content})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/fitness_coach/files/{filename}")
    async def save_fitness_file(request: Request, filename: str, payload: FilePayload):
        if filename not in ALLOWED_FITNESS_FILES:
            raise HTTPException(status_code=400, detail="Invalid filename")
            
        path = get_fitness_metrics_path(request)
        workspace = os.path.dirname(path)
        file_path = os.path.join(workspace, filename)
        
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(payload.content)
            return JSONResponse(content={"status": "success"})
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/api/fitness_coach/recalculate")
    async def trigger_recalculation(request: Request, background_tasks: BackgroundTasks):
        user = effective_user(request)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
            
        async def _run_ai_recalculation():
            import httpx
            from src.endpoint_resolver import resolve_endpoint
            from src.llm_core import llm_call_async
            import re
            
            workspace = os.path.dirname(get_fitness_metrics_path(request))
            def _read_file_safe(filename):
                try:
                    with open(os.path.join(workspace, filename), "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    return ""
                    
            vital_log = _read_file_safe("messwerte_log.md")
            notes = _read_file_safe("temporaere_notizen.md")
            
            prompt_text = f"""Bitte berechne meinen heutigen Condition-Score (0-100) basierend auf meinen Daten.
            
Vitalwerte Log:
{vital_log}

Notizen:
{notes}

Antworte AUSSCHLIESSLICH mit einem validen JSON-Objekt in folgendem Format:
{{"score": 85, "text": "Gut", "tooltip": "Gute Erholung, heute ist ein Training möglich."}}
Gib keinen anderen Text, Markdown oder Erklärungen aus."""
            
            url, model, headers = resolve_endpoint("default", owner=user)
            if not url or not model:
                print("Error: No default endpoint configured for fitness coach")
                return
                
            try:
                response_text = await llm_call_async(url, model, messages=[{"role": "user", "content": prompt_text}], headers=headers)
                
                # Parse JSON safely
                json_str = response_text.strip()
                if "```json" in json_str:
                    json_str = json_str.split("```json")[1].split("```")[0].strip()
                elif "```" in json_str:
                    json_str = json_str.split("```")[1].split("```")[0].strip()
                
                score_data = json.loads(json_str)
                
                metrics_path = get_fitness_metrics_path(request)
                current_data = get_default_metrics()
                if os.path.exists(metrics_path):
                    try:
                        with open(metrics_path, "r", encoding="utf-8") as f:
                            current_data.update(json.load(f))
                    except Exception:
                        pass
                
                current_data["condition"] = {
                    "score": score_data.get("score", 0),
                    "text": score_data.get("text", "?"),
                    "tooltip": score_data.get("tooltip", "")
                }
                
                with open(metrics_path, "w", encoding="utf-8") as f:
                    json.dump(current_data, f, indent=4)
                    
            except Exception as e:
                print(f"Error in background fitness recalculation: {e}")
                
        background_tasks.add_task(_run_ai_recalculation)
        return JSONResponse(content={"status": "calculating"})

    return router
