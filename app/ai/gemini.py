import json
import logging
import re
import time

import vertexai
from vertexai.generative_models import GenerativeModel

from app.config import settings

logger = logging.getLogger(__name__)


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
        relevantes = [f for f in fundamentos if f.get("beto_label") == "RELEVANTE"]
        no_relevantes = [f for f in fundamentos if f.get("beto_label") == "NO_RELEVANTE"]

        rel_text = "\n".join(
            f"[Fund. {f['fundamento_num']}] (BETO: RELEVANTE, confianza: {f.get('beto_confidence', 0):.2f}) {f['texto'][:500]}"
            for f in relevantes
        )
        nrel_summary = "\n".join(
            f"[Fund. {f['fundamento_num']}] (BETO: NO_RELEVANTE, confianza: {f.get('beto_confidence', 0):.2f}) {f['texto'][:200]}"
            for f in no_relevantes
        )

        prompt = f"""Eres un experto en derecho constitucional peruano. Analiza los fundamentos de esta sentencia del Tribunal Constitucional.

Un modelo de IA (BETO) ya clasificó cada fundamento como RELEVANTE o NO_RELEVANTE. Usa estas clasificaciones como referencia, pero toma tu propia decisión final.

FUNDAMENTOS MARCADOS COMO RELEVANTES POR BETO (prioridad alta):
{rel_text}

FUNDAMENTOS MARCADOS COMO NO RELEVANTES POR BETO (revisa si alguno debería incluirse):
{nrel_summary}

Selecciona los fundamentos más importantes para construir un mapa mental del razonamiento jurídico.
Puedes incluir fundamentos que BETO marcó como NO_RELEVANTE si consideras que son esenciales.
Puedes excluir fundamentos que BETO marcó como RELEVANTE si son redundantes o poco informativos.

Responde SOLO con JSON:
[{{"n": 1, "summary": "resumen breve", "beto_agreed": true}}]

Donde "beto_agreed" indica si coincides con la clasificación de BETO para ese fundamento."""

        text = self._generate(prompt)
        return self._parse_json(text)

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
8. Para la categoría "fallo" USA el campo "fallo_text" de los datos (es la parte resolutiva real al final de la sentencia, p. ej. "HA RESUELTO ..."). Coloca el sentido de la decisión en el "label" (MÁX 3 palabras, p. ej. "Fundada en parte", "Improcedente") y copia el texto resolutivo EXACTO en metadata.original, con una explicación en metadata.summary. Si "fallo_text" tiene varios puntos resolutivos, crea un sub-nodo por punto. NUNCA inventes el fallo: si "fallo_text" viene vacío o sin resolución clara, dilo en el summary.

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
