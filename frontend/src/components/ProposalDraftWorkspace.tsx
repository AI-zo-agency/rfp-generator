"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  buildDefaultOutline,
  countWords,
  countSectionsWithContent,
  createCustomSection,
  isLikelyWipedOutline,
  rebuildOutlineFromResearch,
  staticSections1to3Complete,
  stripLegacyMonolithSections,
} from "@/lib/proposal-draft";
import { getManuscriptSections, normalizeOutlineSectionOrder, resolveManuscriptJumpTarget, buildManuscriptIndexMap } from "@/lib/proposal-outline-tree";
import {
  buildPipelineStatus,
  fetchProposalDraft,
  generateFullProposalStaged,
  generateProposalSections1to3,
  pipelineResumeMessage,
  PROPOSAL_INITIAL_LOAD_TIMEOUT_MS,
  recoverProposalDraftIfSaved,
  resetProposal,
  runPhase3Drafting,
  runPhase3_5BudgetWithRecovery,
  runPhase3_6SelfEditWithRecovery,
  runPhase4PreSubmitReview,
  runPhase4FinalizeGaps,
  runFulfillRfpGaps,
  restoreProposalSnapshot,
  stopProposalGeneration,
  downloadProposalDocx,
  saveProposalDraft,
  startLiveDraftPolling,
  fullProposalProgressFromInFlight,
  type FullProposalProgress,
  type ProposalPipelineStatus,
} from "@/lib/proposal-api";
import type { OutlineSection, ProposalBudget, ProposalOutline, ProposalResearch, PreSubmitReview } from "@/types/proposal";
import type { RfpRecord } from "@/types/rfp";
import { ProposalSectionTree } from "./ProposalSectionTree";
import { SectionStatusPill } from "./SectionStatusPill";
import { MarkdownReportBody, stripManuscriptDisplayArtifacts } from "./MarkdownReportBody";
import { DraftSectionEditor, type SectionRevisionRecord } from "./DraftSectionEditor";
import {
  ProposalSectionChatPanel,
  type SectionChatMessage,
  type SectionChatReference,
} from "./ProposalSectionChatPanel";
import { SectionRevisionCompare } from "./SectionRevisionCompare";
import { ProposalManualFlagsPanel } from "./ProposalManualFlagsPanel";
import { ProposalPipelineProgressStrip } from "./ProposalPipelineProgressStrip";
import { ProposalVersionCompare } from "./ProposalVersionCompare";
import { OutlineTabs, TabPanel } from "./ui/OutlineTabs";
import {
  scanSubmissionFlags,
  mergeSubmissionFlags,
  resolveFlagHighlight,
  sectionManualFillCount,
  summarizeManualFillFlags,
  type FlagHighlightRange,
  type ManualFillFlag,
} from "@/lib/proposal-manual-flags";
import {
  phaseIsComplete,
  FULFILL_SCAN_PHASE,
  pipelineServerStillWorkingMessage,
  inProgressPhaseLabel,
} from "@/lib/proposal-pipeline-checkpoint";

type WorkspaceTab = "outline" | "content" | "export";

function prepareOutline(draft: ProposalOutline): ProposalOutline {
  const cleaned: ProposalOutline = {
    ...draft,
    sections: draft.sections.map((s) => ({
      ...s,
      content: s.content
        ? stripManuscriptDisplayArtifacts(s.content)
        : s.content,
    })),
  };
  return normalizeOutlineSectionOrder(
    stripLegacyMonolithSections(cleaned),
  ) as ProposalOutline;
}

type SectionRevisionMap = Record<string, SectionRevisionRecord>;

function revisionsStorageKey(rfpId: string): string {
  return `zo-proposal-section-revisions:${rfpId}`;
}

function loadStoredRevisions(rfpId: string): SectionRevisionMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = sessionStorage.getItem(revisionsStorageKey(rfpId));
    if (!raw) return {};
    const parsed = JSON.parse(raw) as SectionRevisionMap;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function persistStoredRevisions(rfpId: string, revisions: SectionRevisionMap): void {
  if (typeof window === "undefined") return;
  try {
    if (Object.keys(revisions).length === 0) {
      sessionStorage.removeItem(revisionsStorageKey(rfpId));
      return;
    }
    sessionStorage.setItem(revisionsStorageKey(rfpId), JSON.stringify(revisions));
  } catch {
    // ignore quota errors
  }
}

const baseWorkspaceTabs = [
  { id: "outline", label: "Sections" },
  { id: "content", label: "Review" },
  { id: "export", label: "Submit" },
];

function getProposalPlainStatus(options: {
  fullProposalDone: boolean;
  manuscriptComplete: boolean;
  manualFillCount: number;
  reviewCriticalCount: number;
  readyToSubmit: boolean;
  hasEndingReport: boolean;
  isGenerating: boolean;
}): { headline: string; tone: "neutral" | "action" | "good" } {
  if (options.isGenerating) {
    return { headline: "Writing your proposal…", tone: "neutral" };
  }
  if (!options.fullProposalDone && !options.manuscriptComplete) {
    return {
      headline: "Not started — click Generate Proposal to build the draft.",
      tone: "action",
    };
  }
  if (options.manualFillCount > 0) {
    return {
      headline: `${options.manualFillCount} item${options.manualFillCount === 1 ? "" : "s"} need your input — see Checklist on Review.`,
      tone: "action",
    };
  }
  if (options.reviewCriticalCount > 0) {
    return {
      headline: `${options.reviewCriticalCount} issue${options.reviewCriticalCount === 1 ? "" : "s"} to review before submit.`,
      tone: "action",
    };
  }
  if (options.readyToSubmit) {
    return { headline: "Ready to export and submit.", tone: "good" };
  }
  if (options.hasEndingReport) {
    return {
      headline: "Draft complete — open Submit for the final checklist.",
      tone: "neutral",
    };
  }
  return { headline: "Draft complete — read Review, then Submit.", tone: "good" };
}

function IconButton({
  onClick,
  label,
  children,
  variant = "default",
}: {
  onClick: () => void;
  label: string;
  children: React.ReactNode;
  variant?: "default" | "danger";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={`flex h-9 w-9 items-center justify-center rounded-lg border transition-smooth ${
        variant === "danger"
          ? "border-red-200 text-zo-error hover:bg-red-50"
          : "border-zo-border text-zo-text-secondary hover:border-zo-teal hover:bg-[var(--zo-hover-bg)] hover:text-zo-orange"
      }`}
    >
      {children}
    </button>
  );
}

interface ProposalDraftWorkspaceProps {
  rfp: RfpRecord;
  goRfpCount?: number;
  onOpenGoRfpPicker?: () => void;
}

export function ProposalDraftWorkspace({
  rfp,
  goRfpCount,
  onOpenGoRfpPicker,
}: ProposalDraftWorkspaceProps) {
  const [outline, setOutline] = useState<ProposalOutline>(() =>
    buildDefaultOutline(rfp)
  );
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("outline");
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(
    null
  );
  const [isFullProposalRunning, setIsFullProposalRunning] = useState(false);
  const [fullProposalProgress, setFullProposalProgress] =
    useState<FullProposalProgress | null>(null);
  const [liveGeneratedCount, setLiveGeneratedCount] = useState(0);
  const [liveLatestSectionTitle, setLiveLatestSectionTitle] = useState<string | null>(
    null
  );
  const [isPricingRunning, setIsPricingRunning] = useState(false);
  const [isRefiningBudget, setIsRefiningBudget] = useState(false);
  const [isFinalizingGaps, setIsFinalizingGaps] = useState(false);
  const [isFulfillingRfpGaps, setIsFulfillingRfpGaps] = useState(false);
  const [isRestoringSnapshot, setIsRestoringSnapshot] = useState(false);
  const [restoreSnapshotAt, setRestoreSnapshotAt] = useState("");
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [isResettingDraft, setIsResettingDraft] = useState(false);
  const [gapResolveNotice, setGapResolveNotice] = useState<string | null>(null);
  const [gapResolveError, setGapResolveError] = useState<string | null>(null);
  const [presubmitReview, setPresubmitReview] = useState<PreSubmitReview | null>(null);
  const [showManualFlags, setShowManualFlags] = useState(false);
  const [highlightedSectionId, setHighlightedSectionId] = useState<string | null>(null);
  const [activeSubmissionFlag, setActiveSubmissionFlag] = useState<ManualFillFlag | null>(null);
  const [budget, setBudget] = useState<ProposalBudget | null>(null);
  const [research, setResearch] = useState<ProposalResearch | null>(null);
  const [newSectionTitle, setNewSectionTitle] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [draftLoadState, setDraftLoadState] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const [isDownloadingDocx, setIsDownloadingDocx] = useState(false);
  const [docxDownloadError, setDocxDownloadError] = useState<string | null>(null);
  const [docxDownloaded, setDocxDownloaded] = useState(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [generateNotice, setGenerateNotice] = useState<string | null>(null);
  const [provider, setProvider] = useState<string | null>(null);
  const [pipelineStatus, setPipelineStatus] =
    useState<ProposalPipelineStatus | null>(null);
  const skipNextSaveRef = useRef(false);
  const saveGenerationRef = useRef(0);
  const fullProposalAbortRef = useRef<AbortController | null>(null);
  const fulfillAbortRef = useRef<AbortController | null>(null);
  const editorScrollRef = useRef<HTMLDivElement>(null);
  const sectionButtonRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const contentScrollRef = useRef<HTMLDivElement | null>(null);
  const submitScrollRef = useRef<HTMLDivElement | null>(null);
  const liveContentFingerprintRef = useRef<Map<string, number>>(new Map());
  const outlineRef = useRef(outline);
  useEffect(() => {
    outlineRef.current = outline;
  }, [outline]);
  const [sectionRevisions, setSectionRevisions] = useState<SectionRevisionMap>({});
  const [revisionDrawerSectionId, setRevisionDrawerSectionId] = useState<string | null>(
    null
  );
  const [sectionChatReference, setSectionChatReference] = useState<SectionChatReference | null>(
    null
  );
  const [sectionChatBusy, setSectionChatBusy] = useState(false);
  const [sectionChatMessages, setSectionChatMessages] = useState<SectionChatMessage[]>([]);
  const assistantPaneRef = useRef<HTMLDivElement>(null);

  const openSectionChat = useCallback((request?: SectionChatReference | null) => {
    if (request) {
      setSectionChatReference(request);
      window.setTimeout(() => {
        assistantPaneRef.current?.scrollIntoView({
          behavior: "smooth",
          block: "nearest",
        });
      }, 40);
    } else {
      setSectionChatReference(null);
    }
  }, []);

  const applyOutlineFromServer = useCallback((draft: ProposalOutline) => {
    saveGenerationRef.current += 1;
    skipNextSaveRef.current = true;
    setOutline(prepareOutline(draft));
  }, []);

  const recordSectionRevision = useCallback(
    (sectionId: string, revision: SectionRevisionRecord) => {
      setSectionRevisions((prev) => {
        const next = { ...prev, [sectionId]: revision };
        persistStoredRevisions(rfp.id, next);
        return next;
      });
    },
    [rfp.id]
  );

  const dismissSectionRevision = useCallback(
    (sectionId: string) => {
      setSectionRevisions((prev) => {
        const next = { ...prev };
        delete next[sectionId];
        persistStoredRevisions(rfp.id, next);
        return next;
      });
      setRevisionDrawerSectionId((current) => (current === sectionId ? null : current));
    },
    [rfp.id]
  );

  const applySectionImproveFromServer = useCallback(
    (updatedDraft: ProposalOutline, updatedResearch: ProposalResearch | null) => {
      // Backend already persisted the improved manuscript + After snapshot.
      // Do not PUT again — a slim client save can race and drop chat content.
      applyOutlineFromServer(updatedDraft);
      if (updatedResearch) {
        setResearch(updatedResearch);
        if (updatedResearch.budget) {
          setBudget(updatedResearch.budget);
        }
      }
      const authoritativeIds = new Set(
        updatedDraft.sections.map((section) => section.id)
      );
      const authoritativeCount = updatedDraft.sections.length;
      void fetchProposalDraft(rfp.id).then((snap) => {
        if (!snap.draft) return;
        const snapIds = new Set(snap.draft.sections.map((section) => section.id));
        const missing = [...authoritativeIds].filter((id) => !snapIds.has(id));
        // Stale autosave can wipe sections the improve just added — push back.
        if (
          missing.length > 0 ||
          snap.draft.sections.length < authoritativeCount
        ) {
          const repaired: ProposalOutline = {
            ...updatedDraft,
            updatedAt: new Date().toISOString(),
          };
          applyOutlineFromServer(repaired);
          void saveProposalDraft(rfp.id, repaired);
          return;
        }
        applyOutlineFromServer(snap.draft);
        if (snap.research) {
          setResearch(snap.research);
          if (snap.research.budget) {
            setBudget(snap.research.budget);
          }
        }
      });
    },
    [applyOutlineFromServer, rfp.id]
  );

  useEffect(() => {
    const revisions = loadStoredRevisions(rfp.id);
    queueMicrotask(() => {
      setSectionRevisions(revisions);
      setRevisionDrawerSectionId(null);
    });
  }, [rfp.id]);

  const activeRevision =
    revisionDrawerSectionId && sectionRevisions[revisionDrawerSectionId]
      ? sectionRevisions[revisionDrawerSectionId]
      : null;

  const revisionDrawerSection = revisionDrawerSectionId
    ? outline.sections.find((s) => s.id === revisionDrawerSectionId) ?? null
    : null;

  useEffect(() => {
    if (!selectedSectionId) return;
    sectionButtonRefs.current
      .get(selectedSectionId)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    editorScrollRef.current?.scrollTo({ top: 0 });
  }, [selectedSectionId]);

  const selectSection = useCallback((id: string) => {
    setSelectedSectionId(id);
    setActiveSubmissionFlag((current) =>
      current && current.sectionId !== id ? null : current
    );
    // Always land in the editor for the clicked section — including empty ones.
    setActiveTab("outline");
  }, []);

  const scrollToManuscriptSection = useCallback((requestedId: string) => {
    const targetId = resolveManuscriptJumpTarget(outline.sections, requestedId);
    setActiveSubmissionFlag((current) =>
      current && current.sectionId !== targetId ? null : current
    );
    setHighlightedSectionId(targetId);
    setSelectedSectionId(targetId);

    const scroller =
      activeTab === "export"
        ? submitScrollRef.current
        : contentScrollRef.current;
    const target = document.getElementById(targetId);
    if (!target) {
      // Empty sections may not be mounted on Submit — open Sections editor instead.
      setActiveTab("outline");
      window.setTimeout(() => setHighlightedSectionId(null), 2200);
      return;
    }
    window.setTimeout(() => setHighlightedSectionId(null), 2200);
    const scrollableScroller =
      scroller && scroller.scrollHeight > scroller.clientHeight + 2;
    if (scrollableScroller && scroller) {
      const sRect = scroller.getBoundingClientRect();
      const tRect = target.getBoundingClientRect();
      scroller.scrollTo({
        top: scroller.scrollTop + (tRect.top - sRect.top) - 20,
        behavior: "smooth",
      });
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [outline.sections, activeTab]);

  const handleJumpToManualFlag = useCallback(
    (flag: ManualFillFlag) => {
      setShowManualFlags(false);
      setHighlightedSectionId(flag.sectionId);
      setActiveSubmissionFlag(flag);
      setActiveTab("content");
      window.setTimeout(() => setHighlightedSectionId(null), 4000);
      window.requestAnimationFrame(() => {
        scrollToManuscriptSection(flag.sectionId);
      });
    },
    [scrollToManuscriptSection]
  );

  const activeFlagHighlight = useMemo((): FlagHighlightRange | null => {
    if (!activeSubmissionFlag) return null;
    const section = outline.sections.find((s) => s.id === activeSubmissionFlag.sectionId);
    if (!section) return null;
    return resolveFlagHighlight(activeSubmissionFlag, section.content ?? "");
  }, [activeSubmissionFlag, outline.sections]);

  const manuscriptSections = useMemo(
    () => getManuscriptSections(outline.sections),
    [outline.sections]
  );

  const manuscriptIndexById = useMemo(
    () => buildManuscriptIndexMap(outline.sections),
    [outline.sections]
  );

  const manuscriptProgress = useMemo(() => {
    const total = manuscriptSections.length;
    const complete = manuscriptSections.filter((s) =>
      Boolean(s.content?.trim())
    ).length;
    return { complete, total };
  }, [manuscriptSections]);

  useEffect(() => {
    if (activeTab !== "content" && activeTab !== "export") return;
    const pane =
      activeTab === "export" ? submitScrollRef.current : contentScrollRef.current;
    if (!pane) return;
    const layout = pane.closest(".proposal-content-layout");
    if (!layout) return;

    const onWheel = (raw: Event) => {
      if (!(raw instanceof WheelEvent)) return;
      const event = raw;
      const target = event.target;
      if (!(target instanceof Node)) return;
      const nav = layout.querySelector(".proposal-on-page-nav");
      if (nav?.contains(target)) return;
      if (!pane.contains(target)) return;

      const { scrollHeight, clientHeight } = pane;
      if (scrollHeight <= clientHeight + 1) return;

      event.preventDefault();
      pane.scrollTop += event.deltaY;
    };

    layout.addEventListener("wheel", onWheel, { passive: false });
    return () => layout.removeEventListener("wheel", onWheel);
  }, [activeTab, manuscriptSections.length]);

  useEffect(() => {
    let cancelled = false;

    const defaults = buildDefaultOutline(rfp);
    setOutline(defaults);
    setSelectedSectionId(defaults.sections[0]?.id ?? null);
    setHydrated(true);
    setDraftLoadState("loading");
    setGenerateError(null);

    async function load() {
      let draft: ProposalOutline | null = null;
      let research: ProposalResearch | null = null;
      let providerName: string | null = null;
      let status: ProposalPipelineStatus | null = null;

      try {
        for (let attempt = 0; attempt < 2; attempt += 1) {
          const result = await fetchProposalDraft(rfp.id, {
            timeoutMs: PROPOSAL_INITIAL_LOAD_TIMEOUT_MS,
          });
          if (cancelled) return;
          draft = result.draft;
          research = result.research;
          providerName = result.provider ?? null;
          status = result.pipelineStatus;
          if (draft || research) break;
          await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
        }
      } catch (error) {
        if (cancelled) return;
        setDraftLoadState("error");
        setGenerateError(
          error instanceof Error
            ? error.message
            : "Could not load proposal from server."
        );
        return;
      }

      if (cancelled) return;

      setDraftLoadState("ready");

      if (!draft && !research) {
        return;
      }

      setResearch(research);
      setBudget(research?.budget ?? null);
      setPresubmitReview(research?.presubmitReview ?? null);
      setProvider(providerName);
      setPipelineStatus(
        buildPipelineStatus(draft, research, status)
      );

      const inFlightPhase = research?.pipelineCheckpoint?.inProgressPhase;
      if (inFlightPhase && inFlightPhase !== FULFILL_SCAN_PHASE) {
        setGenerateNotice(pipelineServerStillWorkingMessage(inFlightPhase));
      }

      const contentSections = draft ? countSectionsWithContent(draft) : 0;
      const researchReady = (research?.rfpSections?.length ?? 0) > 0;
      const recoverableSnap = [...(draft?.snapshots ?? [])]
        .reverse()
        .find((s) => (s.sectionCount ?? s.sections?.length ?? 0) > 0);

      saveGenerationRef.current += 1;
      skipNextSaveRef.current = true;

      // Live draft wiped but snapshot still in Supabase — restore automatically.
      if (contentSections === 0 && recoverableSnap?.savedAt) {
        try {
          const restored = await restoreProposalSnapshot(
            rfp.id,
            recoverableSnap.savedAt
          );
          if (cancelled) return;
          const prepared = prepareOutline(restored);
          setOutline(prepared);
          setSelectedSectionId(
            prepared.sections.find((s) => s.content)?.id ??
              prepared.sections[0]?.id ??
              null
          );
          setActiveTab("content");
          setPipelineStatus(
            buildPipelineStatus(prepared, research, status)
          );
          setGenerateNotice(
            `Recovered manuscript from saved version (“${recoverableSnap.label}”) — live draft had been emptied by a bad autosave.`
          );
          return;
        } catch {
          // Fall through to empty / rebuild paths below.
        }
      }

      if (draft && contentSections > 0) {
        const prepared = prepareOutline(draft);
        setOutline(prepared);
        setSelectedSectionId(prepared.sections[0]?.id ?? null);
        setActiveTab("content");
      } else if (researchReady && research && isLikelyWipedOutline(draft ?? buildDefaultOutline(rfp), research)) {
        const rebuilt = prepareOutline(
          rebuildOutlineFromResearch(rfp, research, draft)
        );
        setOutline(rebuilt);
        setSelectedSectionId(rebuilt.sections[0]?.id ?? null);
        setActiveTab("outline");
        setGenerateNotice(
          recoverableSnap
            ? `Live draft is empty — use Sections → saved version menu (“${recoverableSnap.label}”) to restore your manuscript.`
            : "Section list restored from cached research — use Generate proposal to re-draft content."
        );
      } else if (draft) {
        const prepared = prepareOutline(draft);
        setOutline(prepared);
        setSelectedSectionId(prepared.sections[0]?.id ?? null);
        setActiveTab(prepared.sections.some((s) => s.content) ? "content" : "outline");
        if (contentSections === 0 && recoverableSnap) {
          setGenerateNotice(
            `Live draft is empty — use Sections → saved version menu (“${recoverableSnap.label}”) to restore your manuscript.`
          );
        }
      } else {
        const defaults = buildDefaultOutline(rfp);
        setOutline(defaults);
        setSelectedSectionId(defaults.sections[0]?.id ?? null);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [rfp]);

  /** Keep trying to sync while the backend is busy with generation. */
  useEffect(() => {
    if (!hydrated) return;
    if (draftLoadState !== "loading" && draftLoadState !== "error") return;

    let cancelled = false;
    const retry = async () => {
      try {
        const result = await fetchProposalDraft(rfp.id, {
          timeoutMs: PROPOSAL_INITIAL_LOAD_TIMEOUT_MS,
        });
        if (cancelled) return;
        if (!result.draft && !result.research) return;
        setDraftLoadState("ready");
        setGenerateError(null);
        setResearch(result.research);
        setBudget(result.research?.budget ?? null);
        setPresubmitReview(result.research?.presubmitReview ?? null);
        setProvider(result.provider ?? null);
        if (result.research) {
          setPipelineStatus(
            buildPipelineStatus(result.draft, result.research, result.pipelineStatus)
          );
          const inFlight = result.research.pipelineCheckpoint?.inProgressPhase;
          if (inFlight && inFlight !== FULFILL_SCAN_PHASE) {
            setGenerateNotice(pipelineServerStillWorkingMessage(inFlight));
            setActiveTab("content");
          }
        }
        saveGenerationRef.current += 1;
        skipNextSaveRef.current = true;
        if (result.draft) {
          const prepared = prepareOutline(result.draft);
          setOutline(prepared);
          setSelectedSectionId(
            prepared.sections.find((s) => s.content)?.id ??
              prepared.sections[0]?.id ??
              null
          );
          if (countSectionsWithContent(prepared) > 0) {
            setActiveTab("content");
          }
        }
      } catch {
        // Keep banner / error until a later retry succeeds.
      }
    };

    const timer = setInterval(() => {
      void retry();
    }, 12_000);
    void retry();

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [hydrated, draftLoadState, rfp.id]);

  useEffect(() => {
    if (!hydrated) return;
    // Never autosave the empty default shell while the initial GET is still in flight —
    // that race was wiping full Supabase manuscripts (snapshots survived, live draft did not).
    if (draftLoadState !== "ready") return;
    if (isFullProposalRunning) return; // never overwrite backend partials mid-generation
    if (skipNextSaveRef.current) {
      skipNextSaveRef.current = false;
      return;
    }
    if (isLikelyWipedOutline(outline, research)) {
      return;
    }
    const generation = saveGenerationRef.current;
    const timer = setTimeout(() => {
      if (generation !== saveGenerationRef.current) return;
      void saveProposalDraft(rfp.id, outline);
    }, 800);
    return () => clearTimeout(timer);
  }, [outline, rfp.id, hydrated, research, isFullProposalRunning, draftLoadState]);

  useEffect(() => {
    if (!hydrated) return;
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      void fetchProposalDraft(rfp.id).then((snap) => {
        // Always sync — including null after Reset — so stale research/checkpoint
        // cannot resurrect "Continue proposal" on an empty outline.
        setPipelineStatus(snap.pipelineStatus);
        setResearch(snap.research);
        if (snap.research?.budget) setBudget(snap.research.budget);
        if (!snap.research) {
          setBudget(null);
          setPresubmitReview(null);
        }
      });
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [rfp.id, hydrated]);

  const manualFillFlags = useMemo(
    () =>
      mergeSubmissionFlags(
        scanSubmissionFlags(outline, {
          budget,
          rfpTitle: rfp.title,
          rfpClient: rfp.client,
          rfpSections: research?.rfpSections,
        }),
        presubmitReview?.manualFillFlags
      ),
    [outline, budget, rfp.title, rfp.client, research?.rfpSections, presubmitReview?.manualFillFlags]
  );
  const manualFillCount = manualFillFlags.length;
  const manualFillSummary = useMemo(
    () => summarizeManualFillFlags(manualFillFlags),
    [manualFillFlags]
  );
  const sectionProgress = Math.round(
    manuscriptProgress.total > 0
      ? (manuscriptProgress.complete / manuscriptProgress.total) * 100
      : 0
  );

  const reviewCriticalCount =
    presubmitReview?.issues.filter((i) => i.severity === "critical").length ?? 0;

  const workspaceTabs = useMemo(
    () =>
      baseWorkspaceTabs.map((tab) => {
        if (tab.id === "content" && manualFillCount > 0) {
          return { ...tab, count: manualFillCount };
        }
        return tab;
      }),
    [manualFillCount]
  );

  const selectedSection = outline.sections.find(
    (s) => s.id === selectedSectionId
  );

  const assistantViewSectionId =
    selectedSectionId ??
    manuscriptSections[0]?.id ??
    outline.sections[0]?.id ??
    "";

  const sections1to3Done = useMemo(
    () => staticSections1to3Complete(outline),
    [outline]
  );

  const phase2Done =
    research?.proposalExecutionPlan?.validation?.readinessStatus === "ready" ||
    (research?.evidenceCorpus?.length ?? 0) > 0;

  const phase3Done = useMemo(
    () =>
      sections1to3Done &&
      phase2Done &&
      outline.sections.some(
        (section) =>
          section.source === "rfp" && section.content.trim().length > 0
      ),
    [outline.sections, phase2Done, sections1to3Done]
  );

  const selfEditDone = useMemo(
    () => phaseIsComplete(outline, research, "phase-3-6-self-edit"),
    [outline, research]
  );

  const fullProposalDone = phase3Done && selfEditDone;

  const plainStatus = useMemo(
    () =>
      getProposalPlainStatus({
        fullProposalDone,
        manuscriptComplete:
          manuscriptProgress.total > 0 &&
          manuscriptProgress.complete === manuscriptProgress.total,
        manualFillCount,
        reviewCriticalCount,
        readyToSubmit: Boolean(research?.endingReport?.readyToSubmit),
        hasEndingReport: Boolean(research?.endingReport),
        isGenerating: isFullProposalRunning,
      }),
    [
      fullProposalDone,
      manuscriptProgress,
      manualFillCount,
      reviewCriticalCount,
      research?.endingReport,
      isFullProposalRunning,
    ]
  );

  const manuscriptRecoveryNeeded = useMemo(
    () =>
      hydrated &&
      (research?.rfpSections?.length ?? 0) > 0 &&
      isLikelyWipedOutline(outline, research),
    [hydrated, outline, research]
  );

  // Resume only when the manuscript itself has content. An empty post-Reset
  // shell (0 words) is always a fresh Generate — leftover research/checkpoint
  // must not keep showing "Continue proposal".
  const canResumePipeline =
    countSectionsWithContent(outline) > 0 &&
    Boolean(pipelineStatus?.canResume) &&
    !pipelineStatus?.isComplete;

  const serverPipelineActive = Boolean(
    research?.pipelineCheckpoint?.inProgressPhase
  );

  const effectiveFullProposalProgress = useMemo((): FullProposalProgress | null => {
    if (!isFullProposalRunning && !serverPipelineActive) return null;
    return (
      fullProposalProgress ??
      fullProposalProgressFromInFlight(
        research?.pipelineCheckpoint?.inProgressPhase
      )
    );
  }, [
    isFullProposalRunning,
    serverPipelineActive,
    fullProposalProgress,
    research?.pipelineCheckpoint?.inProgressPhase,
  ]);

  const anyPipelineRunning =
    isFullProposalRunning ||
    isPricingRunning ||
    isRefiningBudget ||
    isFinalizingGaps ||
    isFulfillingRfpGaps ||
    serverPipelineActive;

  const handleFinalizeGaps = useCallback(
    async (options?: { stayOnTab?: boolean }) => {
      setIsFinalizingGaps(true);
      setGapResolveError(null);
      setGapResolveNotice(null);
      try {
        const { review, research: updatedResearch, draft } =
          await runPhase4FinalizeGaps(rfp.id);
        setPresubmitReview(review);
        setResearch(updatedResearch);
        if (draft) {
          applyOutlineFromServer(draft);
          await saveProposalDraft(rfp.id, draft);
        }
        const flagCount = review.manualFillFlags?.length ?? 0;
        const beforeCount = manualFillCount;
        const notice =
          flagCount > 0
            ? `KB filled what it could — ${beforeCount} → ${flagCount} item(s) for Sonja/Ella.`
            : "KB resolved all submission gaps.";
        setGapResolveNotice(notice);
        if (!options?.stayOnTab) {
          setActiveTab("content");
        }
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "Finalize gaps failed";
        setGapResolveError(message);
      } finally {
        setIsFinalizingGaps(false);
      }
    },
    [rfp.id, manualFillCount, applyOutlineFromServer]
  );

  const handleRestoreSnapshot = useCallback(
    async (savedAtOverride?: string) => {
      const savedAt = savedAtOverride ?? restoreSnapshotAt;
      if (!savedAt) return false;
      const label =
        outline.snapshots?.find((s) => s.savedAt === savedAt)?.label ??
        "saved version";
      if (
        !confirm(
          `Load checkpoint “${label}”?\n\n` +
            "This replaces the FULL live proposal with that saved copy.\n" +
            "Your current live draft is kept as “Live draft (before restore)” in Saved versions.\n" +
            "Example: an earlier Form 2 checkpoint will clear Form 3/References if they were added later."
        )
      ) {
        return false;
      }
      setIsRestoringSnapshot(true);
      setGapResolveError(null);
      const beforeSections = outline.sections;
      try {
        const restored = await restoreProposalSnapshot(rfp.id, savedAt);
        applyOutlineFromServer(restored);
        setRestoreSnapshotAt(savedAt);

        const beforeById = new Map(
          beforeSections.map((s) => [s.id, (s.content || "").trim()] as const)
        );
        const changed =
          restored.sections.find((s) => {
            const prev = beforeById.get(s.id) ?? "";
            const next = (s.content || "").trim();
            return prev !== next;
          }) ??
          restored.sections.find((s) => (s.content || "").trim()) ??
          restored.sections[0];
        if (changed) {
          setSelectedSectionId(changed.id);
        }

        const filled = restored.sections.filter((s) =>
          Boolean(s.content?.trim())
        ).length;
        setGapResolveNotice(
          `Restored “${label}” (${filled} sections with content). Opened “${changed?.title ?? "proposal"}” so you can see the change.`
        );
        setActiveTab("outline");
        return true;
      } catch (error) {
        setGapResolveError(
          error instanceof Error ? error.message : "Restore failed"
        );
        return false;
      } finally {
        setIsRestoringSnapshot(false);
      }
    },
    [restoreSnapshotAt, outline.snapshots, outline.sections, rfp.id, applyOutlineFromServer]
  );

  const handleSnapshotDropdownChange = useCallback((savedAt: string) => {
    // Selecting a version only updates compare target — Restore button loads it.
    if (!savedAt) return;
    setRestoreSnapshotAt(savedAt);
  }, []);

  useEffect(() => {
    const snaps = outline.snapshots ?? [];
    if (!snaps.length) {
      setRestoreSnapshotAt("");
      return;
    }
    if (!snaps.some((s) => s.savedAt === restoreSnapshotAt)) {
      const preferred =
        [...snaps]
          .reverse()
          .find((s) => /saved after chat|after improving/i.test(s.label ?? "")) ??
        snaps[snaps.length - 1]!;
      setRestoreSnapshotAt(preferred.savedAt);
    }
  }, [outline.snapshots, restoreSnapshotAt]);

  const selectedSnapshotForCompare = useMemo(
    () =>
      outline.snapshots?.find((s) => s.savedAt === restoreSnapshotAt) ?? null,
    [outline.snapshots, restoreSnapshotAt]
  );

  const handleCompareJumpToSection = useCallback(
    (sectionId: string) => {
      setActiveTab("content");
      setSelectedSectionId(sectionId);
      window.setTimeout(() => {
        scrollToManuscriptSection(sectionId);
      }, 80);
    },
    [scrollToManuscriptSection]
  );

  const handleLiveDraftUpdate = useCallback((draft: ProposalOutline) => {
    setHydrated(true);
    applyOutlineFromServer(draft);
    // Do not force Review tab on each poll — user may be on Sections or Submit while generating.
    const withContent = draft.sections.filter((s) => s.content?.trim());
    setLiveGeneratedCount(withContent.length);

    // Section 1 must be readable first. While any 1.x subsection is still empty,
    // keep focus on the newest Section 1 subsection instead of jumping ahead.
    const section1Ids = draft.sections
      .filter((s) => s.id.startsWith("section-1-"))
      .map((s) => s.id);
    const section1Complete =
      section1Ids.length > 0 &&
      section1Ids.every((id) =>
        draft.sections.find((s) => s.id === id)?.content?.trim()
      );

    const newestInGroup = (prefix: string) =>
      [...withContent].reverse().find((s) => s.id.startsWith(prefix));

    const fingerprints = new Map(
      draft.sections.map((s) => [s.id, (s.content || "").length] as const)
    );
    const prev = liveContentFingerprintRef.current;

    // Currently writing = content grew since last poll (not merely "last completed").
    let growing: OutlineSection | undefined;
    for (const section of draft.sections) {
      const len = fingerprints.get(section.id) ?? 0;
      const prevLen = prev.get(section.id) ?? 0;
      if (len > prevLen) {
        growing = section;
      }
    }

    // Frontier = first empty section in order (what's next / about to fill).
    const frontier = draft.sections.find((s) => !(s.content || "").trim());

    liveContentFingerprintRef.current = fingerprints;

    const nonSection1 = withContent.filter((s) => !s.id.startsWith("section-1-"));
    const latestComplete = !section1Complete
      ? newestInGroup("section-1-") ?? withContent[withContent.length - 1]
      : newestInGroup("section-3-") ??
        newestInGroup("section-2-") ??
        nonSection1[nonSection1.length - 1] ??
        withContent[withContent.length - 1];

    // Prefer in-flight / next empty so the button does not keep naming a finished
    // section (e.g. 3.2 Oregon) while the next case study is drafting.
    const focus = !section1Complete
      ? growing ?? newestInGroup("section-1-") ?? frontier ?? latestComplete
      : growing ?? frontier ?? latestComplete;

    // Progress chip: only name a section that is still writing or next up —
    // never linger on a completed title like "3.2 — Oregon…".
    const progressTitle = growing?.title ?? frontier?.title ?? null;
    setLiveLatestSectionTitle(progressTitle);
    if (focus) {
      setSelectedSectionId(focus.id);
    }
  }, [applyOutlineFromServer]);

  const handleResearchPoll = useCallback((updated: ProposalResearch | null) => {
    if (!updated) return;
    setResearch(updated);
    setPipelineStatus(buildPipelineStatus(outlineRef.current, updated));
  }, []);

  const handleLiveDraftUpdateRef = useRef(handleLiveDraftUpdate);
  const handleResearchPollRef = useRef(handleResearchPoll);
  handleLiveDraftUpdateRef.current = handleLiveDraftUpdate;
  handleResearchPollRef.current = handleResearchPoll;

  /** Resume live manuscript updates when user reopens during backend generation. */
  useEffect(() => {
    if (!hydrated || isFullProposalRunning) return;
    const phase = research?.pipelineCheckpoint?.inProgressPhase;
    if (!phase || phase === FULFILL_SCAN_PHASE) {
      return;
    }
    const progressMap: Record<string, FullProposalProgress> = {
      "sections-1-3": "sections-1-3",
      "phase-2": "phase-2",
      "phase-3": "phase-3",
      "phase-3-6-self-edit": "phase-3-6-self-edit",
      "phase-3-5-budget": "phase-3-5-budget",
      "phase-4-review": "phase-4-review",
    };
    setFullProposalProgress(
      progressMap[phase] ??
        fullProposalProgressFromInFlight(phase) ??
        "phase-3"
    );
    setGenerateNotice(pipelineServerStillWorkingMessage(phase));
    setGenerateError(null);
    const stop = startLiveDraftPolling(
      rfp.id,
      (draft) => handleLiveDraftUpdateRef.current(draft),
      (updated) => {
        handleResearchPollRef.current(updated);
        const live = updated?.pipelineCheckpoint?.inProgressPhase;
        if (live && live !== FULFILL_SCAN_PHASE) {
          setGenerateNotice(pipelineServerStillWorkingMessage(live));
        } else {
          setGenerateNotice((prev) =>
            prev?.startsWith("Still generating") ? null : prev
          );
        }
      }
    );
    return () => {
      stop();
    };
  }, [
    hydrated,
    isFullProposalRunning,
    research?.pipelineCheckpoint?.inProgressPhase,
    rfp.id,
  ]);

  const rfpTabProgress = useMemo(() => {
    const ids = new Set(research?.rfpSections?.map((s) => s.id) ?? []);
    if (ids.size === 0) return null;
    const filled = outline.sections.filter(
      (s) => ids.has(s.id) && (s.content || "").trim()
    ).length;
    return { filled, total: ids.size };
  }, [research?.rfpSections, outline.sections]);

  const handleStopPipeline = useCallback(async () => {
    fullProposalAbortRef.current?.abort();
    fulfillAbortRef.current?.abort();
    try {
      await stopProposalGeneration(rfp.id);
    } catch {
      // Still stop UI even if stop request fails (e.g. offline).
    }
    try {
      const snapshot = await fetchProposalDraft(rfp.id);
      if (snapshot.draft) {
        applyOutlineFromServer(snapshot.draft);
      }
      if (snapshot.research) {
        setResearch(snapshot.research);
        setPipelineStatus(
          buildPipelineStatus(
            snapshot.draft,
            snapshot.research,
            snapshot.pipelineStatus
          )
        );
        if (snapshot.research.budget) {
          setBudget(snapshot.research.budget);
        }
        if (snapshot.research.presubmitReview) {
          setPresubmitReview(snapshot.research.presubmitReview);
        }
      }
    } catch {
      // Non-fatal — checkpoint still updated on server.
    }
    setIsFullProposalRunning(false);
    setIsFulfillingRfpGaps(false);
    setIsPricingRunning(false);
    setIsRefiningBudget(false);
    setIsFinalizingGaps(false);
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
    fullProposalAbortRef.current = null;
    fulfillAbortRef.current = null;
    setGenerateNotice(
      "Stopped — progress saved in the database. Use Continue proposal to resume."
    );
    setGenerateError(null);
  }, [rfp.id, applyOutlineFromServer]);

  const handleFulfillRfpGaps = useCallback(async () => {
    if (
      !confirm(
        "Scan the full RFP for anything still missing?\n\nWalks the uploaded PDF, submission checklist, **budget** (reconcile + fee sync), and **contractor KPIs** (Section 2.3). Adds missing closing sections. Team bios and case studies stay unchanged.\n\nA saved version is stored before each scan.\n\nUses AI tokens."
      )
    ) {
      return;
    }
    setIsFulfillingRfpGaps(true);
    setGapResolveError(null);
    setGapResolveNotice(null);
    fulfillAbortRef.current?.abort();
    const abort = new AbortController();
    fulfillAbortRef.current = abort;
    const stopScanPoll = startLiveDraftPolling(
      rfp.id,
      handleLiveDraftUpdate,
      handleResearchPoll
    );
    try {
      const { review, research: updatedResearch, draft, fulfillReport } =
        await runFulfillRfpGaps(rfp.id, { signal: abort.signal });
      setPresubmitReview(review);
      setResearch(updatedResearch);
      applyOutlineFromServer(draft);
      await saveProposalDraft(rfp.id, draft);
      const addedSections = fulfillReport.closingAddedSections ?? [];
      const narrativeAdded =
        fulfillReport.submissionDeliverablesAdded?.length ??
        fulfillReport.submissionNarrativesAdded?.length ??
        0;
      const added =
        addedSections.length ||
        (fulfillReport.closingAdded?.length ?? 0) ||
        narrativeAdded;
      const human = fulfillReport.humanDecisionGaps?.length ?? 0;
      const addedTitles = addedSections.map((s) => s.title).filter(Boolean);
      const detectedSections =
        fulfillReport.closingDetectedSections ??
        (fulfillReport.closingDetected ?? []).map((id) => ({ id, title: id }));
      const alreadyPresent = fulfillReport.closingAlreadyPresent ?? [];
      const inPlaceFixes = fulfillReport.inPlaceFixCount ?? 0;
      const detectedLabels = detectedSections.map((s) => s.title).filter(Boolean);
      setGapResolveNotice(
        `RFP scan done — ${detectedLabels.length} closing item(s) found in PDF` +
          (detectedLabels.length ? ` (${detectedLabels.join(", ")})` : "") +
          `; ${added} new section(s)` +
          (addedTitles.length ? `: ${addedTitles.join(", ")}` : "") +
          (alreadyPresent.length
            ? `; ${alreadyPresent.length} already in proposal (KPI/insurance/typos updated in place`
            : "") +
          (inPlaceFixes ? `, ${inPlaceFixes} in-place fix(es)` : "") +
          (alreadyPresent.length ? ")" : "") +
          (human ? `; ${human} need a human decision.` : ".") +
          " Saved version: Sections tab → saved version menu."
      );
      setGenerateNotice(
        addedTitles.length
          ? `Added from RFP — open Review: ${addedTitles.join(", ")}`
          : detectedLabels.length || inPlaceFixes
            ? `RFP scan finished — ${detectedLabels.length} closing items matched; ${inPlaceFixes} in-place fixes. Qualifications stay [VERIFY] until real KB/Section 3 content — Scan will not invent case studies.`
            : "RFP scan finished — review the updated proposal."
      );
      setGenerateError(null);
      setActiveTab("content");
      const jumpId =
        addedSections[0]?.id ||
        draft.sections.find((s) => s.id.startsWith("rfp-closing-"))?.id ||
        draft.sections.find((s) => s.id.startsWith("rfp-qual-"))?.id ||
        null;
      if (jumpId) {
        setSelectedSectionId(jumpId);
        window.setTimeout(() => {
          scrollToManuscriptSection(jumpId);
        }, 120);
      }
    } catch (error) {
      if (
        abort.signal.aborted ||
        (error instanceof DOMException && error.name === "AbortError")
      ) {
        setGenerateNotice(
          "Scan RFP stopped — partial changes may be saved; check Sections → saved version menu."
        );
        setGenerateError(null);
        return;
      }
      const message =
        error instanceof Error ? error.message : "RFP scan failed";
      setGapResolveError(message);
      setGenerateError(message);
    } finally {
      if (fulfillAbortRef.current === abort) {
        fulfillAbortRef.current = null;
      }
      stopScanPoll();
      setIsFulfillingRfpGaps(false);
    }
  }, [rfp.id, applyOutlineFromServer, scrollToManuscriptSection, handleLiveDraftUpdate, handleResearchPoll]);

  const handleGenerateFullProposal = useCallback(async (options?: { startAfterSections1to3?: boolean }) => {
    // Continue = resume from checkpoint (e.g. budget failure).
    // Fresh / regenerate-from-done = forceRestart from Sections 1–3.
    const startAfterSections1to3 = Boolean(options?.startAfterSections1to3);
    const hasManuscriptContent = countSectionsWithContent(outline) > 0;
    // Never "resume" an empty outline — that is always a forceRestart generate.
    const shouldResume =
      !startAfterSections1to3 &&
      canResumePipeline &&
      hasManuscriptContent &&
      Boolean(pipelineStatus);

    if (startAfterSections1to3) {
      if (!staticSections1to3Complete(outline)) {
        setGenerateError("Draft Sections 1–3 first, then start after Sections 1–3.");
        return;
      }
      if (
        !confirm(
          "Start after Sections 1–3?\n\nThis keeps the existing Sections 1–3 and runs Phase 2 intelligence, RFP drafting, budget, and review."
        )
      ) {
        return;
      }
    } else if (shouldResume) {
      if (
        !confirm(
          `${pipelineResumeMessage(pipelineStatus!)}\n\nContinue from where it left off? (Skips finished phases.)`
        )
      ) {
        return;
      }
    } else if (
      fullProposalDone &&
      !confirm(
        "Start full proposal from the beginning?\n\nThis regenerates Sections 1–3, intelligence, drafting, budget, and review (uses LLM tokens)."
      )
    ) {
      return;
    }

    fullProposalAbortRef.current?.abort();
    const abort = new AbortController();
    fullProposalAbortRef.current = abort;

    const forceRestart = !(shouldResume || startAfterSections1to3);

    setIsFullProposalRunning(true);
    setFullProposalProgress(null);
    setGenerateError(null);
    setGenerateNotice(null);

    // Fresh start: clear the editor immediately so old manuscript cannot flash
    // while the server soft-regenerates Sections 1–3 in place (no DB wipe).
    if (forceRestart) {
      const defaults = buildDefaultOutline(rfp);
      saveGenerationRef.current += 1;
      skipNextSaveRef.current = true;
      liveContentFingerprintRef.current = new Map();
      setOutline(defaults);
      setResearch(null);
      setBudget(null);
      setPresubmitReview(null);
      setPipelineStatus(null);
      setSectionRevisions({});
      persistStoredRevisions(rfp.id, {});
      setLiveGeneratedCount(0);
      setLiveLatestSectionTitle(null);
      setSelectedSectionId(defaults.sections[0]?.id ?? null);
      setActiveTab("content");
    } else {
      setLiveGeneratedCount(countSectionsWithContent(outline));
      setLiveLatestSectionTitle(null);
    }

    try {
      const { draft, research: updatedResearch } =
        await generateFullProposalStaged(rfp.id, setFullProposalProgress, {
          forceRestart,
          startFrom: startAfterSections1to3
            ? "phase-2"
            : shouldResume
            ? pipelineStatus!.resumeFromPhase
            : undefined,
          forceRerunFromStart: startAfterSections1to3,
          signal: abort.signal,
          onDraftUpdate: handleLiveDraftUpdate,
          onResearchUpdate: handleResearchPoll,
        });
      if (abort.signal.aborted) return;
      applyOutlineFromServer(draft);
      if (updatedResearch) {
        setResearch(updatedResearch);
        setPipelineStatus(buildPipelineStatus(draft, updatedResearch));
        if (updatedResearch.budget) {
          setBudget(updatedResearch.budget);
        }
        if (updatedResearch.presubmitReview) {
          setPresubmitReview(updatedResearch.presubmitReview);
        }
      }
      await saveProposalDraft(rfp.id, draft);
      setActiveTab("content");
      setSelectedSectionId(
        draft.sections.find((s) => s.content)?.id ?? draft.sections[0]?.id ?? null
      );
    } catch (error) {
      if (abort.signal.aborted || (error instanceof DOMException && error.name === "AbortError")) {
        setGenerateNotice(
          "Stopped — progress saved in the database. Use Continue proposal to resume."
        );
        setGenerateError(null);
        return;
      }
      setFullProposalProgress("recovering");
      const errMsg =
        error instanceof Error ? error.message : "Full proposal generation failed";
      const recovered = await recoverProposalDraftIfSaved(rfp.id, {
        minSectionsWithContent: 10,
      });
      if (recovered) {
        applyOutlineFromServer(recovered.draft);
        if (recovered.research) {
          setResearch(recovered.research);
          const status = buildPipelineStatus(recovered.draft, recovered.research);
          setPipelineStatus(status);
          if (recovered.research.budget) {
            setBudget(recovered.research.budget);
          }
          if (recovered.research.presubmitReview) {
            setPresubmitReview(recovered.research.presubmitReview);
          }
        }
        setActiveTab("content");
        setSelectedSectionId(
          recovered.draft.sections.find((s) => s.content)?.id ??
            recovered.draft.sections[0]?.id ??
            null
        );
        const status = buildPipelineStatus(
          recovered.draft,
          recovered.research
        );
        const inFlight = recovered.research?.pipelineCheckpoint?.inProgressPhase;
        const serverNote = inFlight
          ? ` ${pipelineServerStillWorkingMessage(inFlight)}`
          : "";
        setGenerateNotice(
          `Step failed, but progress is saved. ${pipelineResumeMessage(status, { blocker: errMsg })}${serverNote}`
        );
        setGenerateError(null);
      } else {
        setGenerateError(errMsg);
      }
    } finally {
      if (fullProposalAbortRef.current === abort) {
        fullProposalAbortRef.current = null;
      }
      setIsFullProposalRunning(false);
      setLiveLatestSectionTitle(null);
      let keepClientProgress = false;
      if (!abort.signal.aborted) {
        try {
          const snap = await fetchProposalDraft(rfp.id);
          if (snap.research) {
            setResearch(snap.research);
            setPipelineStatus(
              buildPipelineStatus(outline, snap.research, snap.pipelineStatus)
            );
            keepClientProgress = Boolean(
              snap.research.pipelineCheckpoint?.inProgressPhase
            );
          }
        } catch {
          keepClientProgress = false;
        }
      }
      if (!keepClientProgress) {
        setFullProposalProgress(null);
      }
    }
  }, [rfp, fullProposalDone, canResumePipeline, pipelineStatus, outline, handleLiveDraftUpdate, applyOutlineFromServer]);

  const handleResetOutline = async () => {
    setIsResettingDraft(true);
    setResetConfirmOpen(false);

    // Cancel in-flight Full Proposal / budget HTTP calls first
    fullProposalAbortRef.current?.abort();
    fullProposalAbortRef.current = null;
    setIsFullProposalRunning(false);
    setIsPricingRunning(false);
    setIsRefiningBudget(false);
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
    liveContentFingerprintRef.current = new Map();

    // 1. Hard-delete from DB (archives filled draft first, then wipe)
    let resetFailed: string | null = null;
    try {
      await resetProposal(rfp.id);
    } catch (error) {
      resetFailed =
        error instanceof Error
          ? error.message
          : "Server reset failed — local outline still cleared.";
    }

    // 2. Reset local state to defaults
    const defaults = buildDefaultOutline(rfp);
    saveGenerationRef.current += 1;
    skipNextSaveRef.current = true;
    setOutline(defaults);
    setSectionRevisions({});
    persistStoredRevisions(rfp.id, {});
    setRevisionDrawerSectionId(null);
    setSelectedSectionId(defaults.sections[0]?.id ?? null);
    setPresubmitReview(null);
    setResearch(null);
    setBudget(null);
    setPipelineStatus(null);
    setGenerateError(resetFailed);
    setGenerateNotice(
      resetFailed
        ? "Local outline cleared, but server wipe failed — try Reset again before generating."
        : "Reset complete. Live draft and research cache cleared."
    );
    setFullProposalProgress(null);
    setLiveLatestSectionTitle(null);
    setLiveGeneratedCount(0);

    // 3. Persist empty shell so a late autosave / race cannot resurrect old monolith sections
    try {
      await saveProposalDraft(rfp.id, defaults);
    } catch {
      // Non-fatal — DB reset already cleared content
    } finally {
      setIsResettingDraft(false);
    }
  };


  const handleRecoverManuscript = useCallback(async () => {
    if (!research?.rfpSections?.length) {
      setGenerateError("No cached research to recover from. Run Generate Full Proposal.");
      return;
    }
    setIsFullProposalRunning(true);
    setFullProposalProgress("phase-3");
    setGenerateError(null);
    try {
      const rebuilt = rebuildOutlineFromResearch(rfp, research, outline);
      applyOutlineFromServer(rebuilt);
      await saveProposalDraft(rfp.id, rebuilt);

      setFullProposalProgress("sections-1-3");
      await generateProposalSections1to3(rfp.id);

      setFullProposalProgress("phase-3");
      const { draft: drafted, research: afterPhase3 } = await runPhase3Drafting(rfp.id);

      setFullProposalProgress("phase-3-6-self-edit");
      const { draft: polished, research: afterEdit } =
        await runPhase3_6SelfEditWithRecovery(rfp.id);

      setFullProposalProgress("phase-3-5-budget");
      const { draft, research: updatedResearch, budget } =
        await runPhase3_5BudgetWithRecovery(rfp.id);

      setFullProposalProgress("phase-4-review");
      const { research: reviewedResearch } = await runPhase4PreSubmitReview(rfp.id);

      const finalDraft = draft ?? polished ?? drafted;
      applyOutlineFromServer(finalDraft);
      setResearch(reviewedResearch ?? updatedResearch ?? afterEdit ?? afterPhase3);
      if (budget) setBudget(budget);
      setPresubmitReview(reviewedResearch.presubmitReview ?? null);
      await saveProposalDraft(rfp.id, finalDraft);
      setGenerateNotice("Manuscript re-drafted from cached KB research.");
      setActiveTab("content");
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : "Manuscript recovery failed"
      );
    } finally {
      setIsFullProposalRunning(false);
      setFullProposalProgress(null);
    }
  }, [research, rfp, outline]);

  const handleDraftSections1to3 = useCallback(async () => {
    setIsFullProposalRunning(true);
    setFullProposalProgress("sections-1-3");
    setGenerateError(null);
    setGenerateNotice(null);
    const seeded = buildDefaultOutline(rfp);
    applyOutlineFromServer(seeded);
    await saveProposalDraft(rfp.id, seeded);
    // Show Section 1 stubs immediately so the user can follow subsection-by-subsection.
    setActiveTab("content");
    setSelectedSectionId(
      seeded.sections.find((s) => s.id.startsWith("section-1-"))?.id ??
        seeded.sections[0]?.id ??
        null
    );
    setLiveGeneratedCount(0);
    setLiveLatestSectionTitle(null);
    const stopPolling = startLiveDraftPolling(
      rfp.id,
      handleLiveDraftUpdate,
      handleResearchPoll
    );
    try {
      const draft = await generateProposalSections1to3(rfp.id);
      if (!staticSections1to3Complete(draft)) {
        throw new Error(
          "Sections 1–3 finished but content is missing. Click Reset, then try Draft Sections 1–3 again."
        );
      }
      applyOutlineFromServer(draft);
      await saveProposalDraft(rfp.id, draft);
      setActiveTab("content");
      setSelectedSectionId(
        draft.sections.find((s) => s.content)?.id ?? draft.sections[0]?.id ?? null
      );
      setGenerateNotice("Sections 1–3 successfully drafted.");
    } catch (error) {
      setGenerateError(
        error instanceof Error ? error.message : "Sections 1–3 generation failed"
      );
    } finally {
      stopPolling();
      setIsFullProposalRunning(false);
      setFullProposalProgress(null);
      setLiveLatestSectionTitle(null);
    }
  }, [rfp, applyOutlineFromServer, handleLiveDraftUpdate]);

  const handlePrimaryPipeline = useCallback(async () => {
    if (manuscriptRecoveryNeeded) {
      await handleRecoverManuscript();
      return;
    }
    await handleGenerateFullProposal();
  }, [manuscriptRecoveryNeeded, handleRecoverManuscript, handleGenerateFullProposal]);

  const primaryPipelineLabel = useMemo(() => {
    if (isFullProposalRunning || serverPipelineActive) {
      const activity = research?.pipelineCheckpoint?.activityLabel?.trim();
      if (activity) {
        return activity.length > 44 ? `${activity.slice(0, 43)}…` : activity;
      }
      if (effectiveFullProposalProgress === "sections-1-3") return "Sections 1–3…";
      if (effectiveFullProposalProgress === "phase-2") return "Intelligence…";
      if (effectiveFullProposalProgress === "phase-3") {
        if (rfpTabProgress) {
          return `RFP tabs ${rfpTabProgress.filled}/${rfpTabProgress.total}…`;
        }
        return "RFP tabs…";
      }
      if (effectiveFullProposalProgress === "phase-3-6-self-edit") {
        return manualFillCount > 0
          ? `Senior editor · ${manualFillCount} flags…`
          : "Senior editor…";
      }
      if (effectiveFullProposalProgress === "phase-3-5-budget") return "Budget…";
      if (effectiveFullProposalProgress === "phase-4-review") return "Pre-submit…";
      if (effectiveFullProposalProgress === "recovering") return "Syncing…";
      return "Working…";
    }
    if (isFulfillingRfpGaps) {
      const activity = research?.pipelineCheckpoint?.activityLabel?.trim();
      if (activity) {
        return activity.length > 40 ? `${activity.slice(0, 39)}…` : activity;
      }
      return "Scan RFP…";
    }
    if (canResumePipeline) return "Continue proposal";
    return "Generate proposal";
  }, [
    isFullProposalRunning,
    serverPipelineActive,
    effectiveFullProposalProgress,
    canResumePipeline,
    isFulfillingRfpGaps,
    research?.pipelineCheckpoint?.activityLabel,
    rfpTabProgress,
    manualFillCount,
  ]);

  const updateSection = (id: string, patch: Partial<OutlineSection>) => {
    setOutline((prev) => ({
      ...prev,
      sections: prev.sections.map((s) =>
        s.id === id ? { ...s, ...patch } : s
      ),
      updatedAt: new Date().toISOString(),
    }));
  };

  const moveSection = (id: string, direction: -1 | 1) => {
    setOutline((prev) => {
      const index = prev.sections.findIndex((s) => s.id === id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= prev.sections.length) {
        return prev;
      }
      const sections = [...prev.sections];
      [sections[index], sections[target]] = [sections[target], sections[index]];
      return { ...prev, sections, updatedAt: new Date().toISOString() };
    });
  };

  const removeSection = (id: string) => {
    const target = outline.sections.find((s) => s.id === id);
    if (!target) return;
    if (outline.sections.length <= 1) return;
    const ok = window.confirm(`Delete “${target.title}”? This can’t be undone from here.`);
    if (!ok) return;

    setOutline((prev) => {
      let bio = 0;
      let work = 0;
      const sections = prev.sections
        .filter((s) => s.id !== id)
        .map((s) => {
          if (
            s.id.startsWith("section-2-bio-") &&
            s.id !== "section-2-bio-placeholder"
          ) {
            bio += 1;
            const name = s.title.includes("—")
              ? s.title.split("—").slice(1).join("—").trim()
              : s.title;
            return { ...s, title: `2.${bio} — ${name}` };
          }
          if (
            s.id.startsWith("section-3-work-") &&
            s.id !== "section-3-work-placeholder"
          ) {
            work += 1;
            const name = s.title.includes("—")
              ? s.title.split("—").slice(1).join("—").trim()
              : s.title;
            return { ...s, title: `3.${work} — ${name}` };
          }
          return s;
        });
      if (selectedSectionId === id) {
        setSelectedSectionId(sections[0]?.id ?? null);
      }
      return { ...prev, sections, updatedAt: new Date().toISOString() };
    });
    setSectionRevisions((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      persistStoredRevisions(rfp.id, next);
      return next;
    });
  };

  const addCustomSection = () => {
    const title = newSectionTitle.trim();
    if (!title) return;
    const section = createCustomSection(title);
    setOutline((prev) => ({
      ...prev,
      sections: [...prev.sections, section],
      updatedAt: new Date().toISOString(),
    }));
    setSelectedSectionId(section.id);
    setNewSectionTitle("");
  };

  const fullManuscript = useMemo(() => {
    return getManuscriptSections(outline.sections)
      .filter((s) => s.content?.trim())
      .map((s) => {
        const clean = stripManuscriptDisplayArtifacts(s.content || "").replace(
          /\s*\[E\d+\]/g,
          ""
        );
        return `## ${s.title}\n\n${clean}`;
      })
      .join("\n\n---\n\n");
  }, [outline.sections]);

  const handleDownloadDocx = async () => {
    if (!fullManuscript.trim()) return;
    setDocxDownloadError(null);
    setIsDownloadingDocx(true);
    try {
      await downloadProposalDocx(rfp.id);
      setDocxDownloaded(true);
      setTimeout(() => setDocxDownloaded(false), 3000);
    } catch (error) {
      setDocxDownloadError(
        error instanceof Error ? error.message : "Word download failed."
      );
    } finally {
      setIsDownloadingDocx(false);
    }
  };

  if (!hydrated || draftLoadState === "loading" || draftLoadState === "idle") {
    return (
      <section className="proposal-workspace-card">
        <div className="proposal-workspace-chrome shrink-0 border-b border-zo-border/80 bg-white">
          <div className="flex items-center gap-3 px-3 py-2 md:px-4">
            <h2 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-foreground md:text-[0.95rem]">
              {rfp.title}
            </h2>
            {onOpenGoRfpPicker && goRfpCount ? (
              <button
                type="button"
                onClick={onOpenGoRfpPicker}
                className="proposal-go-picker-btn shrink-0"
                title="Switch to another Go RFP"
              >
                Switch RFP
                <span className="proposal-go-picker-count">{goRfpCount}</span>
              </button>
            ) : null}
          </div>
        </div>
        <div
          className="flex min-h-[min(28rem,70vh)] flex-col items-center justify-center gap-4 px-6 py-12 text-center"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <span
            className="h-9 w-9 animate-spin rounded-full border-[3px] border-zo-border border-t-zo-orange"
            aria-hidden
          />
          <div className="space-y-1.5">
            <p className="text-sm font-semibold text-foreground">
              Loading proposal…
            </p>
            <p className="max-w-sm text-xs leading-relaxed text-zo-text-muted">
              Fetching your saved draft. Generated content appears here when
              load finishes — this is not an empty proposal.
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (draftLoadState === "error") {
    return (
      <section className="proposal-workspace-card">
        <div className="proposal-workspace-chrome shrink-0 border-b border-zo-border/80 bg-white">
          <div className="flex items-center gap-3 px-3 py-2 md:px-4">
            <h2 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-foreground md:text-[0.95rem]">
              {rfp.title}
            </h2>
            {onOpenGoRfpPicker && goRfpCount ? (
              <button
                type="button"
                onClick={onOpenGoRfpPicker}
                className="proposal-go-picker-btn shrink-0"
                title="Switch to another Go RFP"
              >
                Switch RFP
                <span className="proposal-go-picker-count">{goRfpCount}</span>
              </button>
            ) : null}
          </div>
        </div>
        <div className="flex min-h-[min(28rem,70vh)] flex-col items-center justify-center gap-4 px-6 py-12 text-center">
          <p className="text-sm font-semibold text-foreground">
            Couldn’t load this proposal
          </p>
          <p className="max-w-md text-xs leading-relaxed text-zo-text-muted">
            {generateError ??
              "The server didn’t return the draft in time. Your content may still be saved — try again."}
          </p>
          <button
            type="button"
            className="zo-btn !px-4 !py-2 !text-xs"
            onClick={() => {
              setGenerateError(null);
              setDraftLoadState("loading");
            }}
          >
            Retry load
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="proposal-workspace-card">
      <div className="proposal-workspace-chrome shrink-0 border-b border-zo-border/80 bg-white">
        <div className="flex items-center gap-3 px-3 py-2 md:px-4">
          <h2 className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight text-foreground md:text-[0.95rem]">
            {rfp.title}
          </h2>
          {onOpenGoRfpPicker && goRfpCount ? (
            <button
              type="button"
              onClick={onOpenGoRfpPicker}
              className="proposal-go-picker-btn shrink-0"
              title="Switch to another Go RFP"
            >
              Switch RFP
              <span className="proposal-go-picker-count">{goRfpCount}</span>
            </button>
          ) : null}
        </div>

        {manualFillCount === 0 && plainStatus.tone !== "good" ? (
          <p
            className={`proposal-header-status px-3 pb-1.5 text-xs leading-snug md:px-4 proposal-header-status--${plainStatus.tone}`}
          >
            {plainStatus.headline}
          </p>
        ) : null}

        {sectionProgress < 100 ? (
          <div className="flex items-center gap-2 px-3 pb-2 md:px-4">
            <div className="proposal-progress-track proposal-progress-track--slim min-w-0 flex-1">
              <div
                className="proposal-progress-fill"
                style={{ width: `${sectionProgress}%` }}
              />
            </div>
            <span className="shrink-0 text-[10px] font-medium tabular-nums text-zo-text-muted">
              {sectionProgress}%
            </span>
          </div>
        ) : null}

        {(generateNotice || generateError) && (
          <div
            className={`border-t px-3 py-1.5 text-xs md:px-4 ${
              generateNotice
                ? "border-amber-200/80 bg-amber-50 text-amber-950"
                : "border-red-200/80 bg-red-50 text-zo-error"
            }`}
          >
            {generateNotice ?? generateError}
          </div>
        )}

        <div className="px-3 md:px-4">
          <OutlineTabs
            variant="underline"
            fullWidth
            tabs={workspaceTabs}
            activeTab={activeTab}
            onChange={(id) => {
              const next = id as WorkspaceTab;
              setActiveTab(next);
              if (next !== "outline") {
                setSectionChatReference(null);
              }
            }}
          />
        </div>
      </div>

      <ProposalManualFlagsPanel
        open={showManualFlags}
        flags={manualFillFlags}
        summary={manualFillSummary}
        activeSectionId={
          activeSubmissionFlag?.sectionId ?? selectedSectionId ?? highlightedSectionId
        }
        onJumpToFlag={handleJumpToManualFlag}
        onClose={() => setShowManualFlags(false)}
        onResolveAll={() => void handleFinalizeGaps({ stayOnTab: true })}
        isResolving={isFinalizingGaps}
        resolveNotice={gapResolveNotice}
        resolveError={gapResolveError}
      />

      <ProposalPipelineProgressStrip
        checkpoint={research?.pipelineCheckpoint}
        fullProposalProgress={effectiveFullProposalProgress}
        isFulfillScanRunning={isFulfillingRfpGaps}
        rfpTabProgress={rfpTabProgress}
      />

      <div className="proposal-workspace-body">
      {/* Outline tab */}
      <TabPanel id="outline" activeTab={activeTab} className="proposal-workspace-tab">
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="proposal-tab-actions flex shrink-0 flex-wrap items-center justify-end gap-2 border-b border-zo-border/60 px-3 py-2">
            <span className="mr-auto text-[13px] font-semibold tabular-nums text-zo-text-muted">
              {manuscriptProgress.complete}/{manuscriptProgress.total} drafted
            </span>
            <div className="proposal-tab-actions-toolbar">
            <label className="proposal-snapshot-field">
              <span className="proposal-snapshot-field-label">
                Saved version
              </span>
              <span className="proposal-snapshot-field-control">
                <select
                  value={restoreSnapshotAt}
                  onChange={(e) =>
                    handleSnapshotDropdownChange(e.target.value)
                  }
                  disabled={
                    isRestoringSnapshot ||
                    anyPipelineRunning ||
                    (outline.snapshots?.length ?? 0) === 0
                  }
                  className="proposal-snapshot-select"
                  aria-label="Choose a saved proposal version to compare"
                  aria-busy={isRestoringSnapshot}
                  title={
                    (outline.snapshots?.length ?? 0) > 0
                      ? "Select a checkpoint to compare. Click Restore to load it as the live draft."
                      : "Versions appear after chat improve, Scan RFP, or when a draft checkpoint is saved."
                  }
                >
                  {(outline.snapshots?.length ?? 0) === 0 ? (
                    <option value="">No versions yet</option>
                  ) : (
                    [...(outline.snapshots ?? [])].reverse().map((snap) => (
                      <option key={snap.savedAt} value={snap.savedAt}>
                        {snap.label}
                        {" · "}
                        {new Date(snap.savedAt).toLocaleString(undefined, {
                          month: "short",
                          day: "numeric",
                          hour: "numeric",
                          minute: "2-digit",
                        })}
                      </option>
                    ))
                  )}
                </select>
              </span>
              <button
                type="button"
                className="proposal-tab-text-btn"
                disabled={
                  !restoreSnapshotAt ||
                  isRestoringSnapshot ||
                  anyPipelineRunning ||
                  (outline.snapshots?.length ?? 0) === 0
                }
                onClick={() => void handleRestoreSnapshot()}
              >
                {isRestoringSnapshot ? "Restoring…" : "Restore"}
              </button>
            </label>
            <button
              type="button"
              onClick={() => setResetConfirmOpen(true)}
              disabled={isResettingDraft}
              className="zo-btn secondary proposal-toolbar-btn disabled:opacity-60"
            >
              {isResettingDraft ? "Resetting…" : "Reset draft"}
            </button>
            {anyPipelineRunning ? (
              <button
                type="button"
                onClick={() => void handleStopPipeline()}
                className="proposal-toolbar-btn proposal-toolbar-btn--danger"
              >
                Stop
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => void handlePrimaryPipeline()}
              disabled={anyPipelineRunning}
              className="zo-btn proposal-toolbar-btn disabled:opacity-60"
            >
              {isFullProposalRunning || isFulfillingRfpGaps ? (
                <>
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-zo-white/30 border-t-zo-white" />
                  {primaryPipelineLabel}
                </>
              ) : (
                primaryPipelineLabel
              )}
            </button>
            </div>
          </div>
          <div
            className="proposal-outline-layout grid min-h-0 min-w-0 flex-1 gap-0 overflow-hidden lg:gap-4 lg:p-3 xl:gap-6"
          >
          <div className="proposal-section-list flex min-h-0 min-w-0 flex-col overflow-hidden rounded-none border-b border-zo-border lg:rounded-2xl lg:border lg:border-zo-border/80">
            <div className="flex shrink-0 items-center justify-between border-b border-zo-border/60 px-3 py-2.5">
              <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-zo-text-muted">
                Sections
              </p>
            </div>
            <ul className="custom-scrollbar min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
              <ProposalSectionTree
                sections={outline.sections}
                manuscriptIndexById={manuscriptIndexById}
                selectedSectionId={selectedSectionId}
                highlightedSectionId={highlightedSectionId}
                manualFillFlags={manualFillFlags}
                sectionRevisions={sectionRevisions}
                sectionButtonRefs={sectionButtonRefs}
                onSelectSection={selectSection}
                onOpenRevision={(sectionId) => {
                  selectSection(sectionId);
                  setRevisionDrawerSectionId(sectionId);
                }}
                onDeleteSection={removeSection}
              />
            </ul>

            <div className="shrink-0 border-t border-zo-border bg-[var(--zo-input-bg)] p-2">
              <div className="flex min-w-0 items-stretch gap-1.5">
                <input
                  type="text"
                  value={newSectionTitle}
                  onChange={(e) => setNewSectionTitle(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addCustomSection()}
                  placeholder="New section title…"
                  className="min-w-0 flex-1 zo-input px-2.5 py-2 text-sm outline-none transition-smooth focus:border-zo-orange focus:ring-2 focus:ring-zo-orange/10"
                />
                <button
                  type="button"
                  onClick={addCustomSection}
                  className="zo-btn shrink-0 !px-2.5 !py-2"
                >
                  Add
                </button>
              </div>
            </div>
          </div>

          <div className="proposal-editor-pane flex min-h-0 min-w-0 flex-col overflow-hidden rounded-none lg:rounded-2xl lg:border lg:border-zo-border/80">
            {selectedSection ? (
              <>
                <div className="proposal-editor-chrome">
                  <div className="proposal-editor-chrome-row">
                    <span className="shrink-0 text-[10px] font-bold tabular-nums text-zo-text-muted">
                      {manuscriptIndexById.get(selectedSection.id) != null
                        ? `Section ${manuscriptIndexById.get(selectedSection.id)} of ${manuscriptProgress.total}`
                        : "Section"}
                    </span>
                    <input
                      type="text"
                      value={selectedSection.title}
                      onChange={(e) =>
                        updateSection(selectedSection.id, {
                          title: e.target.value,
                        })
                      }
                      className="proposal-editor-chrome-title"
                      aria-label="Section title"
                    />
                    <div className="ml-auto flex shrink-0 flex-wrap items-center gap-1">
                      <IconButton
                        onClick={() => removeSection(selectedSection.id)}
                        label="Remove section"
                        variant="danger"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </IconButton>
                    </div>
                  </div>
                </div>

                <div className="proposal-editor-split">
                  <div ref={editorScrollRef} className="proposal-editor-body">
                    <DraftSectionEditor
                      section={selectedSection}
                      wordCount={countWords(selectedSection.content)}
                      disabled={anyPipelineRunning}
                      chatBusy={sectionChatBusy}
                      value={selectedSection.content}
                      highlightRange={
                        activeSubmissionFlag?.sectionId === selectedSectionId
                          ? activeFlagHighlight
                          : null
                      }
                      onUserEditStart={() => setActiveSubmissionFlag(null)}
                      onChange={(content) =>
                        updateSection(selectedSection.id, {
                          content,
                          status: content ? "generated" : "outline",
                        })
                      }
                      onOpenRevisionChat={(request) => {
                        openSectionChat(request);
                      }}
                      storedRevision={
                        selectedSection
                          ? sectionRevisions[selectedSection.id] ?? null
                          : null
                      }
                      revisionDrawerOpen={
                        revisionDrawerSectionId === selectedSection?.id
                      }
                      onRevisionDrawerOpenChange={(open) =>
                        setRevisionDrawerSectionId(
                          open ? selectedSection.id : null
                        )
                      }
                    />
                  </div>
                </div>
              </>
            ) : (
              <div className="flex min-h-[16rem] flex-1 flex-col items-center justify-center p-6 text-center">
                <p className="text-sm text-zo-text-muted">
                  Select a section from the list to edit.
                </p>
              </div>
            )}
          </div>

          <div
            ref={assistantPaneRef}
            className="proposal-assistant-pane flex min-h-0 min-w-0 flex-col overflow-hidden rounded-none border-t border-zo-border lg:rounded-2xl lg:border lg:border-zo-border/80"
          >
            <ProposalSectionChatPanel
              rfpId={rfp.id}
              sections={outline.sections}
              viewingSectionId={assistantViewSectionId}
              disabled={anyPipelineRunning}
              reference={sectionChatReference}
              onSetReference={setSectionChatReference}
              messages={sectionChatMessages}
              onMessagesChange={setSectionChatMessages}
              onSectionUpdated={applySectionImproveFromServer}
              onRevisionRecorded={(sectionId, revision) =>
                recordSectionRevision(sectionId, revision)
              }
              onRevisionDrawerOpenChange={(sectionId, open) => {
                if (open) {
                  selectSection(sectionId);
                }
                setRevisionDrawerSectionId(open ? sectionId : null);
              }}
              onFocusSection={(sectionId) => {
                selectSection(sectionId);
              }}
              onBusyChange={setSectionChatBusy}
            />
          </div>
        </div>
        </div>
      </TabPanel>

      {/* Content tab */}
      <TabPanel id="content" activeTab={activeTab} className="proposal-workspace-tab proposal-workspace-tab--natural">
        {outline.sections.some((s) => s.content.trim()) ||
        isFullProposalRunning ||
        serverPipelineActive ? (
            <div className="proposal-content-tab-shell flex min-h-0 flex-1 flex-col">
            <div className="proposal-tab-actions flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-zo-border/60 px-3 py-2.5">
              <p className="text-xs text-zo-text-muted">Full proposal text</p>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void handleFulfillRfpGaps()}
                  disabled={
                    anyPipelineRunning ||
                    !outline.sections.some((s) => s.content.trim())
                  }
                  className="zo-btn !py-2 !px-3 !text-sm disabled:opacity-40"
                  title="Read the full RFP and add missing sections (Acknowledgement of Addenda, forms, attachments, etc.)"
                >
                  {isFulfillingRfpGaps
                    ? "Scanning RFP…"
                    : "Scan RFP & add missing pieces"}
                </button>
                <button
                  type="button"
                  onClick={() => setShowManualFlags((open) => !open)}
                  className={`proposal-checklist-btn ${
                    manualFillCount > 0
                      ? "proposal-checklist-btn--alert"
                      : "proposal-checklist-btn--idle"
                  } ${showManualFlags ? "is-open" : ""}`}
                >
                  Checklist
                  {manualFillCount > 0 ? (
                    <span className="proposal-checklist-count">{manualFillCount}</span>
                  ) : null}
                </button>
              </div>
            </div>
            <div className="proposal-content-jump-strip custom-scrollbar" aria-label="Jump to section">
              {manuscriptSections.map((section, index) => (
                <button
                  key={section.id}
                  type="button"
                  className={`proposal-content-jump-chip ${
                    highlightedSectionId === section.id ||
                    selectedSectionId === section.id
                      ? "is-active"
                      : ""
                  }`}
                  title={section.title}
                  onClick={() => scrollToManuscriptSection(section.id)}
                >
                  {index + 1}. {section.title}
                </button>
              ))}
            </div>
            {(outline.snapshots?.length ?? 0) > 0 && selectedSnapshotForCompare ? (
              <details className="mx-3 mb-2 shrink-0 rounded-lg border border-zo-border/70 bg-[#fafbfc] px-3 py-2">
                <summary className="cursor-pointer text-xs font-semibold text-foreground">
                  Compare to saved version ({selectedSnapshotForCompare.label})
                </summary>
                <div className="mt-2">
                  <ProposalVersionCompare
                    rfpId={rfp.id}
                    selectedSnapshot={selectedSnapshotForCompare}
                    currentSections={outline.sections}
                    onJumpToSection={handleCompareJumpToSection}
                  />
                </div>
              </details>
            ) : null}
            <div className="proposal-content-layout flex-1 min-h-0">
            <nav className="proposal-on-page-nav" aria-label="Jump to section">
              <p className="proposal-on-page-nav-label text-[10px] font-semibold text-zo-text-muted">
                Jump to
              </p>
              <ul className="proposal-on-page-nav-list mt-2 space-y-0.5">
                {manuscriptSections.map((section, index) => (
                  <li key={section.id}>
                    <button
                      type="button"
                      className={`proposal-on-page-link w-full text-left ${
                        highlightedSectionId === section.id ||
                        selectedSectionId === section.id
                          ? "is-active"
                          : ""
                      }`}
                      title={section.title}
                      onClick={() => scrollToManuscriptSection(section.id)}
                    >
                      <span className="proposal-on-page-num">{index + 1}</span>
                      <span className="proposal-on-page-title">
                        {section.title}
                        {!section.content?.trim() ? " · …" : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </nav>
            <div
              ref={contentScrollRef}
              className="proposal-content-scroll proposal-content-manuscript-pane proposal-review-manuscript custom-scrollbar min-h-0"
            >
              {(isFullProposalRunning || fullProposalProgress === "sections-1-3") &&
              fullProposalProgress === "sections-1-3" ? (
                <div className="rounded-xl border border-zo-orange/30 bg-[#ef5018]/08 px-4 py-3 text-sm text-foreground">
                  <span className="font-semibold text-zo-orange">Drafting live</span>
                  {" — "}
                  {liveLatestSectionTitle
                    ? `Latest: ${liveLatestSectionTitle}`
                    : "Section stubs ready. Subsections appear here as each agent finishes."}
                  {liveGeneratedCount > 0 ? (
                    <span className="ml-1 text-zo-text-muted">
                      ({liveGeneratedCount} with content)
                    </span>
                  ) : null}
                </div>
              ) : null}
              {manuscriptSections.map((section, index) =>
                  section.content?.trim() ? (
                  <article
                    key={section.id}
                    id={section.id}
                    className={`proposal-content-article proposal-content-article--read scroll-mt-24 ${
                      highlightedSectionId === section.id ? "is-flag-target" : ""
                    }`}
                  >
                    <h3 className="proposal-content-section-title">
                      <span className="text-zo-text-muted">{index + 1}.</span>{" "}
                      {section.title}
                      {sectionManualFillCount(section.id, manualFillFlags) > 0 ? (
                        <span className="ml-2 text-[11px] font-medium text-amber-800">
                          · needs input
                        </span>
                      ) : null}
                    </h3>
                    <div className="proposal-prose proposal-prose--manuscript mt-4">
                      <MarkdownReportBody
                        body={section.content}
                        variant="document"
                        highlightTexts={
                          activeSubmissionFlag?.sectionId === section.id && activeFlagHighlight
                            ? [activeFlagHighlight.text]
                            : []
                        }
                      />
                    </div>
                  </article>
                  ) : (
                  <article
                    key={section.id}
                    id={section.id}
                    className={`proposal-content-article scroll-mt-32 border border-dashed border-zo-border/80 bg-[var(--zo-input-bg)]/40 opacity-90 ${
                      highlightedSectionId === section.id ? "is-flag-target" : ""
                    }`}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-zo-border bg-white text-sm font-bold text-zo-text-muted">
                          {index + 1}
                        </span>
                        <div className="min-w-0">
                          <h3 className="text-[1.05rem] font-bold leading-tight tracking-tight text-foreground">
                            {section.title}
                          </h3>
                          <p className="mt-0.5 text-[11px] font-medium text-zo-orange">
                            {isFullProposalRunning ? "Generating…" : "Not drafted yet"}
                          </p>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <SectionStatusPill status={section.status || "outline"} />
                        <button
                          type="button"
                          className="zo-btn !px-3 !py-1.5 !text-xs"
                          onClick={() => selectSection(section.id)}
                        >
                          Open editor
                        </button>
                      </div>
                    </div>
                  </article>
                  )
                )}
            </div>
          </div>
            </div>
        ) : (
          <div className="flex min-h-[360px] flex-col items-center justify-center px-8 py-16 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[#ef5018]/15 text-[#ef5018]">
              <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
              </svg>
            </div>
            <p className="font-heading mt-5 text-xl font-bold text-foreground">
              No content generated yet
            </p>
            <p className="mt-2 max-w-sm text-sm leading-relaxed text-zo-text-muted">
              {serverPipelineActive && !isFullProposalRunning
                ? `Generation already in progress — ${
                    inProgressPhaseLabel(
                      research!.pipelineCheckpoint!.inProgressPhase!
                    )
                  }. Wait for it to finish instead of starting another run.`
                : "Generate the full proposal (Sections 1–3 + RFP-specific sections)."}
            </p>
            <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
              <button
                type="button"
                onClick={() => void handleGenerateFullProposal()}
                disabled={anyPipelineRunning}
                className="zo-btn disabled:opacity-60"
              >
                Generate Proposal
              </button>
              {anyPipelineRunning ? (
                <button
                  type="button"
                  onClick={() => void handleStopPipeline()}
                  className="rounded-xl border border-red-300 bg-white px-5 py-2.5 text-sm font-semibold text-red-700 transition-smooth hover:bg-red-50"
                >
                  Stop
                </button>
              ) : null}
            </div>
          </div>
        )}
      </TabPanel>


      {/* Export tab */}
      <TabPanel id="export" activeTab={activeTab} className="proposal-workspace-tab proposal-workspace-tab--natural">
        <div className="proposal-submit-tab flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="grid min-h-0 flex-1 gap-0 overflow-hidden lg:grid-cols-[minmax(0,1fr)_minmax(16rem,20rem)]">
            <div className="flex min-h-0 min-w-0 flex-col overflow-hidden border-b border-zo-border lg:border-b-0 lg:border-r">
              <div className="flex shrink-0 items-center justify-between gap-2 border-b border-zo-border/60 px-3 py-2 md:px-4">
                <p className="text-xs font-semibold uppercase tracking-wide text-zo-text-muted">
                  Full proposal preview
                </p>
                <span className="text-[11px] tabular-nums text-zo-text-muted">
                  {manuscriptProgress.complete}/{manuscriptProgress.total} sections
                </span>
              </div>
              {fullManuscript ? (
                <div className="proposal-content-layout flex-1 min-h-0">
                  <nav className="proposal-on-page-nav" aria-label="Jump to section">
                    <p className="proposal-on-page-nav-label text-[10px] font-semibold text-zo-text-muted">
                      Jump to
                    </p>
                    <ul className="proposal-on-page-nav-list mt-2 space-y-0.5">
                      {manuscriptSections.map((section, index) => (
                        <li key={section.id}>
                          <button
                            type="button"
                            className={`proposal-on-page-link w-full text-left ${
                              highlightedSectionId === section.id ||
                              selectedSectionId === section.id
                                ? "is-active"
                                : ""
                            }`}
                            title={section.title}
                            onClick={() => scrollToManuscriptSection(section.id)}
                          >
                            <span className="proposal-on-page-num">{index + 1}</span>
                            <span className="proposal-on-page-title">
                              {section.title}
                              {!section.content?.trim() ? " · …" : ""}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </nav>
                  <div
                    ref={submitScrollRef}
                    className="proposal-content-scroll proposal-content-manuscript-pane proposal-review-manuscript custom-scrollbar min-h-0"
                  >
                    {manuscriptSections.map((section, index) =>
                      section.content?.trim() ? (
                        <article
                          key={section.id}
                          id={section.id}
                          className={`proposal-content-article proposal-content-article--read scroll-mt-24 ${
                            highlightedSectionId === section.id ? "is-flag-target" : ""
                          }`}
                        >
                          <h3 className="proposal-content-section-title">
                            <span className="text-zo-text-muted">{index + 1}.</span>{" "}
                            {section.title}
                          </h3>
                          <div className="proposal-prose proposal-prose--manuscript mt-4">
                            <MarkdownReportBody
                              body={section.content}
                              variant="document"
                            />
                          </div>
                        </article>
                      ) : (
                        <article
                          key={section.id}
                          id={section.id}
                          className={`proposal-content-article scroll-mt-32 border border-dashed border-zo-border/80 bg-[var(--zo-input-bg)]/40 opacity-90 ${
                            highlightedSectionId === section.id ? "is-flag-target" : ""
                          }`}
                        >
                          <div className="flex flex-wrap items-center justify-between gap-3">
                            <div className="flex min-w-0 items-center gap-3">
                              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-zo-border bg-white text-sm font-bold text-zo-text-muted">
                                {index + 1}
                              </span>
                              <div className="min-w-0">
                                <h3 className="text-[1.05rem] font-bold leading-tight tracking-tight text-foreground">
                                  {section.title}
                                </h3>
                                <p className="mt-0.5 text-[11px] font-medium text-zo-orange">
                                  Not drafted yet
                                </p>
                              </div>
                            </div>
                            <button
                              type="button"
                              className="zo-btn !px-3 !py-1.5 !text-xs"
                              onClick={() => selectSection(section.id)}
                            >
                              Open editor
                            </button>
                          </div>
                        </article>
                      )
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex min-h-[16rem] flex-1 flex-col items-center justify-center px-4 text-center">
                  <p className="text-sm text-zo-text-muted">
                    Generate proposal content first, then preview and export
                    it here.
                  </p>
                </div>
              )}
            </div>

            <aside className="flex shrink-0 flex-col gap-4 overflow-y-auto bg-[#fafbfc] px-4 py-4 md:px-5">
              <div>
                <h3 className="font-heading text-base font-bold text-foreground">
                  Finish &amp; export
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-zo-text-muted">
                  Read the manuscript, then download Word for layout and PDF.
                </p>
              </div>

              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => void handleDownloadDocx()}
                  disabled={
                    !fullManuscript || isDownloadingDocx || anyPipelineRunning
                  }
                  className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-[#0b2f6b] bg-[#0b2f6b] px-4 py-3 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#0a2758] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {isDownloadingDocx
                    ? "Preparing Word file…"
                    : docxDownloaded
                      ? "Download started"
                      : "Download Word (.docx)"}
                </button>
                <p className="text-[11px] leading-relaxed text-zo-text-muted">
                  Same headings, lists, tables, and designer notes as the preview
                  — opens in Microsoft Word or Google Docs.
                </p>

                {docxDownloadError ? (
                  <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-800">
                    {docxDownloadError}
                  </p>
                ) : null}
              </div>

              {gapResolveNotice ? (
                <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs leading-relaxed text-emerald-900">
                  {gapResolveNotice}
                </p>
              ) : null}
              {gapResolveError ? (
                <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-relaxed text-rose-800">
                  {gapResolveError}
                </p>
              ) : null}
            </aside>
          </div>
        </div>
      </TabPanel>
      </div>

      {typeof document !== "undefined" && activeRevision && revisionDrawerSectionId
        ? createPortal(
            <>
              <button
                type="button"
                className="proposal-revision-drawer-backdrop"
                aria-label="Close revision summary"
                onClick={() => setRevisionDrawerSectionId(null)}
              />
              <div
                className="proposal-revision-drawer"
                role="dialog"
                aria-labelledby="proposal-revision-drawer-title"
              >
                {revisionDrawerSection ? (
                  <p
                    id="proposal-revision-drawer-title"
                    className="shrink-0 border-b border-zo-border/70 px-4 py-2.5 text-sm font-semibold text-foreground"
                  >
                    {revisionDrawerSection.title}
                  </p>
                ) : null}
                <SectionRevisionCompare
                  before={activeRevision.before}
                  after={activeRevision.after}
                  summary={activeRevision.summary}
                  instruction={activeRevision.instruction}
                  onDismiss={() => dismissSectionRevision(revisionDrawerSectionId)}
                />
              </div>
            </>,
            document.body
          )
        : null}
      {resetConfirmOpen && typeof document !== "undefined"
        ? createPortal(
            <div
              className="fixed inset-0 z-[200] flex items-center justify-center p-4 sm:p-6"
              role="dialog"
              aria-modal="true"
              aria-labelledby="reset-draft-title"
            >
              <button
                type="button"
                className="absolute inset-0 bg-slate-900/20 backdrop-blur-[2px]"
                aria-label="Close reset confirmation"
                disabled={isResettingDraft}
                onClick={() => !isResettingDraft && setResetConfirmOpen(false)}
              />
              <div className="relative z-10 w-full max-w-md rounded-2xl border border-zo-border bg-white p-6 shadow-[0_24px_64px_rgba(15,23,42,0.12)]">
                <h2
                  id="reset-draft-title"
                  className="font-heading text-lg font-bold text-foreground"
                >
                  Reset outline and clear all generated content?
                </h2>
                <p className="mt-2 text-sm leading-relaxed text-zo-text-secondary">
                  This will:
                </p>
                <ul className="mt-2 list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-zo-text-secondary">
                  <li>Clear the live draft sections</li>
                  <li>
                    Delete pipeline checkpoints and research cache from Supabase
                  </li>
                  <li>Cancel any generation currently running</li>
                </ul>
                <p className="mt-3 text-sm leading-relaxed text-zo-text-muted">
                  Use Saved version to load an earlier checkpoint if one exists.
                </p>
                <div className="mt-6 flex flex-wrap justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setResetConfirmOpen(false)}
                    disabled={isResettingDraft}
                    className="zo-btn secondary !py-2.5 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleResetOutline()}
                    disabled={isResettingDraft}
                    className="zo-btn !border-zo-danger !bg-zo-danger !py-2.5 hover:!bg-red-700 disabled:opacity-50"
                  >
                    {isResettingDraft ? "Resetting…" : "Reset draft"}
                  </button>
                </div>
              </div>
            </div>,
            document.body
          )
        : null}
    </section>
  );
}
