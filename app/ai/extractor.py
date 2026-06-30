import io
import re

import pdfplumber


class PDFExtractor:
    FUND_PATTERN = re.compile(r"^\s*(?:§\s*)?(\d{1,3})\.\s+(.+)", re.DOTALL)

    def is_readable(self, pdf_bytes: bytes) -> tuple[bool, int]:
        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                page_count = len(pdf.pages)
                if page_count == 0:
                    return False, 0
                text = pdf.pages[0].extract_text() or ""
                return len(text.strip()) > 50, page_count
        except Exception:
            return False, 0

    def extract_text(self, pdf_bytes: bytes) -> str:
        full_text = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
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

    def extract_entities(self, text: str) -> dict:
        return {"parties": {}, "background": "", "ruling": ""}

    def get_page_mapping(self, pdf_bytes: bytes, fundamentos: list[dict]) -> dict[int, int]:
        page_map = {}
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, 1):
                page_text = page.extract_text() or ""
                for fund in fundamentos:
                    preview = fund["texto"][:80]
                    if preview in page_text:
                        page_map[fund["fundamento_num"]] = page_num
        return page_map
