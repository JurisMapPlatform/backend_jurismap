import json
import logging
import re
import time

import vertexai
from vertexai.generative_models import GenerativeModel

from app.config import settings

logger = logging.getLogger(__name__)

# Arquitectura de dos etapas para elegir los fundamentos del mapa:
#   Etapa 1 (RoBERTalex): filtra los RELEVANTE por confianza -> hasta N candidatos.
#   Etapa 2 (Gemini): de esos candidatos, elige/ordena los más importantes.
MAX_FUNDAMENTO_CANDIDATOS = 20   # cuántos candidatos relevantes pasa RoBERTalex a Gemini
MAX_FUNDAMENTOS_MAPA = 15        # tope duro de fundamentos en el mapa


class GeminiClient:
    def __init__(self):
        vertexai.init(project=settings.gcp_project_id, location=settings.gemini_location)
        self.model = GenerativeModel(settings.gemini_model)

    def _generate(self, prompt: str, max_retries: int = 4) -> str:
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    wait = 15 * (2 ** attempt)
                    logger.warning(f"Gemini 429, reintentando en {wait}s (intento {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError("Max retries exceeded")

    def _parse_json(self, text: str) -> dict | list:
        cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"[\[{].*[}\]]", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise

    def analyze_fundamentos(self, fundamentos: list[dict]) -> list[dict]:
        # --- Etapa 1: RoBERTalex define el universo de candidatos (filtro de relevancia) ---
        # Un fundamento puede aparecer varias veces en el PDF; se de-duplica por número
        # quedándose con la instancia de MAYOR confianza. Luego se toman los RELEVANTE
        # ordenados por confianza (top N). Gemini SOLO verá estos candidatos.
        by_num: dict[int, dict] = {}
        for f in fundamentos:
            n = f["fundamento_num"]
            if n not in by_num or f.get("beto_confidence", 0) > by_num[n].get("beto_confidence", 0):
                by_num[n] = f
        unicos = list(by_num.values())
        relevantes = sorted(
            [f for f in unicos if f.get("beto_label") == "RELEVANTE"],
            key=lambda f: f.get("beto_confidence", 0.0), reverse=True,
        )
        # Si el clasificador no marcó ninguno como RELEVANTE, se usan los de mayor confianza.
        candidatos = (relevantes or sorted(unicos, key=lambda f: f.get("beto_confidence", 0.0), reverse=True))
        candidatos = candidatos[:MAX_FUNDAMENTO_CANDIDATOS]

        cand_text = "\n".join(
            f"[Fund. {f['fundamento_num']}] (confianza RoBERTalex: {f.get('beto_confidence', 0):.2f}) {f['texto'][:500]}"
            for f in candidatos
        )

        # --- Etapa 2: Gemini elige y ordena los MÁS IMPORTANTES entre los candidatos ---
        prompt = f"""Eres un experto en derecho constitucional peruano. Un clasificador especializado (RoBERTalex) ya filtró los fundamentos jurídicamente RELEVANTES de esta sentencia del Tribunal Constitucional. Estos son los candidatos (todos ya considerados relevantes):

{cand_text}

De ESTOS candidatos, selecciona los MÁS IMPORTANTES para entender el razonamiento del caso y construir un mapa mental claro.

REGLAS:
- Elige entre 5 y 15 fundamentos; idealmente alrededor de 8. NUNCA más de 15.
- Ordénalos de MÁS a MENOS importante.
- Usa ÚNICAMENTE números que aparezcan en la lista de candidatos; no inventes números.

Responde SOLO con JSON, en orden de importancia:
[{{"n": 12, "summary": "resumen breve y claro del fundamento"}}]"""

        text = self._generate(prompt)
        result = self._parse_json(text)
        if isinstance(result, list):
            # Tope duro: nunca más de MAX_FUNDAMENTOS_MAPA, aunque Gemini se pase.
            return result[:MAX_FUNDAMENTOS_MAPA]
        return result

    def simplify(self, texto: str) -> str:
        prompt = f"""Simplifica el siguiente fundamento jurídico para que un estudiante de derecho
lo entienda fácilmente. Mantén la precisión jurídica pero usa lenguaje claro.
Máximo 2 oraciones.

Fundamento: {texto[:1500]}

Responde SOLO con el texto simplificado."""

        return self._generate(prompt).strip()

    def build_mindmap(self, analysis_data: dict, custom_prompt: str | None = None) -> dict:
        context = json.dumps(analysis_data, ensure_ascii=False)[:16000]
        expediente = analysis_data.get("expediente", "Sentencia TC")

        reglas_comunes = """REGLAS DE FORMATO (siempre aplican):
1. El "label" de cada sub-nodo debe ser un TÍTULO de MÁXIMO 3 PALABRAS. Ejemplos: "Principio proporcionalidad", "Cese acto lesivo", "Derecho al trabajo". Para fundamentos usa "Fund. N" donde N es el número del fundamento.
2. NUNCA pongas párrafos ni oraciones en el "label". El texto largo va SOLO en metadata.summary (explicación simplificada) y metadata.original (texto exacto de la sentencia).
3. El nodo raíz tiene type "central"; las categorías type "category"; los fundamentos type "fundamento"; los demás type "detail".
4. Responde SOLO con JSON válido (claves "nodes" y "edges"), sin texto adicional ni markdown."""

        if custom_prompt:
            prompt = f"""Eres un experto en derecho constitucional peruano. Genera un mapa mental en JSON de la siguiente sentencia.

INSTRUCCIÓN PRIORITARIA DEL USUARIO (es una ORDEN que debes obedecer literalmente, por encima de cualquier estructura por defecto):
\"\"\"{custom_prompt}\"\"\"

REGLAS DE OBEDIENCIA (críticas):
- Incluye ÚNICAMENTE las categorías y nodos que el usuario pide EXPLÍCITAMENTE. Si el usuario pide "solo los fundamentos", el mapa debe tener EXCLUSIVAMENTE el nodo raíz, la categoría "Fundamentos" y sus fundamentos. NADA MÁS.
- PROHIBIDO agregar categorías que el usuario no mencionó (no agregues materia, partes, pretensión, antecedentes, fallo ni votos singulares a menos que el usuario los nombre).
- El nodo raíz (type "central", label "{expediente}") siempre existe y de él cuelga lo que el usuario solicitó.
- NO incluyas "votos singulares" salvo que el usuario lo pida expresamente.

Antes de responder, verifica: ¿cada categoría de mi salida fue pedida por el usuario? Si no, elimínala.

Datos de la sentencia:
{context}

{reglas_comunes}

Ejemplo de formato de salida (la ESTRUCTURA real depende SOLO de lo que pida el usuario):
{{
  "nodes": [
    {{"id": "root", "type": "central", "label": "{expediente}"}},
    {{"id": "fundamentos", "type": "category", "label": "Fundamentos"}},
    {{"id": "fund_1", "type": "fundamento", "label": "Fund. 1", "metadata": {{"fundamento_num": 1, "summary": "explicación simplificada", "original": "texto completo del fundamento"}}}}
  ],
  "edges": [
    {{"source": "root", "target": "fundamentos"}},
    {{"source": "fundamentos", "target": "fund_1"}}
  ]
}}

RECUERDA: obedece la instrucción del usuario al pie de la letra para decidir QUÉ nodos incluir. Si pidió solo una categoría, NO incluyas ninguna otra."""
        else:
            prompt = f"""Eres un experto en derecho constitucional peruano. Genera un mapa mental en JSON.

Datos:
{context}

{reglas_comunes}

5. DEBES incluir EXACTAMENTE estas 6 categorías como nodos hijos del nodo raíz, NI MÁS NI MENOS:
   - materia, partes, pretension, antecedentes, fundamentos, fallo
6. Cada categoría DEBE tener al menos 1 sub-nodo.
7. PROHIBIDO incluir un nodo de "Votos singulares" o similar. NUNCA lo agregues en este modo predeterminado.
8. Para la categoría "fallo" USA el campo "fallo_text" de los datos (es la parte resolutiva real al final de la sentencia, p. ej. "HA RESUELTO ..."). Crea 1 SOLO nodo (MÁXIMO 2) para el fallo: pon el sentido general de la decisión en el "label" (MÁX 3 palabras, p. ej. "Fundada en parte", "Improcedente") y copia TODO el texto resolutivo EXACTO en metadata.original, con una explicación en metadata.summary. Usa 2 nodos SOLO si hay dos sentidos claramente distintos (p. ej. una parte fundada y otra infundada); NUNCA más de 2, aunque el fallo tenga varios puntos numerados (agrúpalos). NUNCA inventes el fallo: si "fallo_text" viene vacío o sin resolución clara, dilo en el summary.

JSON (responde SOLO esto, nada más):
{{
  "nodes": [
    {{"id": "root", "type": "central", "label": "{expediente}"}},
    {{"id": "materia", "type": "category", "label": "Materia"}},
    {{"id": "materia_1", "type": "detail", "label": "Proceso de amparo", "metadata": {{"summary": "Proceso constitucional de amparo por vulneración de derechos fundamentales"}}}},
    {{"id": "partes", "type": "category", "label": "Partes del Proceso"}},
    {{"id": "partes_dem", "type": "detail", "label": "Dem: Nombre", "metadata": {{"summary": "nombre completo del demandante"}}}},
    {{"id": "partes_ddo", "type": "detail", "label": "Ddo: Nombre", "metadata": {{"summary": "nombre completo del demandado"}}}},
    {{"id": "pretension", "type": "category", "label": "Pretensión"}},
    {{"id": "pretension_1", "type": "detail", "label": "Título 3 palabras", "metadata": {{"summary": "explicación completa de la pretensión", "original": "texto exacto de la sentencia"}}}},
    {{"id": "antecedentes", "type": "category", "label": "Antecedentes"}},
    {{"id": "antecedentes_1", "type": "detail", "label": "Título 3 palabras", "metadata": {{"summary": "explicación del antecedente", "original": "texto de la sentencia"}}}},
    {{"id": "fundamentos", "type": "category", "label": "Fundamentos"}},
    {{"id": "fund_1", "type": "fundamento", "label": "Fund. 1", "metadata": {{"fundamento_num": 1, "summary": "explicación simplificada del fundamento", "original": "texto COMPLETO del fundamento tal como aparece en la sentencia"}}}},
    {{"id": "fallo", "type": "category", "label": "Fallo"}},
    {{"id": "fallo_1", "type": "detail", "label": "Infundada", "metadata": {{"summary": "se declara infundada la demanda", "original": "texto del fallo"}}}}
  ],
  "edges": [
    {{"source": "root", "target": "materia"}},
    {{"source": "root", "target": "partes"}},
    {{"source": "root", "target": "pretension"}},
    {{"source": "root", "target": "antecedentes"}},
    {{"source": "root", "target": "fundamentos"}},
    {{"source": "root", "target": "fallo"}},
    {{"source": "materia", "target": "materia_1"}},
    {{"source": "partes", "target": "partes_dem"}},
    {{"source": "partes", "target": "partes_ddo"}},
    {{"source": "pretension", "target": "pretension_1"}},
    {{"source": "antecedentes", "target": "antecedentes_1"}},
    {{"source": "fundamentos", "target": "fund_1"}},
    {{"source": "fallo", "target": "fallo_1"}}
  ]
}}

RECUERDA: label = MÁXIMO 3 PALABRAS. metadata.original = texto COMPLETO del fundamento. Las 6 categorías son OBLIGATORIAS."""

        text = self._generate(prompt)
        return self._parse_json(text)

    def compare_sentences(self, analyses_data: list[dict]) -> dict:
        context = json.dumps(analyses_data, ensure_ascii=False)[:8000]
        prompt = f"""Genera un mapa mental comparativo de las siguientes sentencias del TC peruano.
Crea un nodo central, un nodo rama por cada sentencia, y un nodo de "Puntos en común".

Datos:
{context}

Responde SOLO con JSON en formato React Flow (nodes + edges)."""

        text = self._generate(prompt)
        return self._parse_json(text)

    def generate_node(self, context: dict, prompt: str) -> dict:
        context_str = json.dumps(context, ensure_ascii=False)[:3000]
        gen_prompt = f"""Basándote en el siguiente contexto de un mapa mental jurídico,
genera un nuevo nodo según la instrucción del usuario.

Contexto del mapa: {context_str}
Instrucción del usuario: {prompt}

REGLAS:
- "label": título de MÁXIMO 3 PALABRAS que resuma el nodo.
- "metadata.summary": explicación clara y completa (2-4 oraciones) que responda a la instrucción.

Responde SOLO con JSON:
{{"label": "título corto", "metadata": {{"summary": "explicación completa del nodo"}}}}"""

        text = self._generate(gen_prompt)
        return self._parse_json(text)

    def extract_parties(self, text: str) -> dict:
        prompt = f"""Extrae las partes procesales del siguiente texto de una sentencia del TC peruano.

{text[:3000]}

Responde SOLO con JSON: {{"demandante": "nombre", "demandado": "nombre", "materia": "tipo de proceso"}}"""

        text = self._generate(prompt)
        return self._parse_json(text)
