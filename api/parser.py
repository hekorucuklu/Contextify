from pdfminer.high_level import extract_text
import tempfile


def parse_pdf(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(delete=True, suffix=".pdf") as f:
        f.write(data)
        f.flush()
        text = extract_text(f.name)
    return clean_text(text)


def parse_text(text: str) -> str:
    return clean_text(text)


def clean_text(text: str) -> str:
    lines = text.splitlines()
    clean = []
    for l in lines:
        l = l.strip()
        if len(l) < 3:
            continue
        low = l.lower()
        if low.startswith("page "):
            continue
        clean.append(l)
    return "\n".join(clean)
