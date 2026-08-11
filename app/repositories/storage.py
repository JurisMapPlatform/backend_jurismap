import asyncio
import uuid
import os
from pathlib import Path

from app.config import settings


class StorageRepository:
    def __init__(self):
        self._local = False
        try:
            from google.cloud import storage
            self.client = storage.Client()
            self.bucket = self.client.bucket(settings.gcs_bucket_name)
        except Exception:
            self._local = True
            self._local_dir = Path(settings.local_storage_path)
            self._local_dir.mkdir(parents=True, exist_ok=True)

    # Los métodos del SDK de GCS son síncronos y bloquean el event loop; se ejecutan
    # en un hilo aparte (asyncio.to_thread) para mantener el servidor atendiendo a otros usuarios.
    async def upload(self, file_bytes: bytes, filename: str, user_id: uuid.UUID) -> str:
        path = f"documents/{user_id}/{uuid.uuid4()}_{filename}"
        await asyncio.to_thread(self._upload_sync, file_bytes, path)
        return path

    def _upload_sync(self, file_bytes: bytes, path: str) -> None:
        if self._local:
            full = self._local_dir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(file_bytes)
        else:
            blob = self.bucket.blob(path)
            blob.upload_from_string(file_bytes, content_type="application/pdf")

    async def download(self, path: str) -> bytes:
        return await asyncio.to_thread(self._download_sync, path)

    def _download_sync(self, path: str) -> bytes:
        if self._local:
            return (self._local_dir / path).read_bytes()
        blob = self.bucket.blob(path)
        return blob.download_as_bytes()

    async def delete(self, path: str) -> None:
        await asyncio.to_thread(self._delete_sync, path)

    def _delete_sync(self, path: str) -> None:
        if self._local:
            full = self._local_dir / path
            if full.exists():
                full.unlink()
            return
        blob = self.bucket.blob(path)
        if blob.exists():
            blob.delete()
