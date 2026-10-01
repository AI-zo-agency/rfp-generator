"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ConfirmDialogProvider, useConfirmDialog } from "@/components/ConfirmDialog";
import { kbBtnPrimary, kbBtnSecondary } from "@/lib/kb-brand";
import {
  addedMessage,
  canSetDefault,
  errorMessage,
  FILE_TOO_LARGE,
  formatAdded,
  MAX_FILE_BYTES,
  MAX_NOTES_CHARS,
  shortHash,
  showNoDefaultNote,
  suggestRevisionLabel,
  type BrandVoiceRevisionList,
} from "@/lib/brand-voice";
import { trackPageView } from "@/lib/zo-analytics";

async function failure(res: Response): Promise<string> {
  return errorMessage(await res.json().catch(() => null), res.status);
}

function networkFailure(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "Network error";
}

type Viewing = { label: string; body: string | null; error: string };

function RevisionViewer({ viewing, onClose }: { viewing: Viewing | null; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (viewing && !dlg.open) dlg.showModal();
    if (!viewing && dlg.open) dlg.close();
  }, [viewing]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={viewing ? `Brand voice ${viewing.label}` : "Brand voice"}
      className="m-auto w-[min(56rem,calc(100vw-2rem))] max-h-[85vh] rounded-xl border border-zo-border p-0 shadow-xl backdrop:bg-black/40"
    >
      {viewing ? (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-center justify-between gap-3 border-b border-zo-border px-5 py-3">
            <h2 className="font-heading text-lg text-foreground">{viewing.label}</h2>
            <button type="button" className={kbBtnSecondary} onClick={onClose}>
              Close
            </button>
          </div>
          <div className="overflow-y-auto px-5 py-4">
            {viewing.error ? (
              <p role="alert" className="text-sm text-red-800">{viewing.error}</p>
            ) : viewing.body === null ? (
              <p className="text-sm text-zo-text-muted">Loading…</p>
            ) : (
              <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed text-foreground">
                {viewing.body}
              </pre>
            )}
          </div>
        </div>
      ) : null}
    </dialog>
  );
}

function BrandVoiceInner() {
  const confirm = useConfirmDialog();
  const [data, setData] = useState<BrandVoiceRevisionList | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileKey, setFileKey] = useState(0);
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [viewing, setViewing] = useState<Viewing | null>(null);

  // Sets an error on failure but never clears one: callers reload after a failed action.
  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/brand-voice/revisions", { cache: "no-store" });
      if (!res.ok) {
        setError(await failure(res));
        return;
      }
      setData(await res.json());
    } catch (err) {
      setError(networkFailure(err));
    }
  }, []);

  useEffect(() => {
    trackPageView("/brand-voice");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void load();
  }, [load]);

  async function view(id: string, label: string) {
    setViewing({ label, body: null, error: "" });
    try {
      const res = await fetch(`/api/brand-voice/revisions/${encodeURIComponent(id)}`, { cache: "no-store" });
      if (!res.ok) {
        setViewing({ label, body: null, error: await failure(res) });
        return;
      }
      const json = (await res.json()) as { body?: string };
      setViewing({ label, body: json.body ?? "", error: "" });
    } catch (err) {
      setViewing({ label, body: null, error: networkFailure(err) });
    }
  }

  async function activate(id: string, name: string) {
    const ok = await confirm({
      title: `Use ${name} as the default?`,
      description:
        "New proposals will be written under this revision. Proposals that already exist keep the revision they were created under.",
      confirmLabel: "Set default",
    });
    if (!ok) return;
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const res = await fetch("/api/brand-voice/active", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revisionId: id }),
      });
      if (!res.ok) setError(await failure(res));
    } catch (err) {
      setError(networkFailure(err));
    }
    await load();
    setBusy(false);
  }

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    const name = label.trim();
    setBusy(true);
    setError("");
    setSuccess("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("label", name);
      form.append("notes", notes);
      const res = await fetch("/api/brand-voice/revisions", { method: "POST", body: form });
      if (!res.ok) {
        setError(await failure(res));
      } else {
        setFile(null);
        setFileKey((k) => k + 1); // remounts the file input so the same file can be picked again
        setLabel("");
        setNotes("");
        setSuccess(addedMessage(name));
      }
    } catch (err) {
      setError(networkFailure(err));
    }
    await load();
    setBusy(false);
  }

  const revisions = data?.revisions ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-8 px-6 py-8">
      <header className="border-l-[5px] border-[#ef5018] pl-8">
        <h1 className="font-heading text-3xl text-foreground">Brand voice</h1>
        <p className="mt-2 max-w-2xl text-sm text-zo-text-muted">
          Every revision of the zö Brand &amp; Writing Standards stays here. The default applies to
          new proposals. Each proposal keeps the revision it was created under.
        </p>
      </header>

      {error ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          <span>{error}</span>
          {!data ? (
            <button
              type="button"
              className={kbBtnSecondary}
              onClick={() => {
                setError("");
                void load();
              }}
            >
              Retry
            </button>
          ) : null}
        </div>
      ) : null}

      {success ? (
        <p
          role="status"
          className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800"
        >
          {success}
        </p>
      ) : null}

      {data && !data.enabled ? (
        <p className="rounded-lg border border-zo-border bg-[#fafbfc] px-4 py-3 text-sm text-zo-text-muted">
          This environment has no Supabase connection, so only the built-in file is available and
          revisions cannot be added.
        </p>
      ) : null}

      {data && showNoDefaultNote(data) ? (
        <p className="rounded-lg border border-zo-border bg-[#fafbfc] px-4 py-3 text-sm text-zo-text-muted">
          No default is set yet. New proposals use the repo copy of rev 6 until you set one below.
        </p>
      ) : null}

      <section className="zo-card overflow-x-auto p-0">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Brand voice revisions</caption>
          <thead className="border-b border-zo-border text-xs uppercase tracking-wide text-zo-text-muted">
            <tr>
              <th scope="col" className="px-4 py-3">Revision</th>
              <th scope="col" className="px-4 py-3">Added</th>
              <th scope="col" className="hidden px-4 py-3 md:table-cell">By</th>
              <th scope="col" className="hidden px-4 py-3 md:table-cell">Notes</th>
              <th scope="col" className="hidden px-4 py-3 md:table-cell">File</th>
              <th scope="col" className="px-4 py-3">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data?.enabled && revisions.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-4 py-6 text-center text-zo-text-muted">
                  No revisions stored yet. Add one below.
                </td>
              </tr>
            ) : null}
            {revisions.map((rev) => (
              <tr key={rev.id} className="border-b border-zo-border/60 last:border-0">
                <td className="px-4 py-3 font-semibold text-foreground">
                  {rev.label}
                  {rev.isActive ? (
                    <span className="ml-2 rounded-full bg-[rgba(239,80,24,0.12)] px-2 py-0.5 text-[11px] font-semibold text-[#ef5018]">
                      Default
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-3 text-zo-text-muted">
                  {formatAdded(rev.createdAt)}
                </td>
                <td className="hidden px-4 py-3 text-zo-text-muted md:table-cell">{rev.createdBy}</td>
                <td className="hidden px-4 py-3 text-zo-text-muted md:table-cell">{rev.notes}</td>
                <td className="hidden px-4 py-3 font-mono text-xs text-zo-text-muted md:table-cell">
                  {shortHash(rev.sha256)} · {Math.ceil(rev.size / 1024)} KB
                </td>
                <td className="space-x-2 whitespace-nowrap px-4 py-3 text-right">
                  <button type="button" className={kbBtnSecondary} onClick={() => void view(rev.id, rev.label)}>
                    View
                  </button>
                  {canSetDefault(rev, Boolean(data?.enabled)) ? (
                    <button
                      type="button"
                      className={kbBtnSecondary}
                      disabled={busy}
                      onClick={() => void activate(rev.id, rev.label)}
                    >
                      Set default
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <RevisionViewer viewing={viewing} onClose={() => setViewing(null)} />

      {data?.enabled ? (
        <form onSubmit={upload} className="zo-card space-y-4 p-6">
          <h2 className="font-heading text-xl text-foreground">Add a revision</h2>
          <label className="block text-sm">
            <span className="mb-1 block font-semibold text-foreground">Standards file (.md)</span>
            <input
              key={fileKey}
              type="file"
              accept=".md,text/markdown,text/plain"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setError("");
                setSuccess("");
                if (f && f.size > MAX_FILE_BYTES) {
                  setFile(null);
                  setError(FILE_TOO_LARGE);
                  return;
                }
                setFile(f);
                if (f && !label) setLabel(suggestRevisionLabel(f.name));
              }}
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-semibold text-foreground">Label</span>
            <input
              value={label}
              maxLength={40}
              onChange={(e) => {
                setError("");
                setLabel(e.target.value);
              }}
              placeholder="rev 7"
              className="w-full max-w-xs rounded-lg border border-zo-border px-3 py-2"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-semibold text-foreground">Notes (optional)</span>
            <input
              value={notes}
              maxLength={MAX_NOTES_CHARS}
              onChange={(e) => {
                setError("");
                setNotes(e.target.value);
              }}
              placeholder="Sent by the client on Sep 30"
              className="w-full rounded-lg border border-zo-border px-3 py-2"
            />
          </label>
          <button type="submit" className={kbBtnPrimary} disabled={busy || !file || !label.trim()}>
            Add revision
          </button>
          <p className="text-xs text-zo-text-muted">
            Adding a revision does not change any proposal. Set it as the default afterwards.
          </p>
        </form>
      ) : null}
    </div>
  );
}

export function BrandVoicePageContent() {
  return (
    <ConfirmDialogProvider>
      <BrandVoiceInner />
    </ConfirmDialogProvider>
  );
}
