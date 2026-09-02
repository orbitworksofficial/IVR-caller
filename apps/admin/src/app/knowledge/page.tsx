"use client";

import { useEffect, useState, useRef } from "react";
import { Shell } from "@/components/Shell";
import {
  Alert, LoadingState, EmptyState, Modal, Spinner, formatDate,
} from "@/components/ui";
import { IconBook, IconPlus, IconTrash, IconUpload } from "@/components/Icons";
import { api, ApiError } from "@/lib/api";
import type { KnowledgeDocument } from "@/lib/types";

const CATEGORIES = ["faq", "services", "pricing", "policies", "company", "other"];

export default function KnowledgePage() {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  function load() {
    setLoading(true);
    api.listKnowledge()
      .then(setDocuments)
      .catch((e: ApiError) => setError(e.message))
      .finally(() => setLoading(false));
  }

  useEffect(load, []);

  async function remove(documentId: string, title: string | null) {
    if (!confirm(`Delete "${title || "this document"}" and all of its chunks?`)) return;
    try {
      await api.deleteKnowledge(documentId);
      setDocuments(documents.filter((d) => d.document_id !== documentId));
      setNotice("Document deleted.");
    } catch (e) {
      setError((e as ApiError).message);
    }
  }

  const totalChunks = documents.reduce((sum, d) => sum + d.chunk_count, 0);

  return (
    <Shell
      title="Knowledge base"
      subtitle="The only source of facts the bot may use when answering."
      actions={
        <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
          <IconPlus /> Add knowledge
        </button>
      }
    >
      {error && <Alert kind="error">{error}</Alert>}
      {notice && <Alert kind="success">{notice}</Alert>}

      {loading ? (
        <LoadingState label="Loading knowledge base…" />
      ) : documents.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<IconBook />}
            title="No knowledge yet"
            text="The bot will decline to answer until you add content. Upload your FAQs, service descriptions, pricing and policies."
            action={
              <button className="btn btn-primary" onClick={() => setShowAdd(true)}>
                <IconPlus /> Add your first document
              </button>
            }
          />
        </div>
      ) : (
        <>
          <div className="grid grid-3" style={{ marginBottom: 16 }}>
            <div className="card stat">
              <div className="stat-label">Documents</div>
              <div className="stat-value">{documents.length}</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Chunks indexed</div>
              <div className="stat-value">{totalChunks}</div>
              <div className="stat-hint">Retrievable passages</div>
            </div>
            <div className="card stat">
              <div className="stat-label">Categories</div>
              <div className="stat-value">
                {new Set(documents.map((d) => d.category).filter(Boolean)).size}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Title</th><th>Category</th><th>Chunks</th>
                    <th>Added</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {documents.map((doc) => (
                    <tr key={doc.document_id}>
                      <td>
                        <div className="cell-strong">{doc.title || "Untitled"}</div>
                        {doc.source_url && (
                          <a href={doc.source_url} target="_blank"
                             rel="noopener noreferrer"
                             style={{ fontSize: 12 }}>
                            {doc.source_url}
                          </a>
                        )}
                      </td>
                      <td>
                        <span className="badge badge-neutral">
                          {doc.category || "uncategorized"}
                        </span>
                      </td>
                      <td className="cell-muted">{doc.chunk_count}</td>
                      <td className="cell-muted">{formatDate(doc.created_at)}</td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => remove(doc.document_id, doc.title)}
                          aria-label="Delete document"
                        >
                          <IconTrash />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {showAdd && (
        <AddKnowledgeModal
          onClose={() => setShowAdd(false)}
          onAdded={(message) => {
            setShowAdd(false);
            setNotice(message);
            load();
          }}
        />
      )}
    </Shell>
  );
}

function AddKnowledgeModal({
  onClose, onAdded,
}: {
  onClose: () => void;
  onAdded: (message: string) => void;
}) {
  const [mode, setMode] = useState<"paste" | "upload">("paste");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState("faq");
  const [sourceUrl, setSourceUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode === "paste") {
        const result = await api.ingestText({
          title: title.trim(),
          content: content.trim(),
          category,
          source_url: sourceUrl.trim() || undefined,
        });
        onAdded(`"${result.title}" ingested — ${result.chunks_created} chunks indexed.`);
      } else {
        if (!file) { setError("Choose a file to upload."); setBusy(false); return; }
        const result = await api.uploadKnowledge(
          file, title.trim() || file.name, category
        );
        onAdded(`"${result.title}" ingested — ${result.chunks_created} chunks indexed.`);
      }
    } catch (e) {
      setError((e as ApiError).message);
      setBusy(false);
    }
  }

  const canSubmit = mode === "paste"
    ? title.trim().length > 0 && content.trim().length >= 20
    : file !== null;

  return (
    <Modal
      title="Add knowledge"
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit}
                  disabled={busy || !canSubmit}>
            {busy ? <><Spinner /> Ingesting…</> : "Ingest"}
          </button>
        </>
      }
    >
      {error && <Alert kind="error">{error}</Alert>}

      <div className="tabs" style={{ marginBottom: 16 }}>
        <button className={`tab${mode === "paste" ? " active" : ""}`}
                onClick={() => setMode("paste")}>Paste text</button>
        <button className={`tab${mode === "upload" ? " active" : ""}`}
                onClick={() => setMode("upload")}>Upload file</button>
      </div>

      {mode === "paste" ? (
        <>
          <div className="field">
            <label className="label">Title</label>
            <input className="input" value={title}
                   onChange={(e) => setTitle(e.target.value)}
                   placeholder="Pricing and packages" autoFocus />
          </div>

          <div className="field">
            <label className="label">Content</label>
            <textarea className="textarea" value={content}
                      onChange={(e) => setContent(e.target.value)}
                      style={{ minHeight: 180 }}
                      placeholder="Paste your FAQ, service descriptions, pricing or policies here…" />
            <p className="hint">
              {content.trim().length} characters — at least 20 required.
            </p>
          </div>

          <div className="field">
            <label className="label">Source URL (optional)</label>
            <input className="input" value={sourceUrl}
                   onChange={(e) => setSourceUrl(e.target.value)}
                   placeholder="https://yoursite.com/pricing" />
          </div>
        </>
      ) : (
        <>
          <div className="field">
            <label className="label">File</label>
            <input
              ref={fileRef}
              className="input"
              type="file"
              accept=".pdf,.txt,.md,.csv"
              onChange={(e) => {
                const chosen = e.target.files?.[0] ?? null;
                setFile(chosen);
                if (chosen && !title) setTitle(chosen.name.replace(/\.[^.]+$/, ""));
              }}
            />
            <p className="hint">PDF, TXT, Markdown or CSV — up to 10MB.</p>
          </div>

          {file && (
            <div className="alert alert-info" style={{
              display: "flex", alignItems: "center", gap: 8,
            }}>
              <IconUpload />
              <span>{file.name} — {(file.size / 1024).toFixed(0)} KB</span>
            </div>
          )}

          <div className="field">
            <label className="label">Title</label>
            <input className="input" value={title}
                   onChange={(e) => setTitle(e.target.value)}
                   placeholder="Defaults to the filename" />
          </div>
        </>
      )}

      <div className="field">
        <label className="label">Category</label>
        <select className="select" value={category}
                onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <p className="hint">
          Used for filtering and traceability during retrieval.
        </p>
      </div>
    </Modal>
  );
}
