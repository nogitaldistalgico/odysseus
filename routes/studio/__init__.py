"""
Studio Module - Photo and Video generation capabilities.
"""

from fastapi import FastAPI
from routes.studio.studio_routes import router as studio_router

def setup_studio_routes(app: FastAPI):
    """Register all studio routes."""
    app.include_router(studio_router)
