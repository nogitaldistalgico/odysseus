import asyncio
from fastapi.testclient import TestClient
from main import app
from src.auth_helpers import AuthManager

client = TestClient(app)

# Bypass auth for testing
app.dependency_overrides[AuthManager.get_current_user] = lambda: {"id": "test_user"}
app.dependency_overrides[AuthManager.get_current_user_optional] = lambda: {"id": "test_user"}

# Note: require_studio_privilege reads from request.state.user
def set_user_state(request):
    request.state.user = {"id": "test_user", "role": "admin"}
    return request

response = client.get("/api/studio/characters", headers={"Authorization": "Bearer test"})
print(response.status_code)
print(response.json())
