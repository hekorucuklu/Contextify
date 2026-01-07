from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import uuid

from parser import parse_pdf, parse_text
from formatter import build_context
from tokens import estimate_tokens

app = FastAPI(title="Contextify API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/convert")
async def convert(
    file: Optional[UploadFile] = File(None),
    raw_text: Optional[str] = Form(None),
    mode: str = Form("default")  # future-proof
):
    if not file and not raw_text:
        return {"error": "No input provided"}

    if file:
        content = parse_pdf(await file.read())
    else:
        content = parse_text(raw_text)

    context = build_context(content)
    tokens = estimate_tokens(context)

    return {
        "id": str(uuid.uuid4()),
        "context": context,
        "token_estimate": tokens
    }
