import logging
import os
import shutil
from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

from app.config import settings

logger = logging.getLogger(__name__)

LABELS = {0: "NO_RELEVANTE", 1: "RELEVANTE"}

# Cache local para el modelo descargado desde GCS (writable en Cloud Run: solo /tmp)
_GCS_CACHE_DIR = Path("/tmp/beto_model")
# Archivos mínimos que deben existir para considerar la descarga completa
_REQUIRED_FILES = ("config.json", "tokenizer.json", "model.safetensors")


def _download_model_from_gcs(prefix: str, dest: Path) -> Path:
    """Descarga el modelo desde GCS de forma atómica (a un dir temporal y luego renombra),
    para que una descarga a medias nunca deje un directorio final incompleto."""
    from google.cloud import storage

    # Ya está completo: no re-descargar
    if all((dest / f).exists() for f in _REQUIRED_FILES):
        return dest

    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    client = storage.Client(project=settings.gcp_project_id)
    bucket = client.bucket(settings.gcs_bucket_name)
    prefix = prefix.rstrip("/") + "/"
    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        raise FileNotFoundError(f"No se encontró el modelo en gs://{settings.gcs_bucket_name}/{prefix}")

    for blob in blobs:
        rel = blob.name[len(prefix):]
        if not rel:
            continue
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))

    # Renombrado atómico: el dir final solo existe cuando la descarga terminó completa
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)
    logger.info(f"Modelo BETO descargado a {dest}")
    return dest


class BETOClassifier:
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def _resolve_model_path(self, path: str | None) -> str:
        model_path = path or settings.beto_model_path
        if Path(model_path).exists():
            return model_path
        # No está en disco local (entorno de producción): descargar desde GCS
        logger.info(f"Modelo BETO no está en {model_path}; descargando desde GCS...")
        return str(_download_model_from_gcs(settings.beto_model_gcs_prefix, _GCS_CACHE_DIR))

    def load_model(self, path: str | None = None):
        model_path = self._resolve_model_path(path)
        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_path).to(self.device)
        self.model.eval()

    def predict(self, texts: list[str]) -> list[dict]:
        if not self.model or not self.tokenizer:
            raise RuntimeError("Modelo no cargado. Ejecuta load_model() primero.")

        results = []
        batch_size = 32

        with torch.no_grad():
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                inputs = self.tokenizer(batch, truncation=True, padding=True, max_length=256, return_tensors="pt").to(self.device)
                outputs = self.model(**inputs)
                probs = torch.softmax(outputs.logits, dim=-1)
                preds = torch.argmax(probs, dim=-1)

                for j in range(len(batch)):
                    pred_label = LABELS[preds[j].item()]
                    confidence = probs[j][preds[j].item()].item()
                    results.append({"label": pred_label, "confidence": round(confidence, 4)})

        return results

    def classify_fundamentos(self, fundamentos: list[dict]) -> list[dict]:
        total = len(fundamentos)
        texts = []
        for f in fundamentos:
            pos_ratio = f["fundamento_num"] / total if total > 0 else 0
            if pos_ratio <= 0.15:
                tag = "INICIO"
            elif pos_ratio <= 0.40:
                tag = "DESARROLLO_TEMPRANO"
            elif pos_ratio <= 0.70:
                tag = "DESARROLLO"
            elif pos_ratio <= 0.90:
                tag = "DESARROLLO_TARDIO"
            else:
                tag = "CIERRE"
            texts.append(f"[POSICION: {f['fundamento_num']}/{total}] [{tag}] {f['texto']}")

        predictions = self.predict(texts)

        for f, pred in zip(fundamentos, predictions):
            f["beto_label"] = pred["label"]
            f["beto_confidence"] = pred["confidence"]

        return fundamentos


# Instancia compartida: el modelo (~480 MB) se carga UNA sola vez por proceso y se reutiliza
# en todos los análisis, evitando recargarlo en memoria en cada solicitud (causa de OOM).
# El lock evita que varios análisis concurrentes intenten descargar/cargar el modelo a la vez
# (condición de carrera que dejaba /tmp/beto_model incompleto).
import threading

_shared_classifier: "BETOClassifier | None" = None
_classifier_lock = threading.Lock()


def get_classifier() -> "BETOClassifier":
    global _shared_classifier
    if _shared_classifier is not None and _shared_classifier.model is not None:
        return _shared_classifier
    with _classifier_lock:
        if _shared_classifier is None or _shared_classifier.model is None:
            clf = BETOClassifier()
            clf.load_model()  # puede lanzar FileNotFoundError; lo maneja el llamador
            _shared_classifier = clf
    return _shared_classifier
