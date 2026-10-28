from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import auth, classroom, health, library, outlines, providers
from app.core.config import settings
from app.core.docs import swagger_ui_html
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import engine

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    configure_logging(settings.log_level)
    openapi_url = f"{settings.api_v1_prefix}/openapi.json"
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="TeachAIAgent course generation and knowledge API",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=openapi_url,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-CSRF-Token",
            "X-Request-ID",
        ],
        expose_headers=["X-Request-ID", "X-Process-Time-Ms"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware, settings=settings)

    install_error_handlers(app)

    @app.get("/docs", include_in_schema=False)
    async def offline_swagger_ui() -> HTMLResponse:
        return swagger_ui_html(
            openapi_url=openapi_url,
            title=f"{settings.app_name} - API docs",
        )

    if settings.app_env != "production":

        @app.get(
            "/.well-known/appspecific/com.chrome.devtools.json",
            include_in_schema=False,
        )
        async def chrome_devtools_workspace_probe() -> Response:
            return Response(status_code=204, headers={"Cache-Control": "no-store"})

    app.include_router(health.router, prefix=settings.api_v1_prefix)
    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(providers.router, prefix=settings.api_v1_prefix)
    app.include_router(library.folders_router, prefix=settings.api_v1_prefix)
    app.include_router(library.courses_router, prefix=settings.api_v1_prefix)
    app.include_router(outlines.router, prefix=settings.api_v1_prefix)
    app.include_router(classroom.router, prefix=settings.api_v1_prefix)
    app.include_router(classroom.jobs_router, prefix=settings.api_v1_prefix)
    app.include_router(library.assets_router, prefix=settings.api_v1_prefix)
    app.include_router(library.search_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
