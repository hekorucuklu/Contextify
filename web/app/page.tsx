"use client";

import { useEffect, useMemo, useRef, useState } from "react";

export default function Home() {
  const API_URL = useMemo(() => process.env.NEXT_PUBLIC_API_URL, []);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const [output, setOutput] = useState("");
  const [tokens, setTokens] = useState<number | null>(null);

  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const [dragOver, setDragOver] = useState(false);

  // 🔧 Update this if your production domain is different
  const WEB_APP_URL = "https://contextify-neon.vercel.app";

  // Client-side cap to prevent UI freezes on massive pages
  const MAX_IMPORT_CHARS = 120_000;

  const openPicker = () => fileInputRef.current?.click();

  const parseAny = async (res: Response) => {
    const text = await res.text();
    try {
      return JSON.parse(text);
    } catch {
      return { raw: text };
    }
  };

  const normalizeErr = (data: any, res?: Response) => {
    return (
      data?.error ||
      data?.detail ||
      data?.message ||
      (res ? `Request failed (${res.status})` : "Unknown error")
    );
  };

  const resetForNext = () => {
    setFile(null);
    setUrl("");
    setOutput("");
    setTokens(null);
    setErr("");
    setCopied(false);

    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const acceptFile = (f: File) => {
    setErr("");
    setCopied(false);

    const isPdf =
      f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setErr("Only PDF files are supported in this MVP.");
      return;
    }

    if (f.size > 5 * 1024 * 1024) {
      setErr("File too large (5MB max).");
      return;
    }

    setFile(f);
  };

  const submitPdf = async () => {
    setErr("");
    setOutput("");
    setTokens(null);

    if (!file) {
      setErr("Please select a PDF first (or drag & drop one).");
      return;
    }

    if (!API_URL) {
      setErr("Missing NEXT_PUBLIC_API_URL (Vercel env var).");
      return;
    }

    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);

      const res = await fetch(`${API_URL}/convert`, { method: "POST", body: form });
      const data: any = await parseAny(res);

      // Handle both "proper" HTTP errors and legacy "200 + {error}"
      if (!res.ok || data?.error || data?.detail) {
        setErr(normalizeErr(data, res));
        return;
      }

      const ctx = data?.context ?? "";
      setOutput(ctx);
      setTokens(data?.token_estimate ?? null);

      if (!ctx) setErr("API returned success, but the output is empty (try another PDF).");
    } catch (e: any) {
      setErr(e?.message || "Network error");
    } finally {
      setBusy(false);
    }
  };

  const submitUrl = async () => {
    setErr("");
    setOutput("");
    setTokens(null);

    if (!url.trim()) return;

    if (!API_URL) {
      setErr("Missing NEXT_PUBLIC_API_URL (Vercel env var).");
      return;
    }

    setBusy(true);
    try {
      const form = new FormData();
      form.append("url", url.trim());

      const res = await fetch(`${API_URL}/convert_url`, { method: "POST", body: form });
      const data: any = await parseAny(res);

      if (!res.ok || data?.error || data?.detail) {
        setErr(normalizeErr(data, res));
        return;
      }

      const ctx = data?.context ?? "";
      setOutput(ctx);
      setTokens(data?.token_estimate ?? null);
      if (!ctx) setErr("URL converted, but output is empty.");
    } catch (e: any) {
      setErr(e?.message || "Network error");
    } finally {
      setBusy(false);
    }
  };

  const copyOutput = async () => {
    if (!output) return;
    await navigator.clipboard.writeText(output);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  // ✅ Bookmarklet code (copy & paste into Chrome bookmark URL field)
  const bookmarkletCode =
	  "javascript:(()=>{try{const pick=()=>{const a=document.querySelector('article');if(a&&a.innerText&&a.innerText.trim().length>200)return a.innerText;const main=document.querySelector('main');if(main&&main.innerText&&main.innerText.trim().length>200)return main.innerText;const c=document.querySelector('[data-testid=\"post-content\"],[role=\"article\"]');if(c&&c.innerText&&c.innerText.trim().length>200)return c.innerText;return (document.body&&document.body.innerText)||'';};const t=((window.getSelection&&window.getSelection().toString().trim())||'');const text=(t&&t.length>200)?t:pick();const u='" +
	  WEB_APP_URL +
	  "/?import=1';const w=window.open(u,'_blank');setTimeout(()=>{try{w&&w.postMessage({type:'CONTEXTIFY_IMPORT',text:text},'*')}catch(e){}},1200);alert('Opening Contextify and sending text…');}catch(e){alert('Contextify Import failed: '+e);}})();";


  const addBookmarklet = async () => {
    try {
      await navigator.clipboard.writeText(bookmarkletCode);
      alert(
        "Bookmarklet copied ✅\n\nInstall (Chrome):\n1) Press Ctrl+Shift+O\n2) ⋮ menu → Add new bookmark\n3) Name: Contextify Import\n4) Paste into URL field\n\nUse: Open an article (optional select text) → click bookmark."
      );
    } catch {
      alert("Could not copy automatically. Please copy the bookmarklet code manually.");
    }
  };

  // ✅ Accept imported text via postMessage (from bookmarklet / future extension)
  useEffect(() => {
    const handler = async (event: MessageEvent) => {
      if (!event?.data || event.data.type !== "CONTEXTIFY_IMPORT") return;

      let text = String(event.data.text || "").trim();

      if (text.length < 50) {
        setErr("Imported text is too short. Select more content and try again.");
        return;
      }

      // Prevent UI freezes on extremely large imports
      if (text.length > MAX_IMPORT_CHARS) {
        text = text.slice(0, MAX_IMPORT_CHARS);
        // Non-blocking hint (do not overwrite an existing error)
        setErr(
          `Imported text was very long, so we truncated it to ${MAX_IMPORT_CHARS.toLocaleString()} characters (free safety cap).`
        );
      } else {
        setErr("");
      }

      if (!API_URL) {
        setErr("Missing NEXT_PUBLIC_API_URL (Vercel env var).");
        return;
      }

      setOutput("");
      setTokens(null);
      setCopied(false);

      setBusy(true);
      try {
        const form = new FormData();
        form.append("raw_text", text);

        const res = await fetch(`${API_URL}/convert`, { method: "POST", body: form });
        const data: any = await parseAny(res);

        if (!res.ok || data?.error || data?.detail) {
          setErr(normalizeErr(data, res));
          return;
        }

        const ctx = data?.context ?? "";
        setOutput(ctx);
        setTokens(data?.token_estimate ?? null);
        if (!ctx) setErr("Imported successfully, but output is empty.");
      } catch (e: any) {
        setErr(e?.message || "Network error");
      } finally {
        setBusy(false);
      }
    };

    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [API_URL]); // eslint-disable-line react-hooks/exhaustive-deps

  // Drag & Drop handlers
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (busy) return;
    setDragOver(true);
  };

  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (busy) return;

    const f = e.dataTransfer.files?.[0];
    if (f) acceptFile(f);
  };

  const helperText = file
    ? "Tip: You can change the file anytime."
    : "Tip: Drag & drop a PDF anywhere in this card.";

  return (
    <main
      style={{
        maxWidth: 980,
        margin: "48px auto",
        padding: 20,
        fontFamily: "Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial",
        color: "#0F172A",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 40, margin: 0, letterSpacing: -0.5 }}>Contextify</h1>
          <p style={{ margin: "6px 0 0 0", color: "#475569" }}>Make any document AI-ready</p>
        </div>
        <a
          href="#"
          style={{ textDecoration: "none", color: "#1F3A5F", fontSize: 14, opacity: 0.9 }}
          onClick={(e) => {
            e.preventDefault();
            window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
          }}
        >
          Privacy & limits ↓
        </a>
      </div>

      {/* Card (Drop zone) */}
      <div
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        style={{
          position: "relative",
          marginTop: 18,
          border: dragOver ? "1px solid #3BB3A9" : "1px solid #E2E8F0",
          borderRadius: 16,
          padding: 18,
          background: "#F8FAFC",
          boxShadow: "0 1px 0 rgba(15, 23, 42, 0.03)",
          outline: dragOver ? "4px solid rgba(59, 179, 169, 0.15)" : "none",
          transition: "outline 120ms ease, border-color 120ms ease",
        }}
      >
        {/* Drag overlay */}
        {dragOver && (
          <div
            style={{
              position: "absolute",
              inset: 10,
              borderRadius: 14,
              background: "rgba(59, 179, 169, 0.10)",
              border: "1px dashed #3BB3A9",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              color: "#0F172A",
              pointerEvents: "none",
            }}
          >
            Drop PDF to upload
          </div>
        )}

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,application/pdf"
          disabled={busy}
          style={{ display: "none" }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) acceptFile(f);
          }}
        />

        {/* URL row */}
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="Paste a web URL (https://...)"
            disabled={busy}
            style={{
              flex: "1 1 420px",
              padding: "10px 12px",
              borderRadius: 12,
              border: "1px solid #E2E8F0",
              background: "#FFFFFF",
              fontSize: 14,
            }}
          />
          <button
            type="button"
            disabled={busy || !url.trim()}
            onClick={submitUrl}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid #0F172A",
              background: busy || !url.trim() ? "#E2E8F0" : "#0F172A",
              color: busy || !url.trim() ? "#334155" : "#FFFFFF",
              cursor: busy || !url.trim() ? "not-allowed" : "pointer",
              fontWeight: 700,
            }}
            title="Convert the web page into AI-ready context"
          >
            {busy ? "Converting…" : "Convert URL"}
          </button>
        </div>

        {/* Bookmarklet */}
        <div style={{ fontSize: 13, color: "#475569", marginBottom: 12 }}>
          Blocked sites (e.g., Medium)? Install the <strong>Contextify Bookmarklet</strong>.
          <div style={{ marginTop: 8, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              disabled={busy}
              onClick={addBookmarklet}
              style={{
                padding: "10px 14px",
                borderRadius: 12,
                border: "1px solid #0F172A",
                background: busy ? "#E2E8F0" : "#0F172A",
                color: busy ? "#334155" : "#FFFFFF",
                cursor: busy ? "not-allowed" : "pointer",
                fontWeight: 800,
              }}
              title="Copies bookmarklet code + shows install steps"
            >
              Add Bookmarklet
            </button>

            <span style={{ fontSize: 12, color: "#64748B" }}>
              Copies code to clipboard + shows install steps
            </span>
          </div>
        </div>

        {/* PDF controls */}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
          <button
            type="button"
            onClick={openPicker}
            disabled={busy}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 12px",
              background: "#FFFFFF",
              border: "1px solid #E2E8F0",
              borderRadius: 12,
              cursor: busy ? "not-allowed" : "pointer",
              opacity: busy ? 0.6 : 1,
              fontWeight: 700,
            }}
            title="Choose a PDF file"
          >
            {file ? "Change PDF:" : "Choose PDF"}
            <span style={{ fontWeight: 600, color: "#334155" }}>{file ? file.name : ""}</span>
            {!file && <span style={{ fontWeight: 600, color: "#475569" }}>(or drag & drop)</span>}
          </button>

          <button
            type="button"
            onClick={resetForNext}
            disabled={busy}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid #E2E8F0",
              background: "#FFFFFF",
              color: "#0F172A",
              cursor: busy ? "not-allowed" : "pointer",
              fontWeight: 600,
              opacity: file || output || err || url ? 1 : 0.6,
            }}
            title="Clear and start a new conversion"
          >
            New / Clear
          </button>

          <button
            onClick={submitPdf}
            disabled={busy || !file}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid #0F172A",
              background: busy || !file ? "#E2E8F0" : "#0F172A",
              color: busy || !file ? "#334155" : "#FFFFFF",
              cursor: busy || !file ? "not-allowed" : "pointer",
              fontWeight: 600,
            }}
          >
            {busy ? "Converting…" : "Convert PDF"}
          </button>

          <button
            onClick={copyOutput}
            disabled={!output || busy}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid #E2E8F0",
              background: copied ? "#3BB3A9" : "#FFFFFF",
              color: "#0F172A",
              cursor: !output || busy ? "not-allowed" : "pointer",
              fontWeight: 600,
            }}
            title={!output ? "Convert first" : "Copy output"}
          >
            {copied ? "Copied ✓" : "Copy"}
          </button>

          {tokens !== null && (
            <span
              style={{
                marginLeft: "auto",
                fontSize: 13,
                color: "#475569",
                padding: "6px 10px",
                borderRadius: 999,
                border: "1px solid #E2E8F0",
                background: "#FFFFFF",
              }}
              title="Approximate token count (cl100k_base)"
            >
              Est. tokens: <strong>{tokens}</strong>
            </span>
          )}
        </div>

        <div style={{ marginTop: 10, fontSize: 13, color: "#475569" }}>{helperText}</div>

        {err && (
          <div
            style={{
              marginTop: 12,
              padding: "10px 12px",
              borderRadius: 12,
              border: "1px solid rgba(220, 38, 38, 0.25)",
              background: "rgba(220, 38, 38, 0.06)",
              color: "#991B1B",
              fontSize: 14,
              whiteSpace: "pre-wrap",
            }}
          >
            {err}
          </div>
        )}

        <textarea
          value={output}
          readOnly
          rows={18}
          placeholder="Your AI-ready context will appear here…"
          style={{
            width: "100%",
            marginTop: 12,
            padding: 14,
            borderRadius: 16,
            border: "1px solid #E2E8F0",
            background: "#FFFFFF",
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace",
            fontSize: 13,
            lineHeight: 1.5,
            color: "#0F172A",
          }}
        />
      </div>

      {/* Footer */}
      <div id="privacy" style={{ marginTop: 18, color: "#475569", fontSize: 13, lineHeight: 1.6 }}>
        <p style={{ margin: 0 }}>
          <strong style={{ color: "#0F172A" }}>Privacy:</strong> Files are processed on-demand and not stored.
        </p>
        <p style={{ margin: "6px 0 0 0" }}>
          <strong style={{ color: "#0F172A" }}>MVP limits:</strong> Up to <strong>5MB</strong> per PDF. Very long documents may exceed the free <strong>content cap</strong> even below 5MB.
        </p>
      </div>
    </main>
  );
}
