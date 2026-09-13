import asyncio

from fastapi import WebSocket, status
from jose import jwt, JWTError

from app.config import settings

AUTH_TIMEOUT_SECONDS = 10


class WSManager:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}

    async def authenticate(self, websocket: WebSocket) -> str | None:
        """Acepta la conexión y la autentica con el JWT.

        El frontend envía el token en el PRIMER MENSAJE ({"type": "auth", "token": ...}) y no en la
        URL: la URL queda registrada en los logs de Cloud Run y cualquiera con acceso a ellos podría
        usar la sesión del estudiante mientras el token siga vigente. Se sigue aceptando `?token=`
        por compatibilidad con clientes antiguos."""
        await websocket.accept()
        token = websocket.query_params.get("token")
        if not token:
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT_SECONDS)
                if isinstance(msg, dict) and msg.get("type") == "auth":
                    token = msg.get("token")
            except Exception:
                token = None
        user_id = None
        if token:
            try:
                user_id = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm]).get("sub")
            except JWTError:
                user_id = None
        if not user_id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None
        return user_id

    async def connect(self, user_id: str, websocket: WebSocket):
        # La conexión ya fue aceptada en authenticate().
        self.connections[user_id] = websocket

    def disconnect(self, user_id: str):
        self.connections.pop(user_id, None)

    async def send_progress(self, user_id: str, data: dict):
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(user_id)


ws_manager = WSManager()
