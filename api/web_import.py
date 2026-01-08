import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse


BLOCKED_EXTENSIONS = (".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mp3")


def _is_public_http_url(url: str) -> bool:
    try:
        u = urlparse(url.strip())
        if u.scheme not in ("http", "https"):
            return False
        if not u.netloc:
            return False
        return True
    except Exception:
        return False


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_readable_text(url: str, timeout_s: int = 15) -> str:
    if not _is_public_http_url(url):
        raise ValueError("Invalid URL. Please include http(s)://")

    if r.status_code == 403:
        raise ValueError(
            "This site blocked server-side fetching (403). "
            "Use the Contextify Bookmarklet to import directly from your browser."
        )

    low = url.lower()
    if low.endswith(BLOCKED_EXTENSIONS):
        raise ValueError("This URL points to a non-HTML file. Please paste a web page URL.")

    headers = {
        "User-Agent": "ContextifyBot/1.0 (+https://contextify-neon.vercel.app)",
        "Accept": "text/html,application/xhtml+xml",
    }

    r = requests.get(url, headers=headers, timeout=timeout_s)
    r.raise_for_status()

    content_type = (r.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        raise ValueError("URL did not return HTML content.")

    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # remove noisy tags
    for tag in soup(["script", "style", "noscript", "svg", "canvas", "form", "aside", "nav", "footer", "header"]):
        tag.decompose()

    # Prefer <main> if available
    main = soup.find("main")
    root = main if main else soup.body if soup.body else soup

    # Collect readable text
    text = root.get_text(separator="\n")

    # Basic cleanup
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if len(line) < 2:
            continue
        # skip very common cookie/footer junk
        low = line.lower()
        if low in ("cookie policy", "privacy policy", "terms of service"):
            continue
        lines.append(line)

    cleaned = _clean_text("\n".join(lines))

    # MVP safety cap
    return cleaned[:20000]
