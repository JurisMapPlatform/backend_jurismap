import io
import re
from bisect import bisect_right

from pypdf import PdfReader


class PDFExtractor:
    FUND_PATTERN = re.compile(r"^\s*(?:§\s*)?(\d{1,3})\.\s+(.+)", re.DOTALL)

    # Encabezados de la parte resolutiva ("fallo") en sentencias del TC peruano.
    # Case-SENSITIVE a propósito: el encabezado formal va en MAYÚSCULAS ("HA RESUELTO"),
    # mientras que frases del cuerpo como "ha resuelto el problema" van en minúsculas y no
    # deben confundirse con el fallo.
    FALLO_PRIMARY = re.compile(r"\bHA\s+RESUELTO\b")
    FALLO_SECONDARY = re.compile(r"\b(SE\s+RESUELVE|RESUELVE|HA\s+DECIDIDO|SE\s+DECIDE|FALLA)\b")

    # Encabezados de sección de las sentencias del TC: línea completa y en MAYÚSCULAS, con numeración
    # opcional ("II. FUNDAMENTOS"; "11." es la lectura errónea de "II." en algunos PDF) y, en los
    # índices, el número de página al final ("II. FUNDAMENTOS 8").
    _PREFIX = r"^(?:[IVXL1]+\s*[.\-]\s*)?"
    SECTION_HEADERS = (
        ("antecedentes", re.compile(_PREFIX + r"ANTECEDENTES(?:\s+\d+)?$")),
        ("fundamentos", re.compile(_PREFIX + r"FUNDAMENTOS(?:\s+JUR[IÍ]DICOS)?(?:\s+\d+)?$")),
        ("fallo", re.compile(_PREFIX + r"(?:FALLO|FALLA|HA\s+RESUELTO|SE\s+RESUELVE|RESUELVE)(?:\s+\d+)?\s*:?$")),
        ("voto", re.compile(r"^(?:FUNDAMENTOS?\s+(?:DE\s+)?)?VOTO\s+(?:SINGULAR|DIRIMENTE|DEL?|DE\s+LOS?|DE\s+LA)\b")),
    )

    @classmethod
    def _section_of(cls, line: str) -> str | None:
        text = " ".join(line.split())
        if not text or len(text) > 120:
            return None
        for section, pattern in cls.SECTION_HEADERS:
            if pattern.match(text):
                return section
        return None

    def _reader(self, pdf_bytes: bytes) -> PdfReader:
        return PdfReader(io.BytesIO(pdf_bytes))

    def is_readable(self, pdf_bytes: bytes) -> tuple[bool, int]:
        try:
            reader = self._reader(pdf_bytes)
            page_count = len(reader.pages)
            if page_count == 0:
                return False, 0
            text = reader.pages[0].extract_text() or ""
            return len(text.strip()) > 50, page_count
        except Exception:
            return False, 0

    def extract_text(self, pdf_bytes: bytes) -> str:
        reader = self._reader(pdf_bytes)
        full_text = []
        for page in reader.pages:
            text = page.extract_text() or ""
            full_text.append(text)
        return "\n".join(full_text)

    def extract_fundamentos(self, pdf_bytes: bytes) -> list[dict]:
        """Extrae todos los párrafos numerados de la sentencia.

        Los bloques (número y texto) son exactamente los mismos con los que se entrenó el
        clasificador, que también incluyen antecedentes, puntos del fallo y votos. Además, cada
        bloque indica en `section` la parte de la sentencia donde empieza, para que después solo
        los de "fundamentos" lleguen a Gemini y al mapa."""
        full_text = self.extract_text(pdf_bytes)
        lines = full_text.split("\n")
        # Sin encabezado FUNDAMENTOS (p. ej. autos con "considerandos"), todo lo que no sea fallo
        # ni voto se trata como fundamento, igual que antes.
        has_header = any(self._section_of(line) == "fundamentos" for line in lines)
        section = "antecedentes" if has_header else "fundamentos"

        fundamentos = []
        current_num = None
        current_text = []
        current_section = section

        for line in lines:
            header = self._section_of(line)
            if header and (has_header or header != "antecedentes"):
                section = header
            match = self.FUND_PATTERN.match(line)
            if match:
                if current_num is not None and len(" ".join(current_text).split()) >= 20:
                    fundamentos.append({"fundamento_num": current_num, "texto": " ".join(current_text).strip(),
                                        "section": current_section})
                current_num = int(match.group(1))
                current_text = [match.group(2).strip()]
                current_section = section
            elif current_num is not None:
                current_text.append(line.strip())

        if current_num is not None and len(" ".join(current_text).split()) >= 20:
            fundamentos.append({"fundamento_num": current_num, "texto": " ".join(current_text).strip(),
                                "section": current_section})

        return fundamentos

    def extract_fallo(self, full_text: str, max_chars: int = 2500) -> str:
        """Devuelve la parte resolutiva (fallo) de la sentencia.

        El fallo va al final de la sentencia y no es un fundamento numerado, por lo que
        el pipeline debe pasárselo aparte a Gemini. Se prefiere el encabezado canónico
        "HA RESUELTO"; si no existe, se usa el primer marcador resolutivo; como último
        recurso, la cola del documento (donde suele estar la resolución).
        """
        if not full_text:
            return ""
        m = self.FALLO_PRIMARY.search(full_text) or self.FALLO_SECONDARY.search(full_text)
        if m:
            return full_text[m.start(): m.start() + max_chars].strip()
        return full_text[-2000:].strip()

    def extract_entities(self, text: str) -> dict:
        return {"parties": {}, "background": "", "ruling": ""}

    def assign_pages(self, pdf_bytes: bytes, fundamentos: list[dict]) -> None:
        """HU-12: guarda en cada fundamento la página donde empieza (f["page_number"]).

        Se comparan los textos con los espacios normalizados: el fundamento une sus líneas con
        espacios y la página conserva los saltos de línea, así que una comparación literal casi
        nunca coincide. Se busca por fundamento y no por número, porque una misma sentencia puede
        repetir numeraciones (antecedentes y fundamentos).

        El inicio del fundamento se busca en el texto de todas las páginas unidas, así también se
        encuentra cuando sus primeras palabras quedan partidas entre dos páginas; su página es
        aquella en la que cae ese inicio. Además se avanza en orden (cada fundamento se busca
        después del anterior), para que un texto repetido antes no le asigne otra página."""
        reader = self._reader(pdf_bytes)
        pages = [" ".join((page.extract_text() or "").split()) for page in reader.pages]
        inicios, pos = [], 0
        for text in pages:
            inicios.append(pos)
            pos += len(text) + 1  # +1 por el espacio que las une
        completo = " ".join(pages)

        cursor = 0
        for fund in fundamentos:
            preview = " ".join(fund.get("texto", "").split()[:8])
            idx = completo.find(preview, cursor) if preview else -1
            if idx == -1 and preview:
                idx = completo.find(preview)
            if idx == -1:
                fund["page_number"] = None
                continue
            fund["page_number"] = bisect_right(inicios, idx)
            cursor = idx + 1

    def get_page_mapping(self, pdf_bytes: bytes, fundamentos: list[dict]) -> dict[int, int]:
        reader = self._reader(pdf_bytes)
        page_map = {}
        for page_num, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            for fund in fundamentos:
                preview = fund["texto"][:80]
                if preview in page_text:
                    page_map[fund["fundamento_num"]] = page_num
        return page_map
