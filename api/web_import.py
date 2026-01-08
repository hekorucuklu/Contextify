import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

BLOCKED_EXTENSIONS = (
    ".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mp3"
)

def _is_public_http_url(url: str) -> bool:
    try:
        u = urlparse(url.strip())
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False

def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def fetch_readable_text(url: str, timeout_s: int = 15) -> str:
    url = (url or "").strip()

    if not _is_public_http_url(url):
        raise ValueError("Invalid URL. Please include http(s)://")

    low_url = url.lower()
    if low_url.endswith(BLOCKED_EXTENSIONS):
        raise ValueError("This URL points to a non-HTML file. Please paste a web page URL.")

    headers = {
        # Daha tarayıcı-benzeri UA: bazı sitelerde basit bot engelini azaltır
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": url,
    }

    try:
        r = requests.get(url, headers=headers, timeout=timeout_s, allow_redirects=True)
    except requests.RequestException as e:
        raise ValueError(f"Failed to fetch URL: {e}")

    # ✅ 403 kontrolü, r oluştuktan sonra yapılmalı
    if r.status_code == 403:
        raise ValueError(
            "This site blocked server-side fetching (403). "
            "Use the Contextify Bookmarklet to import directly from your browser."
        )

    # Diğer HTTP hataları
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise ValueError(f"HTTP error: {e}")

    content_type = (r.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        raise ValueError("URL did not return HTML content.")

    soup = BeautifulSoup(r.text, "html.parser")

    # remove noisy tags
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "form", "aside", "nav", "footer", "header"]):
        tag.decompose()

    # Prefer <main> if available
    main = soup.find("main")
    root = main if main else soup.body if soup.body else soup

    text = root.get_text(separator="\n")

    lines = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 2:
            continue
        low_line = line.lower()
        if low_line in ("cookie policy", "privacy policy", "terms of service"):
            continue
        lines.append(line)

    cleaned = _clean_text("\n".join(lines))

    # MVP safety cap
    return cleaned[:20000]
