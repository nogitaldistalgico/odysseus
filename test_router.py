from app import app
from fastapi.testclient import TestClient

client = TestClient(app)
response = client.get("/api/studio/library")
print(f"Status: {response.status_code}")
print(f"Body: {response.text}")
