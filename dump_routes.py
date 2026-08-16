import sys
from app import app

found = False
for route in app.routes:
    if hasattr(route, "path"):
        if "/api/studio/library" in route.path:
            print(f"FOUND: {route.path} {route.methods}")
            found = True

if not found:
    print("NOT FOUND!")
