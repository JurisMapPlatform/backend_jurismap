from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.routers import auth, analysis, mindmap, document, export
from app.services.ws import ws_manager

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="JurisMind API",
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
    return JSONResponse(status_code=429, content={"detail": "Demasiadas solicitudes. Intenta de nuevo más tarde."})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id)


@app.get("/health", tags=["Health Check"],
         summary="Service health check",
         description="Returns the current status of the API. Used by Cloud Run for liveness probes.")
async def health():
    return {"status": "ok", "service": "JurisMind API", "version": "1.0.0"}
