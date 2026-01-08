"use client";

import { useMemo, useRef, useState } from "react";

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

  const openPicker = () => fileInputRef.current?.click();

  const resetForNext = () => {
    setFile(null);
    setOutput("");
    setTokens(null);
    setErr("");
    setCopied(false);

    // Allow selecting the same file again (important!)
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

  const submit = async () => {
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

      const text = await res.text();
      let data: any = {};
      try {
        data = JSON.parse(text);
      } catch {
        data = { raw: text };
      }

      if (!res.ok) {
        setErr(data?.error || `Request failed (${res.status})`);
        return;
      }

      if (data?.error) {
        setErr(data.error);
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

  const copy = async () => {
    if (!output) return;
    await navigator.clipboard.writeText(output);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

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

  const fileLabel = file ? file.name : "Choose PDF (or drag & drop)";
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

		<div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
  <input
    value={url}
    onChange={(e) => setUrl(e.target.value)}
    placeholder="Paste a web URL (https://...)"
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
    onClick={async () => {
      setErr("");
      setOutput("");
      setTokens(null);

      const API_URL = process.env.NEXT_PUBLIC_API_URL;
      if (!API_URL) {
        setErr("Missing NEXT_PUBLIC_API_URL (Vercel env var).");
        return;
      }

      setBusy(true);
      try {
        const form = new FormData();
        form.append("url", url.trim());

        const res = await fetch(`${API_URL}/convert_url`, { method: "POST", body: form });
        const text = await res.text();
        let data: any = {};
        try { data = JSON.parse(text); } catch { data = { raw: text }; }

        if (!res.ok) {
          setErr(data?.error || `Request failed (${res.status})`);
          return;
        }
        if (data?.error) {
          setErr(data.error);
          return;
        }

        const ctx = data?.context ?? "";
        setOutput(ctx);
        setTokens(data?.token_estimate ?? null);
        if (!ctx) setErr("URL converted, but output is empty (page may block bots).");
      } catch (e: any) {
        setErr(e?.message || "Network error");
      } finally {
        setBusy(false);
      }
    }}
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


        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
          {/* Choose/Change control */}
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

          {/* Clear / New */}
          <button
            type="button"
            onClick={resetForNext}
            disabled={busy && !file}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid #E2E8F0",
              background: "#FFFFFF",
              color: "#0F172A",
              cursor: busy ? "not-allowed" : "pointer",
              fontWeight: 600,
              opacity: file || output || err ? 1 : 0.6,
            }}
            title="Clear and start a new conversion"
          >
            New / Clear
          </button>

          {/* Convert */}
          <button
            onClick={submit}
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
            {busy ? "Converting…" : "Convert"}
          </button>

          {/* Copy */}
          <button
            onClick={copy}
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
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace",
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
          <strong style={{ color: "#0F172A" }}>MVP limits:</strong> 5MB per PDF and output capped (free safety limit).
        </p>
      </div>
    </main>
  );
}
