"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { IconSwitch } from "@/components/ui/icons";
import {
  ArrowUpRight,
  Ban,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Copy,
  Filter,
  Loader2,
  RefreshCw,
  Search,
  Sparkles,
} from "lucide-react";
import {
  enrichmentRows,
  filterLeads,
  hrefFor,
  personRows,
  preparationState,
  shouldLoadProspectInputs,
  type CompanyEnrichment,
  type EnrichmentStatus,
  type LeadFilter,
} from "@/leads/lead-finder-utils";

export interface LeadRow {
  id: string;
  name: string | null;
  email: string;
  owner: string | null;
  company: string | null;
  industry: string | null;
  location: string | null;
  last_activity: string | null;
  score: number;
  band: string;
  breakdown: Record<string, number>;
  reasons: string[];
  disqualified_reason: string | null;
}

export interface LeadsPayload {
  stats: {
    total: number;
    scored: number;
    disqualified: number;
    hot: number;
    warm: number;
    cool: number;
  };
  rationale: string;
  leads: LeadRow[];
}

interface AiSummary {
  summary: string | null;
  open_questions: string[];
  watch_outs: string[];
}

interface Brief {
  who: string;
  company: string | null;
  industry: string | null;
  location: string | null;
  company_data_source: string | null;
  case_studies: string[];
  case_studies_source?: string;
  why: string[];
  next_step: string;
  ai_available?: boolean;
  ai?: AiSummary;
  ai_error?: string;
}

const FILTERS: LeadFilter[] = ["All", "Hot", "Warm", "Cool", "Excluded"];

const BAND_STYLE: Record<string, string> = {
  Hot: "border-red-200 bg-red-50 text-red-700",
  Warm: "border-amber-200 bg-amber-50 text-amber-800",
  Cool: "border-slate-200 bg-slate-100 text-slate-600",
};

const DIMENSION_MAX: Record<string, number> = {
  industry_fit: 40,
  geography: 25,
  contact_quality: 20,
  engagement_recency: 15,
};

function Score({ lead }: { lead: LeadRow }) {
  if (lead.disqualified_reason) {
    return <span className="text-lg font-semibold text-stone-400">—</span>;
  }
  return (
    <span
      className={`grid h-11 w-11 place-items-center rounded-full border text-sm font-bold ${
        BAND_STYLE[lead.band] ?? BAND_STYLE.Cool
      }`}
    >
      {lead.score}
    </span>
  );
}

function initials(lead: LeadRow): string {
  const source = lead.name?.trim() || lead.email.split("@")[0] || "?";
  const parts = source.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

function activityLabel(value: string | null): string {
  if (!value) return "No recent activity";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 10);
}

function EnrichmentPanel({
  enrichment,
  status,
  onEnrich,
}: {
  enrichment?: CompanyEnrichment;
  status: EnrichmentStatus;
  onEnrich: () => void;
}) {
  const companyLinkedIn = hrefFor(enrichment?.linkedin_url);
  const website = hrefFor(enrichment?.website);
  const personLinkedIn = hrefFor(enrichment?.person?.linkedin_url);
  const busy = status === "loading";

  return (
    <section className="rounded-xl border border-[#274742]/15 bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Building2 className="h-4 w-4 text-[#274742]" />
          <h3 className="font-cabin text-xl font-semibold tracking-[-0.025em]">Company enrichment</h3>
        </div>
        <button
          type="button"
          onClick={onEnrich}
          disabled={busy}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[#274742] px-3 py-2 text-xs font-bold text-white transition hover:bg-[#1e3632] disabled:opacity-50"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Building2 className="h-3.5 w-3.5" />}
          {enrichment ? "Refresh enrichment" : "Enrich company & contact"}
        </button>
      </div>
      {status === "idle" && !enrichment && (
        <p className="mt-4 text-sm text-[#52635f]">
          Run enrichment to verify company and contact. This is billed per lookup and is not started automatically.
        </p>
      )}
      {busy && (
        <p className="mt-4 flex items-center gap-2 text-sm text-[#52635f]">
          <Loader2 className="h-4 w-4 animate-spin" />
          Verifying company and contact…
        </p>
      )}
      {status === "unavailable" && (
        <p className="mt-4 text-sm text-[#52635f]">
          Verification unavailable. AI prep can still use the research brief.
        </p>
      )}
      {enrichment && (
        <div className="mt-4 space-y-2 text-sm text-[#334541]">
          {enrichment.company_skipped === "hubspot" && (
            <p className="text-xs text-[#687875]">Company is from HubSpot and was not overwritten.</p>
          )}
          {enrichmentRows(enrichment).map((row) => (
            <p key={row.label}>
              <span className="text-[#687875]">{row.label}:</span> {row.value}
            </p>
          ))}
          {companyLinkedIn && (
            <p>
              <span className="text-[#687875]">Company LinkedIn:</span>{" "}
              <a className="font-semibold text-[#274742] underline-offset-2 hover:underline" href={companyLinkedIn} target="_blank" rel="noreferrer">
                Company page
              </a>
            </p>
          )}
          {website && (
            <p>
              <span className="text-[#687875]">Website:</span>{" "}
              <a className="font-semibold text-[#274742] underline-offset-2 hover:underline" href={website} target="_blank" rel="noreferrer">
                {enrichment.website}
              </a>
            </p>
          )}
          {enrichment.name_conflict && (
            <p className="rounded-lg bg-[#fff8e5] p-3 text-xs leading-5 text-[#795d00]">
              Monid also returned “{enrichment.name_conflict}”, which does not match this domain.
              That firmographic record was not used.
            </p>
          )}
          {enrichment.basis && (
            <p className="rounded-lg bg-[#fff8e5] p-3 text-xs leading-5 text-[#795d00]">
              {enrichment.confidence} confidence. {enrichment.basis}
            </p>
          )}
          {enrichment.person && (
            <div className="space-y-2 border-t border-[#274742]/10 pt-4">
              <p className="text-xs font-bold uppercase tracking-[0.1em] text-[#52635f]">Contact</p>
              {personRows(enrichment.person).map((row) => (
                <p key={row.label}>
                  <span className="text-[#687875]">{row.label}:</span> {row.value}
                </p>
              ))}
              {personLinkedIn && (
                <p>
                  <span className="text-[#687875]">LinkedIn:</span>{" "}
                  <a className="font-semibold text-[#274742] underline-offset-2 hover:underline" href={personLinkedIn} target="_blank" rel="noreferrer">
                    Profile
                  </a>
                </p>
              )}
              <p className="rounded-lg bg-[#fff8e5] p-3 text-xs leading-5 text-[#795d00]">
                {enrichment.person.confidence} confidence. {enrichment.person.basis}
              </p>
            </div>
          )}
          {enrichment.person_error && !enrichment.person && (
            <p className="rounded-lg bg-[#fff8e5] p-3 text-xs leading-5 text-[#795d00]">
              No person match for this email. Company data above can still be used.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export function LeadFinderClient({ payload }: { payload: LeadsPayload }) {
  const router = useRouter();
  const [openId, setOpenId] = useState<string | null>(null);
  const [briefs, setBriefs] = useState<Record<string, Brief>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState<LeadFilter>("All");
  const [enrichments, setEnrichments] = useState<Record<string, CompanyEnrichment>>({});
  const [enrichmentStatuses, setEnrichmentStatuses] = useState<Record<string, EnrichmentStatus>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [outreachReady, setOutreachReady] = useState<Record<string, boolean>>({});
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const visible = useMemo(() => filterLeads(payload.leads, query, activeFilter), [payload.leads, query, activeFilter]);
  const focusLeads = useMemo(() => payload.leads.filter((lead) => !lead.disqualified_reason).slice(0, 3), [payload.leads]);
  const qualifiedRate = payload.stats.total ? Math.round((payload.stats.scored / payload.stats.total) * 100) : 0;

  async function toggle(lead: LeadRow) {
    if (lead.disqualified_reason) return;
    if (openId === lead.id) { setOpenId(null); return; }
    setOpenId(lead.id);
    if (!shouldLoadProspectInputs(Boolean(briefs[lead.id]))) return;
    void loadBrief(lead.id);
  }
  async function loadBrief(id: string) { setLoadingId(id); try { const response = await fetch(`/api/leads/${id}/brief`); if (!response.ok) throw new Error("Could not load this prospect brief."); const brief: Brief = await response.json(); setBriefs((prev) => ({ ...prev, [id]: brief })); } catch (error) { setErrors((prev) => ({ ...prev, [id]: error instanceof Error ? error.message : "Could not load the brief." })); } finally { setLoadingId(null); } }
  async function runAiSummary(id: string) { setAiBusy(id); setErrors((prev) => ({ ...prev, [id]: "" })); try { const response = await fetch(`/api/leads/${id}/brief`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(enrichments[id] ?? null) }); const brief: Brief = await response.json(); if (!response.ok) throw new Error("Could not generate preparation notes."); setBriefs((prev) => ({ ...prev, [id]: brief })); } catch (error) { setErrors((prev) => ({ ...prev, [id]: error instanceof Error ? error.message : "AI preparation failed." })); } finally { setAiBusy(null); } }
  async function runEnrich(id: string) {
    setEnrichmentStatuses((prev) => ({ ...prev, [id]: "loading" }));
    setErrors((prev) => ({ ...prev, [id]: "" }));
    try {
      const response = await fetch(`/api/leads/${id}/enrich`, { method: "POST" });
      const enrichment = await response.json();
      if (!response.ok) throw new Error(typeof enrichment?.detail === "string" ? enrichment.detail : "Enrichment failed.");
      setEnrichments((prev) => ({ ...prev, [id]: enrichment }));
      setEnrichmentStatuses((prev) => ({ ...prev, [id]: "ready" }));
    } catch (error) {
      console.warn("[lead-finder] enrichment unavailable:", id, error);
      setEnrichmentStatuses((prev) => ({ ...prev, [id]: "unavailable" }));
      setErrors((prev) => ({
        ...prev,
        [id]: error instanceof Error ? error.message : "Enrichment failed.",
      }));
    }
  }
  async function copyBrief(lead: LeadRow, brief: Brief) { await navigator.clipboard?.writeText([`${lead.name ?? lead.email} — outreach preparation`, `Company: ${lead.company ?? "Unverified"}`, `Why now: ${lead.reasons.join("; ")}`, brief.ai?.summary ?? brief.next_step, ...(brief.ai?.open_questions ?? [])].join("\n")); setCopiedId(lead.id); window.setTimeout(() => setCopiedId(null), 1800); }

  function handleLogout() {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    router.push("/login");
  }

  return <main className="min-h-screen bg-[#f4f3ef] text-[#0a0f1a]"><header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-3 border-b border-[#274742]/15 bg-[#f4f3ef]/95 px-5 py-2.5 backdrop-blur-xl sm:px-8 lg:px-10"><div className="flex items-center gap-2 text-sm font-semibold text-[#274742]"><span className="grid h-6 w-6 place-items-center rounded-full bg-[#ef5018] text-xs text-white">zö</span> Prospect Operations</div><div className="flex items-center gap-2 md:gap-3"><Link href="/choose" className="zo-btn secondary !py-3" aria-label="Switch workspace"><IconSwitch className="h-4 w-4" /><span className="hidden sm:inline">Switch Workspace</span></Link><button type="button" onClick={handleLogout} className="zo-btn secondary !py-3 cursor-pointer">Logout</button></div></header><div className="mx-auto max-w-[1440px] px-5 py-6 sm:px-8 lg:px-10">
    <header className="border-b border-[#274742]/15 pb-6"><div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between"><div><h1 className="max-w-2xl font-cabin text-4xl font-semibold tracking-[-0.045em] text-[#0a0f1a] sm:text-5xl">Turn activity into the next right conversation.</h1></div><div className="flex items-center gap-3 rounded-2xl border border-[#274742]/15 bg-white px-4 py-3 shadow-[0_12px_32px_rgba(39,71,66,0.08)]"><span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#6b9b48] opacity-50" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[#5d8840]" /></span><div><p className="text-sm font-semibold">HubSpot source connected</p><p className="text-xs text-[#52635f]">Snapshot loaded • contact data under review</p></div><button type="button" className="ml-2 grid h-9 w-9 place-items-center rounded-lg text-[#274742] transition hover:bg-[#edf3f1]" aria-label="Refresh HubSpot snapshot"><RefreshCw className="h-4 w-4" /></button></div></div></header>
    <section className="grid gap-4 py-7 lg:grid-cols-[1.15fr_1.85fr]"><div className="rounded-2xl bg-[#274742] p-6 text-white shadow-[0_18px_42px_rgba(39,71,66,0.16)]"><p className="text-sm font-semibold text-[#d5e1d6]">Pipeline signal</p><div className="mt-5 flex items-end gap-4"><span className="font-cabin text-6xl font-semibold tracking-[-0.07em]">{qualifiedRate}%</span><span className="mb-2 max-w-[16ch] text-sm leading-5 text-[#d5e1d6]">of imported contacts are in a usable sales queue</span></div><div className="mt-7 grid grid-cols-3 gap-3 border-t border-white/15 pt-5 text-sm"><div><p className="font-cabin text-2xl font-semibold">{payload.stats.hot}</p><p className="text-[#d5e1d6]">High priority</p></div><div><p className="font-cabin text-2xl font-semibold">{payload.stats.warm}</p><p className="text-[#d5e1d6]">Worth warming</p></div><div><p className="font-cabin text-2xl font-semibold">{payload.stats.disqualified}</p><p className="text-[#d5e1d6]">Held back</p></div></div></div><div className="rounded-2xl border border-[#274742]/15 bg-white p-6 shadow-[0_12px_32px_rgba(39,71,66,0.06)]"><div className="flex items-center justify-between"><h2 className="font-cabin text-2xl font-semibold tracking-[-0.035em]">System activity</h2><span className="text-xs font-semibold text-[#52635f]">Preparing the queue</span></div><div className="mt-6 grid gap-4 sm:grid-cols-3"><div className="border-t border-[#ef5018] pt-3"><p className="text-xs font-semibold uppercase tracking-[0.13em] text-[#52635f]">1. Import</p><p className="mt-2 text-sm font-semibold">{payload.stats.total} contacts indexed</p><p className="mt-1 text-xs leading-5 text-[#687875]">HubSpot snapshot available for review.</p></div><div className="border-t border-[#274742] pt-3"><p className="text-xs font-semibold uppercase tracking-[0.13em] text-[#52635f]">2. Prioritize</p><p className="mt-2 text-sm font-semibold">{payload.stats.scored} prospects scored</p><p className="mt-1 text-xs leading-5 text-[#687875]">Fit, geography, contact quality, and activity are weighted.</p></div><div className="border-t border-[#90882a] pt-3"><p className="text-xs font-semibold uppercase tracking-[0.13em] text-[#52635f]">3. Prepare</p><p className="mt-2 text-sm font-semibold">{payload.stats.hot + payload.stats.warm} ready to investigate</p><p className="mt-1 text-xs leading-5 text-[#687875]">Open a prospect to review AI research and outreach prep.</p></div></div></div></section>
    <section className="border-y border-[#274742]/15 py-6"><div className="flex items-center justify-between gap-4"><div><h2 className="font-cabin text-2xl font-semibold tracking-[-0.035em]">Focus now</h2><p className="mt-1 text-sm text-[#52635f]">The clearest places to spend the team’s attention.</p></div><span className="hidden items-center gap-1 text-sm font-semibold text-[#274742] sm:flex">See full queue <ArrowUpRight className="h-4 w-4" /></span></div><div className="mt-5 grid gap-3 md:grid-cols-3">{focusLeads.map((lead) => <button key={lead.id} type="button" onClick={() => toggle(lead)} className="group flex items-center gap-4 rounded-xl border border-[#274742]/15 bg-white p-4 text-left transition hover:-translate-y-0.5 hover:border-[#ef5018]/35 hover:shadow-[0_14px_28px_rgba(39,71,66,0.09)]"><Score lead={lead} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-bold">{lead.name ?? lead.email}</span><span className="mt-0.5 block truncate text-xs text-[#52635f]">{lead.company ?? "Company needs verification"}</span><span className="mt-2 block truncate text-xs font-semibold text-[#274742]">{lead.reasons[0]}</span></span><ChevronRight className="h-4 w-4 text-[#687875] transition group-hover:text-[#ef5018]" /></button>)}</div></section>
    <section className="py-7"><div className="flex flex-col justify-between gap-5 md:flex-row md:items-end"><div><h2 className="font-cabin text-3xl font-semibold tracking-[-0.04em]">Prospect queue</h2><p className="mt-1 text-sm text-[#52635f]">{visible.length} shown from {payload.stats.total} imported contacts</p></div><div className="flex w-full flex-col gap-2 sm:flex-row md:w-auto"><label className="relative block min-w-0 sm:w-72"><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#687875]" /><span className="sr-only">Search prospects</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search name, company, industry…" className="h-10 w-full rounded-lg border border-[#274742]/20 bg-white pl-10 pr-3 text-sm outline-none transition placeholder:text-[#83908d] focus:border-[#ef5018] focus:ring-2 focus:ring-[#ef5018]/15" /></label><button type="button" className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-[#274742]/20 bg-white px-3 text-sm font-semibold text-[#274742] transition hover:bg-[#edf3f1]"><Filter className="h-4 w-4" /> Filters</button></div></div><div className="mt-5 flex gap-2 overflow-x-auto pb-1">{FILTERS.map((filter) => <button key={filter} type="button" onClick={() => setActiveFilter(filter)} aria-pressed={activeFilter === filter} className={`shrink-0 rounded-full border px-4 py-2 text-sm font-semibold transition ${activeFilter === filter ? "border-[#274742] bg-[#274742] text-white" : "border-[#274742]/20 bg-white text-[#274742] hover:border-[#274742]/45"}`}>{filter}</button>)}</div>
    <div className="mt-5 overflow-hidden rounded-2xl border border-[#274742]/15 bg-white shadow-[0_14px_34px_rgba(39,71,66,0.06)]"><div className="hidden grid-cols-[80px_minmax(220px,1.4fr)_minmax(140px,.85fr)_minmax(170px,.9fr)_110px_30px] gap-4 border-b border-[#274742]/12 bg-[#edf3f1]/70 px-5 py-3 text-[11px] font-bold uppercase tracking-[0.12em] text-[#52635f] lg:grid"><span>Score</span><span>Prospect</span><span>Signal</span><span>Owner & activity</span><span>Readiness</span><span /></div>{visible.map((lead) => { const brief = briefs[lead.id]; const open = openId === lead.id; const excluded = Boolean(lead.disqualified_reason); const preparation = preparationState({ briefLoaded: Boolean(brief), enrichmentStatus: enrichmentStatuses[lead.id] ?? "idle" }); return <div key={lead.id} className={excluded ? "bg-stone-50/80" : ""}><button type="button" onClick={() => toggle(lead)} disabled={excluded} className="grid w-full gap-4 border-b border-[#274742]/10 px-5 py-4 text-left transition hover:bg-[#fffaf5] disabled:cursor-default lg:grid-cols-[80px_minmax(220px,1.4fr)_minmax(140px,.85fr)_minmax(170px,.9fr)_110px_30px] lg:items-center"><div className="flex items-center gap-3"><Score lead={lead} /></div><div className="min-w-0"><span className="flex items-center gap-2"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-[#edf3f1] text-[10px] font-bold text-[#274742]">{initials(lead)}</span><span className="truncate text-sm font-bold">{lead.name ?? lead.email}</span></span><span className="mt-1 block truncate text-xs text-[#52635f]">{lead.company ?? lead.email} {lead.industry ? `· ${lead.industry}` : ""}</span></div><div className="min-w-0"><span className="block truncate text-xs font-semibold text-[#274742]">{excluded ? "Excluded from outreach" : lead.reasons[0]}</span><span className="mt-1 block truncate text-xs text-[#687875]">{lead.location ?? "Location to verify"}</span></div><div className="text-xs text-[#52635f]"><span className="block font-semibold text-[#0a0f1a]">{lead.owner ?? "Unassigned"}</span><span className="mt-1 block">{activityLabel(lead.last_activity)}</span></div><div>{excluded ? <span className="inline-flex items-center gap-1 text-xs font-semibold text-stone-500"><Ban className="h-3.5 w-3.5" /> Held back</span> : <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-bold ${outreachReady[lead.id] ? "border-[#274742]/15 bg-[#edf3f1] text-[#274742]" : BAND_STYLE[lead.band] ?? BAND_STYLE.Cool}`}>{outreachReady[lead.id] ? "Ready to review" : lead.band}</span>}</div><ChevronDown className={`hidden h-4 w-4 text-[#687875] transition lg:block ${open ? "rotate-180" : ""}`} /></button>
    {open && !excluded && <div className="border-b border-[#274742]/10 bg-[#faf9f6] px-5 py-6"><div className="grid gap-6 xl:grid-cols-[.9fr_1.1fr]"><div className="space-y-5"><section><div className="flex items-center justify-between"><h3 className="font-cabin text-xl font-semibold tracking-[-0.025em]">Why this prospect</h3><span className="text-xs text-[#52635f]">Transparent score</span></div><div className="mt-4 space-y-3">{Object.entries(lead.breakdown).map(([dimension, points]) => <div key={dimension} className="grid grid-cols-[122px_1fr_44px] items-center gap-3"><span className="text-xs capitalize text-[#52635f]">{dimension.replace(/_/g, " ")}</span><span className="h-1.5 overflow-hidden rounded-full bg-[#dce6e1]"><span className="block h-full rounded-full bg-[#ef5018]" style={{ width: `${(points / (DIMENSION_MAX[dimension] ?? 40)) * 100}%` }} /></span><span className="text-right text-xs font-bold text-[#274742]">{points}/{DIMENSION_MAX[dimension] ?? "?"}</span></div>)}</div><ul className="mt-5 space-y-2">{lead.reasons.map((reason) => <li key={reason} className="flex gap-2 text-sm leading-5 text-[#334541]"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[#5d8840]" />{reason}</li>)}</ul></section>{loadingId === lead.id && <p className="flex items-center gap-2 text-sm text-[#52635f]"><Loader2 className="h-4 w-4 animate-spin" />Building a prospect brief…</p>}{errors[lead.id] && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{errors[lead.id]}</p>}{brief && <section className="rounded-xl border border-[#274742]/15 bg-white p-5"><div className="flex items-center justify-between gap-3"><h3 className="font-cabin text-xl font-semibold tracking-[-0.025em]">Research brief</h3><span className="text-xs font-semibold text-[#52635f]">{brief.company_data_source === "hubspot" ? "CRM record" : "Company data inferred"}</span></div><dl className="mt-4 grid grid-cols-[105px_1fr] gap-y-2 text-sm"><dt className="text-[#687875]">Contact</dt><dd>{lead.email}</dd><dt className="text-[#687875]">Case studies</dt><dd>{brief.case_studies.length ? <ul className="space-y-1">{brief.case_studies.map((study) => <li key={study}>{study}</li>)}</ul> : brief.case_studies_source === "supermemory" ? "No matching case studies in the knowledge base" : "No industry match yet"}{brief.case_studies_source === "supermemory" && brief.case_studies.length > 0 ? <p className="mt-1 text-xs text-[#687875]">From the knowledge base — confirm before using in conversation.</p> : null}</dd><dt className="text-[#687875]">Next step</dt><dd>{brief.next_step}</dd></dl>{brief.company_data_source !== "hubspot" && <p className="mt-4 rounded-lg bg-[#fff8e5] p-3 text-xs leading-5 text-[#795d00]">Company details were inferred from the email domain. Verify before making a decision or using them externally.</p>}</section>}</div>
    <div className="space-y-5"><EnrichmentPanel enrichment={enrichments[lead.id]} status={enrichmentStatuses[lead.id] ?? "idle"} onEnrich={() => void runEnrich(lead.id)} />{brief && <><section className="rounded-xl border border-[#274742]/15 bg-white p-5"><div className="flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-[#ef5018]" /><h3 className="font-cabin text-xl font-semibold tracking-[-0.025em]">AI preparation</h3></div><p className="mt-1 text-xs leading-5 text-[#52635f]">Research support for human review. Never treated as a verified source.</p><p aria-live="polite" className="mt-2 text-xs font-semibold text-[#52635f]">{preparation.label}</p></div><button type="button" onClick={() => runAiSummary(lead.id)} disabled={!preparation.ready || aiBusy === lead.id || brief.ai_available === false} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-[#274742] px-3 py-2 text-xs font-bold text-white transition hover:bg-[#1e3632] disabled:opacity-50">{aiBusy === lead.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}{brief.ai ? "Refresh notes" : "Generate preparation"}</button></div>{brief.ai ? <div className="mt-5 space-y-4"><p className="text-sm leading-6 text-[#334541]">{brief.ai.summary}</p>{brief.ai.open_questions.length > 0 && <div><p className="text-xs font-bold uppercase tracking-[0.1em] text-[#52635f]">Confirm before contact</p><ul className="mt-2 space-y-2 text-sm text-[#334541]">{brief.ai.open_questions.map((question) => <li key={question} className="flex gap-2"><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#ef5018]" />{question}</li>)}</ul></div>}{brief.ai.watch_outs.length > 0 && <div className="rounded-lg bg-[#fff8e5] p-3"><p className="text-xs font-bold text-[#795d00]">Watch-outs</p><p className="mt-1 text-sm leading-5 text-[#795d00]">{brief.ai.watch_outs.join(" ")}</p></div>}</div> : <p className="mt-5 text-sm text-[#52635f]">Generate preparation once the available research is ready.</p>}</section><section className="rounded-xl bg-[#274742] p-5 text-white"><div className="flex items-center justify-between gap-3"><div><div className="flex items-center gap-2"><ClipboardCheck className="h-4 w-4 text-[#ffd652]" /><h3 className="font-cabin text-xl font-semibold tracking-[-0.025em]">Outreach review</h3></div><p className="mt-1 text-xs leading-5 text-[#d5e1d6]">Prepare the context; a person owns the message and the send.</p></div>{outreachReady[lead.id] && <span className="rounded-full bg-white/15 px-2.5 py-1 text-xs font-bold">Ready</span>}</div><div className="mt-5 grid gap-2 sm:grid-cols-2"><button type="button" onClick={() => setOutreachReady((prev) => ({ ...prev, [lead.id]: !prev[lead.id] }))} className="inline-flex items-center justify-center gap-2 rounded-lg bg-[#ef5018] px-3 py-2.5 text-sm font-bold text-white transition hover:bg-[#d84413]"><ClipboardCheck className="h-4 w-4" />{outreachReady[lead.id] ? "Remove from review" : "Mark ready for review"}</button><button type="button" onClick={() => copyBrief(lead, brief)} className="inline-flex items-center justify-center gap-2 rounded-lg border border-white/25 px-3 py-2.5 text-sm font-bold transition hover:bg-white/10"><Copy className="h-4 w-4" />{copiedId === lead.id ? "Prep copied" : "Copy preparation"}</button></div><p className="mt-4 text-xs leading-5 text-[#d5e1d6]">No message is generated, queued, or sent here. Use this brief to make the outreach judgment yourself.</p></section></>}</div></div></div>}</div>; })}{visible.length === 0 && <div className="px-5 py-14 text-center"><Search className="mx-auto h-5 w-5 text-[#687875]" /><p className="mt-3 text-sm font-bold">No prospects match this view</p><button type="button" onClick={() => { setQuery(""); setActiveFilter("All"); }} className="mt-3 text-sm font-bold text-[#ef5018]">Clear filters</button></div>}</div></section>
  </div></main>;
}
