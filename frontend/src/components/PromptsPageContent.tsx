"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronDown, History, RotateCcw, Sparkles } from "lucide-react";
import { ConfirmDialogProvider, useConfirmDialog } from "@/components/ConfirmDialog";
import { PromptDiff, changeSummary } from "@/components/PromptDiff";
import { errorMessage, formatAdded } from "@/lib/brand-voice";
import { kbBtnPrimary, kbBtnSecondary } from "@/lib/kb-brand";
import { trackPageView } from "@/lib/zo-analytics";

type PromptSummary = {
  key: string;
  group: string;
  name: string;
  description: string;
  source: string;
  isEdited: boolean;
  updatedAt: string;
  updatedBy: string | null;
};

type PromptDetail = PromptSummary & { body: string; defaultBody: string };

type Version = { id: number; body: string; note: string; updatedBy: string | null; updatedAt: string };

type Notice = { tone: "ok" | "error"; text: string };
type Guide = { key: string; what: string; when: string } | { key: string; loading: true };
type Suggestion = { revised: string; summary: string[] };

const focusRing =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#ef5018]";

const selectClass = `w-full appearance-none rounded-[var(--zo-radius-md)] border border-zo-border bg-white py-3 pl-4 pr-11 text-base text-foreground focus:border-[#ef5018] focus:outline-none disabled:cursor-not-allowed disabled:bg-[#f1f3f2] disabled:text-zo-text-muted ${focusRing}`;

async function failure(res: Response): Promise<string> {
  return errorMessage(await res.json().catch(() => null), res.status);
}

function networkFailure(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "Network error";
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function promptUrl(key: string): string {
  return `/api/prompts/${encodeURIComponent(key)}`;
}

function EditedBadge() {
  return (
    <span className="shrink-0 rounded-full bg-[rgba(239,80,24,0.12)] px-2.5 py-0.5 text-xs font-semibold text-[#b8360e]">
      Changed
    </span>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <h3 className="flex items-center gap-3 text-lg !font-semibold text-foreground">
        <span
          aria-hidden
          className="grid size-7 shrink-0 place-items-center rounded-full bg-[#ef5018] text-sm font-semibold text-white"
        >
          {n}
        </span>
        {title}
      </h3>
      {children}
    </section>
  );
}

function PickerField({
  id,
  label,
  value,
  onChange,
  disabled,
  placeholder,
  children,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  placeholder: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-semibold text-foreground">
        {label}
      </label>
      <div className="relative">
        <select
          id={id}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className={selectClass}
        >
          <option value="">{placeholder}</option>
          {children}
        </select>
        <ChevronDown
          className="pointer-events-none absolute right-4 top-1/2 size-5 -translate-y-1/2 text-zo-text-muted"
          aria-hidden
        />
      </div>
    </div>
  );
}

function PromptsInner() {
  const confirm = useConfirmDialog();
  const [prompts, setPrompts] = useState<PromptSummary[] | null>(null);
  const [group, setGroup] = useState("");
  const [selected, setSelected] = useState<PromptDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice | null>(null);
  const [guide, setGuide] = useState<Guide | null>(null);
  const [request, setRequest] = useState("");
  const [asking, setAsking] = useState(false);
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggestionError, setSuggestionError] = useState("");
  const [showText, setShowText] = useState(false);
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [comparing, setComparing] = useState<number | null>(null);
  const [pendingNote, setPendingNote] = useState("");
  const openSeq = useRef(0);
  const changesRef = useRef<HTMLDivElement>(null);

  const dirty = selected !== null && draft !== selected.body;
  const isOriginal = selected !== null && selected.body === selected.defaultBody;

  const loadList = useCallback(async () => {
    try {
      const res = await fetch("/api/prompts", { cache: "no-store" });
      if (!res.ok) {
        setLoadError(await failure(res));
        return;
      }
      setPrompts((await res.json()).prompts);
    } catch (err) {
      setLoadError(networkFailure(err));
    }
  }, []);

  useEffect(() => {
    trackPageView("/prompts");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount
    void loadList();
  }, [loadList]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const groups = useMemo(() => {
    const out = new Map<string, PromptSummary[]>();
    for (const prompt of prompts ?? []) out.set(prompt.group, [...(out.get(prompt.group) ?? []), prompt]);
    return [...out.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [prompts]);

  const inGroup = groups.find(([name]) => name === group)?.[1] ?? [];

  async function confirmDiscard(): Promise<boolean> {
    if (!dirty || !selected) return true;
    return confirm({
      title: "Leave without saving?",
      description: `You changed "${selected.name}" but have not saved it. Leaving now will lose your change.`,
      confirmLabel: "Leave without saving",
      tone: "danger",
    });
  }

  function show(detail: PromptDetail) {
    setSelected(detail);
    setDraft(detail.body);
    setPrompts((list) => list?.map((p) => (p.key === detail.key ? detail : p)) ?? list);
  }

  function clearWorkspace() {
    setNotice(null);
    setSuggestion(null);
    setSuggestionError("");
    setRequest("");
    setShowText(false);
    setGuide(null);
    setVersions(null);
    setHistoryOpen(false);
    setHistoryError("");
    setComparing(null);
    setPendingNote("");
  }

  async function pickGroup(next: string) {
    if (next === group) return;
    if (!(await confirmDiscard())) return;
    setGroup(next);
    setSelected(null);
    setDraft("");
    clearWorkspace();
  }

  async function pickPrompt(key: string) {
    if (!key) {
      if (!(await confirmDiscard())) return;
      setSelected(null);
      setDraft("");
      clearWorkspace();
      return;
    }
    if (key === selected?.key) return;
    if (!(await confirmDiscard())) return;
    const seq = ++openSeq.current;
    setBusy(true);
    clearWorkspace();
    try {
      const res = await fetch(promptUrl(key), { cache: "no-store" });
      if (seq !== openSeq.current) return;
      if (res.ok) {
        show(await res.json());
        setGuide({ key, loading: true });
        void loadGuide(key, seq);
      } else {
        setNotice({ tone: "error", text: await failure(res) });
      }
    } catch (err) {
      setNotice({ tone: "error", text: networkFailure(err) });
    }
    setBusy(false);
  }

  async function loadGuide(key: string, seq: number) {
    let next: Guide = { key, what: "", when: "" };
    try {
      const res = await fetch(`${promptUrl(key)}/guide`, { cache: "no-store" });
      if (res.ok) next = { key, ...(await res.json()) };
    } catch {
      // The guide is a convenience; the page works without it.
    }
    if (seq === openSeq.current) setGuide(next);
  }

  async function loadVersions(key: string) {
    setHistoryError("");
    try {
      const res = await fetch(`${promptUrl(key)}/versions`, { cache: "no-store" });
      if (!res.ok) {
        setHistoryError(await failure(res));
        return;
      }
      const data = await res.json();
      if (!data.available) setHistoryError("History is not switched on yet. Ask your developer to finish the setup.");
      setVersions(data.versions);
    } catch (err) {
      setHistoryError(networkFailure(err));
    }
  }

  function bringBack(version: Version) {
    setDraft(version.body);
    setPendingNote(version.id ? `Brought back the version from ${when(version.updatedAt)}` : "Went back to the original");
    setHistoryOpen(false);
    setNotice({ tone: "ok", text: "That version is loaded. Check what will change, then press Save changes." });
    requestAnimationFrame(() => changesRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }));
  }

  async function suggest() {
    if (!selected || !request.trim() || asking) return;
    setAsking(true);
    setSuggestion(null);
    setSuggestionError("");
    try {
      const res = await fetch(`${promptUrl(selected.key)}/assist`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: request, currentBody: draft }),
      });
      if (res.ok) setSuggestion(await res.json());
      else setSuggestionError(await failure(res));
    } catch (err) {
      setSuggestionError(networkFailure(err));
    }
    setAsking(false);
  }

  function useSuggestion() {
    if (!suggestion) return;
    setDraft(suggestion.revised);
    setPendingNote(suggestion.summary.length ? `Assistant: ${suggestion.summary.join("; ")}` : "Changed with the assistant");
    setSuggestion(null);
    setRequest("");
    setNotice({ tone: "ok", text: "The change is added below. Nothing is saved until you press Save changes." });
  }

  async function save() {
    if (!selected || !dirty || !draft.trim() || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await fetch(promptUrl(selected.key), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: draft, expectedUpdatedAt: selected.updatedAt, note: pendingNote }),
      });
      if (res.ok) {
        show(await res.json());
        setPendingNote("");
        if (historyOpen) void loadVersions(selected.key);
        else setVersions(null);
        setNotice({ tone: "ok", text: "Saved. The AI helpers will use this within a minute." });
      } else {
        setNotice({ tone: "error", text: await failure(res) });
      }
    } catch (err) {
      setNotice({ tone: "error", text: networkFailure(err) });
    }
    setBusy(false);
  }

  async function goBackToOriginal() {
    if (!selected) return;
    const ok = await confirm({
      title: "Go back to the original?",
      description: `This replaces "${selected.name}" with the version that came with the app. Your changes to it will be lost.`,
      confirmLabel: "Go back to the original",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(true);
    setNotice(null);
    try {
      const res = await fetch(`${promptUrl(selected.key)}/reset`, { method: "POST" });
      if (res.ok) {
        show(await res.json());
        setSuggestion(null);
        setPendingNote("");
        if (historyOpen) void loadVersions(selected.key);
        else setVersions(null);
        setNotice({ tone: "ok", text: "Back to the original." });
      } else {
        setNotice({ tone: "error", text: await failure(res) });
      }
    } catch (err) {
      setNotice({ tone: "error", text: networkFailure(err) });
    }
    setBusy(false);
  }

  const status = dirty
    ? { dot: "bg-[#e0a100]", text: "You have changes that are not saved yet" }
    : isOriginal
      ? { dot: "bg-zo-text-muted/40", text: "This is the original version" }
      : { dot: "bg-[#ef5018]", text: "Changed from the original and saved" };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-6 py-8">
      <header className="border-l-[5px] border-[#ef5018] pl-8">
        <h1 className="font-heading text-3xl text-foreground">Prompts</h1>
        <p className="mt-2 max-w-xl text-base leading-7 text-zo-text-muted">
          These are the instructions the AI helpers follow. Choose one, tell the assistant what you
          would like changed, and check the result before you save.
        </p>
      </header>

      {loadError ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          <span>{loadError}</span>
          {!prompts ? (
            <button
              type="button"
              className={kbBtnSecondary}
              onClick={() => {
                setLoadError("");
                void loadList();
              }}
            >
              Try again
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="zo-card space-y-8 p-6 sm:p-8">
        <Step n={1} title="Choose what to change">
          <div className="grid gap-4 sm:grid-cols-2">
            <PickerField
              id="prompt-group"
              label="Where it is used"
              value={group}
              onChange={(value) => void pickGroup(value)}
              disabled={!prompts}
              placeholder={prompts ? "Choose an area…" : "Loading…"}
            >
              {groups.map(([name, items]) => (
                <option key={name} value={name}>
                  {name} ({items.length})
                </option>
              ))}
            </PickerField>
            <PickerField
              id="prompt-name"
              label="Which one"
              value={selected?.key ?? ""}
              onChange={(value) => void pickPrompt(value)}
              disabled={!group}
              placeholder={group ? "Choose one…" : "Choose an area first"}
            >
              {inGroup.map((prompt) => (
                <option key={prompt.key} value={prompt.key}>
                  {prompt.name}
                  {prompt.isEdited ? " — changed" : ""}
                </option>
              ))}
            </PickerField>
          </div>
        </Step>

        {!selected ? (
          <p className="rounded-lg bg-[#f6f7f9] px-5 py-4 text-[15px] leading-6 text-zo-text-muted">
            {notice?.tone === "error"
              ? notice.text
              : "Pick an area and then one item to see what it does. You can always go back to the original."}
          </p>
        ) : (
          <>
            <div className="space-y-4 border-t border-zo-border pt-8">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="font-heading text-2xl text-foreground">{selected.name}</h2>
                {selected.isEdited ? <EditedBadge /> : null}
              </div>
              {guide && "loading" in guide ? (
                <div aria-hidden className="space-y-2">
                  <div className="h-5 w-4/5 animate-pulse rounded bg-[#f1f3f2]" />
                  <div className="h-5 w-3/5 animate-pulse rounded bg-[#f1f3f2]" />
                </div>
              ) : guide && (guide.what || guide.when) ? (
                <dl className="grid gap-4 rounded-lg bg-[#f6f7f9] p-5 text-[15px] leading-6 sm:grid-cols-2">
                  {guide.what ? (
                    <div>
                      <dt className="font-semibold text-foreground">What this does</dt>
                      <dd className="mt-1 text-zo-text-secondary">{guide.what}</dd>
                    </div>
                  ) : null}
                  {guide.when ? (
                    <div>
                      <dt className="font-semibold text-foreground">When to change it</dt>
                      <dd className="mt-1 text-zo-text-secondary">{guide.when}</dd>
                    </div>
                  ) : null}
                </dl>
              ) : null}
            </div>

            <Step n={2} title="Tell the assistant what you want changed">
              <label className="block">
                <span className="sr-only">What would you like changed?</span>
                <textarea
                  value={request}
                  onChange={(event) => setRequest(event.target.value)}
                  onKeyDown={(event) => {
                    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                      event.preventDefault();
                      void suggest();
                    }
                  }}
                  rows={3}
                  maxLength={1000}
                  placeholder={"Write it the way you would say it to a colleague.\nFor example: “Make the tone warmer.” or “Never mention prices here.”"}
                  className={`w-full resize-y rounded-[var(--zo-radius-md)] border border-zo-border bg-white p-4 text-base leading-7 text-foreground placeholder:text-zo-text-muted focus:border-[#ef5018] focus:outline-none ${focusRing}`}
                />
              </label>
              <button
                type="button"
                className={kbBtnPrimary}
                disabled={asking || !request.trim()}
                onClick={() => void suggest()}
              >
                <Sparkles className="size-4" aria-hidden />
                {asking ? "Thinking…" : "Suggest a change"}
              </button>

              {suggestionError ? (
                <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-[15px] leading-6 text-red-800">
                  {suggestionError}
                </p>
              ) : null}

              {suggestion ? (
                <div role="status" className="space-y-4 rounded-lg border border-[rgba(239,80,24,0.35)] bg-[#fff4ef] p-5">
                  <p className="font-semibold text-foreground">Here is what I would change:</p>
                  {suggestion.summary.length ? (
                    <ul className="list-disc space-y-1.5 pl-5 text-[15px] leading-6 text-zo-text-secondary">
                      {suggestion.summary.map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  ) : null}
                  <div className="flex flex-wrap gap-3">
                    <button type="button" className={kbBtnPrimary} onClick={useSuggestion}>
                      Use this change
                    </button>
                    <button type="button" className={kbBtnSecondary} onClick={() => setSuggestion(null)}>
                      Not quite
                    </button>
                  </div>
                  <p className="text-sm text-zo-text-muted">Using it does not save anything yet.</p>
                </div>
              ) : null}
            </Step>

            <Step n={3} title="Check it, then save">
              {notice ? (
                <p
                  role={notice.tone === "error" ? "alert" : "status"}
                  className={`rounded-lg border px-4 py-3 text-[15px] leading-6 ${
                    notice.tone === "error"
                      ? "border-red-200 bg-red-50 text-red-800"
                      : "border-green-200 bg-green-50 text-green-800"
                  }`}
                >
                  {notice.text}
                </p>
              ) : null}

              <details
                open={showText}
                onToggle={(event) => setShowText(event.currentTarget.open)}
                className="rounded-lg border border-zo-border"
              >
                <summary className={`cursor-pointer select-none rounded-lg px-5 py-3.5 text-[15px] font-semibold text-foreground ${focusRing}`}>
                  Read or edit the full text yourself
                </summary>
                <div className="space-y-2 border-t border-zo-border p-4">
                  <label className="block">
                    <span className="sr-only">Full text of {selected.name}</span>
                    <textarea
                      value={draft}
                      onChange={(event) => {
                        setDraft(event.target.value);
                        setPendingNote("");
                        setNotice(null);
                      }}
                      onKeyDown={(event) => {
                        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
                          event.preventDefault();
                          void save();
                        }
                      }}
                      spellCheck={false}
                      className={`h-96 w-full resize-y rounded-[var(--zo-radius-md)] border border-zo-border bg-[#fafbfc] p-4 font-mono text-sm leading-[1.7] text-foreground focus:border-[#ef5018] focus:bg-white focus:outline-none ${focusRing}`}
                    />
                  </label>
                  <p className="text-sm leading-5 text-zo-text-muted">
                    If you change the text yourself, keep any lines about how the answer must be laid
                    out, such as ones that mention JSON. The app reads the AI helper&apos;s reply using them.
                  </p>
                </div>
              </details>

              <p className="flex items-center gap-2.5 text-[15px] text-zo-text-secondary" aria-live="polite">
                <span aria-hidden className={`size-2.5 rounded-full ${status.dot}`} />
                {status.text}
                {!dirty && !isOriginal && selected.updatedBy
                  ? ` (${selected.updatedBy}, ${formatAdded(selected.updatedAt)})`
                  : ""}
              </p>

              {dirty ? (
                <div ref={changesRef} className="space-y-2 rounded-lg border border-[rgba(239,80,24,0.35)] bg-[#fff4ef] p-4">
                  <p className="font-semibold text-foreground">
                    What will change when you save{" "}
                    <span className="font-normal text-zo-text-secondary">({changeSummary(selected.body, draft)})</span>
                  </p>
                  <PromptDiff before={selected.body} after={draft} />
                  <p className="text-sm leading-5 text-zo-text-muted">
                    Saving changes how the AI helpers work for everyone, within about a minute. The earlier
                    version is kept, so you can bring it back.
                  </p>
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  className={kbBtnPrimary}
                  disabled={busy || !dirty || !draft.trim()}
                  onClick={() => void save()}
                >
                  {busy ? "Working…" : "Save changes"}
                </button>
                {dirty ? (
                  <button
                    type="button"
                    className={kbBtnSecondary}
                    disabled={busy}
                    onClick={() => {
                      setDraft(selected.body);
                      setNotice(null);
                    }}
                  >
                    Undo my changes
                  </button>
                ) : null}
                {!isOriginal ? (
                  <button
                    type="button"
                    className={`ml-auto inline-flex items-center gap-2 rounded-[var(--zo-radius-md)] px-3 py-2.5 text-sm font-semibold text-zo-text-secondary transition-colors hover:bg-[#f1f3f2] hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50 ${focusRing}`}
                    disabled={busy}
                    onClick={() => void goBackToOriginal()}
                  >
                    <RotateCcw className="size-4" aria-hidden />
                    Go back to the original
                  </button>
                ) : null}
              </div>

              <details
                open={historyOpen}
                onToggle={(event) => {
                  const open = event.currentTarget.open;
                  setHistoryOpen(open);
                  if (open && !versions) void loadVersions(selected.key);
                }}
                className="rounded-lg border border-zo-border"
              >
                <summary className={`flex cursor-pointer select-none items-center gap-2 rounded-lg px-5 py-3.5 text-[15px] font-semibold text-foreground ${focusRing}`}>
                  <History className="size-4" aria-hidden />
                  Earlier versions
                </summary>
                <div className="space-y-3 border-t border-zo-border p-4">
                  {historyError ? (
                    <p role="alert" className="text-[15px] text-red-800">{historyError}</p>
                  ) : !versions ? (
                    <p className="text-[15px] text-zo-text-muted">Loading…</p>
                  ) : (
                    <ul className="space-y-3">
                      {versions.map((version, index) => {
                        const before = versions[index + 1]?.body ?? selected.defaultBody;
                        const isCurrent = version.body === selected.body && index === 0;
                        return (
                          <li key={version.id} className="space-y-2 rounded-lg bg-[#f6f7f9] p-4">
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                              <span className="font-semibold text-foreground">{when(version.updatedAt)}</span>
                              <span className="text-sm text-zo-text-muted">{version.updatedBy ?? "Someone"}</span>
                              {isCurrent ? (
                                <span className="rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-semibold text-green-800">In use now</span>
                              ) : null}
                            </div>
                            <p className="text-[15px] leading-6 text-zo-text-secondary">
                              {version.note || "Edited by hand"} · {changeSummary(before, version.body)}
                            </p>
                            <div className="flex flex-wrap gap-3">
                              <button
                                type="button"
                                className={kbBtnSecondary}
                                onClick={() => setComparing(comparing === version.id ? null : version.id)}
                              >
                                {comparing === version.id ? "Hide what changed" : "See what changed"}
                              </button>
                              {!isCurrent ? (
                                <button type="button" className={kbBtnSecondary} onClick={() => bringBack(version)}>
                                  Bring this version back
                                </button>
                              ) : null}
                            </div>
                            {comparing === version.id ? <PromptDiff before={before} after={version.body} /> : null}
                          </li>
                        );
                      })}
                      <li className="space-y-2 rounded-lg bg-[#f6f7f9] p-4">
                        <p className="font-semibold text-foreground">The original</p>
                        <p className="text-[15px] leading-6 text-zo-text-secondary">The version that came with the app.</p>
                        {selected.body !== selected.defaultBody ? (
                          <button
                            type="button"
                            className={kbBtnSecondary}
                            onClick={() => bringBack({ id: 0, body: selected.defaultBody, note: "", updatedBy: null, updatedAt: "" })}
                          >
                            Bring this version back
                          </button>
                        ) : null}
                      </li>
                      {versions.length === 0 ? (
                        <p className="text-sm text-zo-text-muted">Nothing has been saved yet. Versions appear here after your first save.</p>
                      ) : null}
                    </ul>
                  )}
                </div>
              </details>
            </Step>
          </>
        )}
      </div>
    </div>
  );
}

export function PromptsPageContent() {
  return (
    <ConfirmDialogProvider>
      <PromptsInner />
    </ConfirmDialogProvider>
  );
}
