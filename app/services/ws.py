import uuid

from fastapi import WebSocket, WebSocketDisconnect, status
from jose import jwt, JWTError

from app.config import settings


class WSManager:
    def __init__(self):
        self.connections: dict[str, WebSocket] = {}

    async def authenticate(self, websocket: WebSocket) -> str | None:
        token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
            user_id = payload.get("sub")
            if not user_id:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return None
            return user_id
        except JWTError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
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
