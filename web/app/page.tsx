"use client";

import { useMemo, useState } from "react";

export default function Home() {
  const API_URL = useMemo(() => process.env.NEXT_PUBLIC_API_URL, []);
  const [file, setFile] = useState<File | null>(null);

  const [output, setOutput] = useState("");
  const [tokens, setTokens] = useState<number | null>(null);

  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const [copied, setCopied] = useState(false);

  const submit = async () => {
    setErr("");
    setOutput("");
    setTokens(null);

    if (!file) {
      setErr("Please select a PDF first.");
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

      // Robust parsing (handles non-json too)
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
          style={{
            textDecoration: "none",
            color: "#1F3A5F",
            fontSize: 14,
            opacity: 0.9,
          }}
          onClick={(e) => {
            e.preventDefault();
            window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
          }}
        >
          Privacy & limits ↓
        </a>
      </div>

      {/* Card */}
      <div
        style={{
          marginTop: 18,
          border: "1px solid #E2E8F0",
          borderRadius: 16,
          padding: 18,
          background: "#F8FAFC",
          boxShadow: "0 1px 0 rgba(15, 23, 42, 0.03)",
        }}
      >
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
          <label
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
            }}
          >
            <span style={{ fontSize: 14, color: "#334155" }}>{file ? "Selected:" : "Choose PDF"}</span>
            <strong style={{ fontSize: 14 }}>{file ? file.name : ""}</strong>
            <input
              type="file"
              accept=".pdf,application/pdf"
              disabled={busy}
              style={{ display: "none" }}
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
          </label>

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

          <button
            onClick={copy}
            disabled={!output || busy}
            style={{
              padding: "10px 14px",
              borderRadius: 12,
              border: "1px solid #E2E8F0",
              background: copied ? "#3BB3A9" : "#FFFFFF",
              color: copied ? "#0F172A" : "#0F172A",
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
