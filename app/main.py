import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.security import client_ip
from app.routers import auth, analysis, mindmap, document, export
from app.services.ws import ws_manager
from app.services.analysis import recover_stale_analyses, fail_running_analyses

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=client_ip)

SWEEP_SECONDS = 120


async def _sweep_stale_analyses():
    # Al arrancar y cada 2 minutos: falla los análisis que otra instancia dejó a medias.
    while True:
        try:
            await recover_stale_analyses(settings.analysis_stale_minutes)
        except Exception:
            logger.exception("No se pudo revisar los análisis interrumpidos")
        await asyncio.sleep(SWEEP_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sweeper = asyncio.create_task(_sweep_stale_analyses())
    yield
    sweeper.cancel()
    # Cloud Run avisa antes de apagar la instancia: los análisis que corrían aquí no terminarán.
    try:
        await fail_running_analyses()
    except Exception:
        logger.exception("No se pudo marcar los análisis en curso al apagar")


app = FastAPI(
    lifespan=lifespan,
    title="JurisMap API",
    description="Plataforma que combina **Deep Learning (BETO)** e **IA Generativa (Gemini 2.5 Flash)** "
                "para analizar sentencias del Tribunal Constitucional del Perú y generar "
                "**mapas mentales interactivos** orientados a estudiantes de derecho.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {
            "name": "Authentication",
            "description": "User registration, login (email + Google OAuth), email verification, and password recovery.",
        },
        {
            "name": "Documents",
            "description": "Upload, validate, and manage PDF files of TC rulings. Files are stored in Google Cloud Storage.",
        },
        {
            "name": "Analyses",
            "description": "Create, list, and manage jurisprudential analyses. Each analysis processes 1-5 documents through the 5-step AI pipeline.",
        },
        {
            "name": "Mind Map",
            "description": "Interactive mind map editing: generate AI nodes, rename, delete sub-trees, auto-save, and AI-powered reorganization.",
        },
        {
            "name": "Export",
            "description": "Download mind maps in JSON, PNG image, or PDF format.",
        },
    ],
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Demasiados intentos seguidos. Espera un minuto e inténtalo de nuevo."})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    # Solo el frontend de JurisMap (producción y sus vistas previas), no cualquier app de Vercel.
    allow_origin_regex=r"https://frontend-jurismap(-[a-z0-9-]+)?\.vercel\.app",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(document.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")
app.include_router(mindmap.router, prefix="/api/v1")
app.include_router(export.router, prefix="/api/v1")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    user_id = await ws_manager.authenticate(websocket)
    if not user_id:
        return
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # Heartbeat: si el cliente no envía nada en 20s, mandamos un "ping" para mantener
            # viva la conexión durante los pasos largos del análisis (Gemini). Sin esto, un
            # idle timeout del proxy la cierra y se pierden los eventos finales (p. ej. completed).
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=20)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id)
    except Exception:
        ws_manager.disconnect(user_id)


@app.get("/health", tags=["Health Check"],
         summary="Service health check",
         description="Returns the current status of the API. Used by Cloud Run for liveness probes.")
async def health():
    return {"status": "ok", "service": "JurisMap API", "version": "1.0.0"}
