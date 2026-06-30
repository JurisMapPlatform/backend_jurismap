from pathlib import Path

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

from app.config import settings

LABELS = {0: "NO_RELEVANTE", 1: "RELEVANTE"}


class BETOClassifier:
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def load_model(self, path: str | None = None):
        model_path = path or settings.beto_model_path
        if not Path(model_path).exists():
            raise FileNotFoundError(f"Modelo BETO no encontrado en {model_path}")
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
