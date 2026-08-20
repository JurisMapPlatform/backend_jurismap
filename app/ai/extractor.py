import io
import re

from pypdf import PdfReader


class PDFExtractor:
    FUND_PATTERN = re.compile(r"^\s*(?:§\s*)?(\d{1,3})\.\s+(.+)", re.DOTALL)

    # Encabezados de la parte resolutiva ("fallo") en sentencias del TC peruano.
    # Case-SENSITIVE a propósito: el encabezado formal va en MAYÚSCULAS ("HA RESUELTO"),
    # mientras que frases del cuerpo como "ha resuelto el problema" van en minúsculas y no
    # deben confundirse con el fallo.
    FALLO_PRIMARY = re.compile(r"\bHA\s+RESUELTO\b")
    FALLO_SECONDARY = re.compile(r"\b(SE\s+RESUELVE|RESUELVE|HA\s+DECIDIDO|SE\s+DECIDE|FALLA)\b")

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
        full_text = self.extract_text(pdf_bytes)
        fundamentos = []
        current_num = None
        current_text = []

        for line in full_text.split("\n"):
            match = self.FUND_PATTERN.match(line)
            if match:
                if current_num is not None and len(" ".join(current_text).split()) >= 20:
                    fundamentos.append({"fundamento_num": current_num, "texto": " ".join(current_text).strip()})
                current_num = int(match.group(1))
                current_text = [match.group(2).strip()]
            elif current_num is not None:
                current_text.append(line.strip())

        if current_num is not None and len(" ".join(current_text).split()) >= 20:
            fundamentos.append({"fundamento_num": current_num, "texto": " ".join(current_text).strip()})

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
