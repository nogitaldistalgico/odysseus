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
            from src.auth_helpers import _is_api_token_request
            
            prompt_text = "Bitte lies meine neusten Vitalwerte aus dem Log und meine temporären Notizen, berechne meinen heutigen Condition-Score (0-100) und schreibe den neuen Score in den condition-Block von fitness_metrics.json. Schreibe in das Feld 'text' des condition-Blocks ein kurzes Label (max 2 Wörter, z.B. 'Gut', 'Eingeschränkt'). Schreibe ZUSÄTZLICH eine kurze Erklärung (max 1-2 Sätze inkl. kleinem Tipp) in das Feld 'tooltip' innerhalb des condition-Blocks, warum du diesen Wert gewählt hast. (WICHTIG: Antworte SOFORT mit dem Tool Call und gib keinerlei Erklärungen oder Gedanken vorher aus, um Token zu sparen. Du musst keine Romane schreiben, komme direkt zum Ergebnis.)"
            
            headers = {}
            cookies = {}
            if _is_api_token_request(request):
                auth = request.headers.get("Authorization")
                if auth:
                    headers["Authorization"] = auth
            else:
                cookies = request.cookies
                
            try:
                transport = httpx.ASGITransport(app=request.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    data = {
                        "message": prompt_text,
                        "incognito": True,
                        "mode": "agent",
                        "is_subchat": True,
                        "is_fitness_coach": "true"
                    }
                    await client.post("/api/chat", json=data, headers=headers, cookies=cookies, timeout=60.0)
            except Exception as e:
                print(f"Error in background fitness recalculation: {e}")
                
        background_tasks.add_task(_run_ai_recalculation)
        return JSONResponse(content={"status": "calculating"})

    return router
