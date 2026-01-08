from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uuid

from web_import import fetch_readable_text
from parser import parse_pdf, parse_text
from formatter import build_context
from tokens import estimate_tokens

app = FastAPI(title="Contextify API")

# Şimdilik localhost; Vercel URL gelince burayı güncelleyeceğiz.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://contextify-neon.vercel.app"
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.get("/health")
def health():
    return {"ok": True}

@app.post("/convert")
async def convert(
    file: Optional[UploadFile] = File(None),
    raw_text: Optional[str] = Form(None),
    mode: str = Form("default"),
):
    if not file and not raw_text:
        return {"error": "No input provided"}

    if file:
        data = await file.read()
        if len(data) > 5 * 1024 * 1024:
            return {"error": "File too large (5MB max)"}
        content = parse_pdf(data)
    else:
        content = parse_text(raw_text or "")

    if len(content) > 20000:
        return {"error": "Free limit exceeded (content too large)"}

    context = build_context(content)
    tokens = estimate_tokens(context)

    return {"id": str(uuid.uuid4()), "context": context, "token_estimate": tokens}
    
@app.post("/convert_url")
async def convert_url(
    url: str = Form(...),
):
    try:
        content = fetch_readable_text(url)
    except Exception as e:
        return {"error": str(e)}

    if len(content) > 20000:
        return {"error": "Free limit exceeded (content too large)"}

    context = build_context(content)
    tokens = estimate_tokens(context)
    return {"id": str(uuid.uuid4()), "context": context, "token_estimate": tokens}
