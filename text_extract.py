import pdfplumber
import docx
from pathlib import Path
import pytesseract
from PIL import Image

def extract_text_from_pdf(path):
    texts = []
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    texts.append(page_text)
    except Exception as e:
        print(f"PDF extract error {path}: {e}")
    return "\n".join(texts)

def extract_text_from_docx(path):
    try:
        doc = docx.Document(path)
        return "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        print(f"DOCX extract error {path}: {e}")
        return ""

def extract_text(path: Path):
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    elif ext in [".docx", ".doc"]:
        return extract_text_from_docx(path)
    elif ext in [".png", ".jpg", ".jpeg", ".tiff"]:
        try:
            text = pytesseract.image_to_string(Image.open(path), lang='rus')
            return text
        except Exception as e:
            print(f"OCR error {path}: {e}")
            return ""
    return ""
