"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  fetchKeyPersonas,
  saveProposalKeyPersonas,
  setKeyPersonaRetired,
  type KeyPersonaItem,
} from "@/lib/proposal-api";
import type { ProposalOutline } from "@/types/proposal";

interface KeyPersonasModalProps {
  isOpen: boolean;
  onClose: () => void;
  rfpId?: string;
  initialSelectedIds?: string[];
  onSelectionChange?: (selectedPersonaIds: string[]) => void;
  onDraftSynced?: (draft: ProposalOutline) => void;
}

export function KeyPersonasModal({
  isOpen,
  onClose,
  rfpId,
  initialSelectedIds = [],
  onSelectionChange,
  onDraftSynced,
}: KeyPersonasModalProps) {
  const [personas, setPersonas] = useState<KeyPersonaItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>(initialSelectedIds);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const userModifiedRef = useRef(false);
  // Latest selection — toggle/save must not use a stale React closure when the
  // user clicks several checkboxes quickly (that was dropping the 4th pick).
  const selectedIdsRef = useRef<string[]>(initialSelectedIds);
  const saveSeqRef = useRef(0);

  const applySelectedIds = useCallback((next: string[]) => {
    selectedIdsRef.current = next;
    setSelectedIds(next);
  }, []);

  // Sync initial selected IDs ONLY when modal opens
  useEffect(() => {
    if (isOpen) {
      userModifiedRef.current = false;
      applySelectedIds(initialSelectedIds || []);
    }
  }, [isOpen, applySelectedIds]);

  // ESC key handler
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  const loadPersonas = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchKeyPersonas(rfpId);
      if (data.personas && data.personas.length > 0) {
        setPersonas(data.personas);
      }
      // Sync local modal checkboxes from server — do NOT push into parent on
      // load (that was re-saving stale picks). Parent owns selection via draft.
      if (Array.isArray(data.selectedPersonaIds) && !userModifiedRef.current) {
        applySelectedIds(data.selectedPersonaIds);
      }
    } catch {
      // Keep existing list
    } finally {
      setLoading(false);
    }
  }, [rfpId, applySelectedIds]);

  useEffect(() => {
    if (isOpen) {
      void loadPersonas();
    }
  }, [isOpen, loadPersonas]);

  const persistSelection = useCallback(
    async (nextSelected: string[]) => {
      userModifiedRef.current = true;
      applySelectedIds(nextSelected);
      onSelectionChange?.(nextSelected);
      if (!rfpId) return;

      const seq = ++saveSeqRef.current;
      setSaving(true);
      setSaveSuccess(false);
      try {
        const result = await saveProposalKeyPersonas(rfpId, nextSelected);
        // Ignore outdated responses so an earlier toggle cannot overwrite a
        // newer 4-person save with a 3-person payload.
        if (seq !== saveSeqRef.current) return;
        if (result.ok) {
          setSaveSuccess(true);
          window.setTimeout(() => setSaveSuccess(false), 2000);
        }
        if (result.draft && result.biosSynced) {
          onDraftSynced?.(result.draft);
        }
      } catch {
        // silent fallback
      } finally {
        if (seq === saveSeqRef.current) {
          setSaving(false);
        }
      }
    },
    [rfpId, onSelectionChange, onDraftSynced, applySelectedIds]
  );

  const togglePersona = useCallback(
    (id: string) => {
      const person = personas.find((p) => p.id === id);
      if (person?.retired) return;
      const current = selectedIdsRef.current;
      const exists = current.includes(id);
      const next = exists
        ? current.filter((item) => item !== id)
        : [...current, id];
      void persistSelection(next);
    },
    [persistSelection, personas]
  );

  const toggleRetired = useCallback(
    async (person: KeyPersonaItem, retired: boolean) => {
      const updated = await setKeyPersonaRetired({
        personId: person.id,
        name: person.name,
        retired,
      });
      if (updated?.personas) {
        setPersonas(updated.personas);
      } else {
        setPersonas((prev) =>
          prev.map((p) => (p.id === person.id ? { ...p, retired } : p))
        );
      }
      if (retired) {
        const next = selectedIdsRef.current.filter((id) => id !== person.id);
        void persistSelection(next);
      }
    },
    [persistSelection]
  );

  const selectAll = useCallback(() => {
    const allIds = personas.filter((p) => !p.retired).map((p) => p.id);
    void persistSelection(allIds);
  }, [personas, persistSelection]);

  const clearAll = useCallback(() => {
    void persistSelection([]);
  }, [persistSelection]);

  const filteredPersonas = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const matched = !q
      ? personas
      : personas.filter(
          (p) =>
            p.name.toLowerCase().includes(q) ||
            p.title.toLowerCase().includes(q) ||
            p.sourceFile.toLowerCase().includes(q)
        );
    return [...matched].sort((a, b) => Number(Boolean(a.retired)) - Number(Boolean(b.retired)));
  }, [personas, searchQuery]);

  if (!isOpen) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="key-personas-modal-title"
    >
      {/* Backdrop */}
      <button
        type="button"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        aria-label="Close dialog"
        onClick={onClose}
      />

      {/* Modal Card */}
      <div className="relative z-10 flex max-h-[min(90dvh,720px)] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-[#e5e7eb] bg-white shadow-2xl">
        
        {/* Header */}
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-[#e5e7eb] px-6 py-5 md:px-8 bg-white">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#ef5018]">
              TEAM RESUMES & BIOS
            </p>
            <h2
              id="key-personas-modal-title"
              className="mt-1 text-2xl font-bold text-[#111827]"
            >
              Key Persons
            </h2>
            <p className="mt-1 text-xs text-[#4b5563]">
              Select current staff whose Knowledge Base resumes go in this proposal.
              Mark someone Retired so Go/No-Go and proposal agents never assign them.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#e5e7eb] bg-[#f9fafb] text-[#374151] hover:bg-[#e5e7eb] transition-smooth"
            aria-label="Close"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Toolbar & Search */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#e5e7eb] bg-[#f9fafb] px-6 py-3 md:px-8">
          <div className="relative flex-1 min-w-[200px]">
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search team member or role…"
              className="w-full rounded-xl border border-[#d1d5db] bg-white px-3.5 py-2 text-xs text-[#111827] placeholder-[#9ca3af] outline-none focus:border-[#ef5018] focus:ring-2 focus:ring-[#ef5018]/15"
            />
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs font-bold text-[#ef5018]">
              {selectedIds.length} of {personas.length} selected
            </span>
            {saving ? (
              <span className="text-[11px] text-[#6b7280]">Saving…</span>
            ) : saveSuccess ? (
              <span className="text-[11px] font-bold text-emerald-600">Saved ✓</span>
            ) : null}
            <button
              type="button"
              onClick={selectAll}
              className="rounded-lg border border-[#d1d5db] bg-white px-3 py-1.5 text-xs font-semibold text-[#111827] hover:bg-[#f3f4f6] transition-smooth"
            >
              Select All
            </button>
            <button
              type="button"
              onClick={clearAll}
              className="rounded-lg border border-[#d1d5db] bg-white px-3 py-1.5 text-xs font-semibold text-[#4b5563] hover:bg-[#f3f4f6] transition-smooth"
            >
              Clear
            </button>
          </div>
        </div>

        {/* List of Personas */}
        <div className="custom-scrollbar flex-1 overflow-y-auto p-6 bg-white md:p-8">
          {filteredPersonas.length === 0 ? (
            <p className="py-12 text-center text-xs text-[#6b7280]">
              No team members found matching &ldquo;{searchQuery}&rdquo;.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {filteredPersonas.map((person) => {
                const isRetired = Boolean(person.retired);
                const isSelected = !isRetired && selectedIds.includes(person.id);
                return (
                  <label
                    key={person.id}
                    htmlFor={`persona-modal-${person.id}`}
                    className={`group relative flex items-start gap-3.5 rounded-xl border p-4 transition-smooth ${
                      isRetired
                        ? "cursor-not-allowed border-[#e5e7eb] bg-[#f9fafb] opacity-80"
                        : isSelected
                          ? "cursor-pointer border-[#ef5018] bg-[#fef2f2]/60 shadow-xs"
                          : "cursor-pointer border-[#e5e7eb] bg-white hover:border-[#ef5018]/50 hover:bg-[#f9fafb]"
                    }`}
                  >
                    <input
                      type="checkbox"
                      id={`persona-modal-${person.id}`}
                      checked={isSelected}
                      disabled={isRetired}
                      onChange={() => togglePersona(person.id)}
                      className="mt-1 h-4.5 w-4.5 shrink-0 rounded border-[#d1d5db] text-[#ef5018] accent-[#ef5018] focus:ring-[#ef5018]/20 cursor-pointer disabled:cursor-not-allowed"
                    />

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-1">
                        <span className="text-sm font-bold text-[#111827] group-hover:text-[#ef5018]">
                          {person.name}
                        </span>
                        <span className="flex shrink-0 items-center gap-1">
                          {isRetired ? (
                            <span className="rounded-full border border-[#e5e7eb] bg-[#f3f4f6] px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide text-[#6b7280]">
                              Retired
                            </span>
                          ) : person.hasResume ? (
                            <span className="rounded-full border border-[#a7f3d0] bg-[#ecfdf5] px-2 py-0.5 text-[9px] font-bold text-[#047857]">
                              Resume KB
                            </span>
                          ) : null}
                        </span>
                      </div>

                      <p className="mt-0.5 text-xs font-semibold text-[#4b5563] truncate">
                        {person.title}
                      </p>

                      {person.bioSnippet ? (
                        <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-[#6b7280]">
                          {person.bioSnippet}
                        </p>
                      ) : null}

                      {person.sourceFile ? (
                        <p className="mt-1.5 font-mono text-[9px] text-[#9ca3af] truncate">
                          📄 {person.sourceFile}
                        </p>
                      ) : null}

                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          void toggleRetired(person, !isRetired);
                        }}
                        className="mt-2 text-[10px] font-semibold text-[#6b7280] underline-offset-2 hover:text-[#111827] hover:underline"
                      >
                        {isRetired ? "Mark current staff" : "Mark retired"}
                      </button>
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex shrink-0 items-center justify-between border-t border-[#e5e7eb] bg-[#f9fafb] px-6 py-4 md:px-8">
          <p className="text-xs font-medium text-[#4b5563]">
            <strong className="font-bold text-[#111827]">{selectedIds.length}</strong> persona{selectedIds.length === 1 ? "" : "s"} selected for proposal bio sections.
          </p>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-[#ef5018] px-7 py-2.5 text-xs font-bold uppercase tracking-wider text-white shadow-md shadow-[#ef5018]/20 hover:bg-[#d94411] active:scale-95 transition-smooth cursor-pointer"
          >
            Done
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
