"use client";

import { useState } from "react";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [output, setOutput] = useState("");
  const [tokens, setTokens] = useState<number | null>(null);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    setOutput("");
    setTokens(null);

    if (!file) return;

    const API_URL = process.env.NEXT_PUBLIC_API_URL;
    if (!API_URL) {
      setErr("Missing NEXT_PUBLIC_API_URL");
      return;
    }

    const form = new FormData();
    form.append("file", file);

    const res = await fetch(`${API_URL}/convert`, { method: "POST", body: form });
    const data = await res.json();

    if (!res.ok || data.error) {
      setErr(data.error || "Request failed");
      return;
    }

    setOutput(data.context);
    setTokens(data.token_estimate);
  };

  const copy = async () => {
    await navigator.clipboard.writeText(output);
  };

  return (
    <main style={{ maxWidth: 900, margin: "40px auto", padding: 20, fontFamily: "Inter, system-ui, Arial" }}>
      <h1 style={{ fontSize: 36, marginBottom: 6 }}>Contextify</h1>
      <p style={{ marginTop: 0, color: "#475569" }}>Make any document AI-ready</p>

      <input type="file" accept=".pdf" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <div style={{ marginTop: 12, display: "flex", gap: 10 }}>
        <button onClick={submit}>Convert</button>
        <button onClick={copy} disabled={!output}>Copy</button>
      </div>

      {tokens !== null && <p style={{ marginTop: 12 }}>Estimated tokens: {tokens}</p>}
      {err && <p style={{ marginTop: 12, color: "crimson" }}>{err}</p>}

      <textarea value={output} readOnly rows={18} style={{ width: "100%", marginTop: 14 }} />
    </main>
  );
}
