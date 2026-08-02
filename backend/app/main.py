from fastapi import FastAPI

from app.api import chat, health, settings as settings_api
from app.config import settings
from app.di import build_deps


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.deps = build_deps()
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(settings_api.router)
    return app


app = create_app()
