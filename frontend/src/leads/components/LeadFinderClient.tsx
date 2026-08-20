"use client";

import { useState } from "react";
import { ChevronDown, Ban, Loader2, Sparkles, Building2 } from "lucide-react";

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
  why: string[];
  next_step: string;
  ai_available?: boolean;
  ai?: AiSummary;
  ai_error?: string;
}

interface Enrichment {
  company_name: string | null;
  industry: string | null;
  city: string | null;
  state: string | null;
  employee_band: string | null;
  what_they_do: string | null;
  confidence: "high" | "medium" | "low" | string;
  basis: string;
}

const CONFIDENCE_PILL: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-700 border-emerald-200",
  medium: "bg-amber-100 text-amber-800 border-amber-200",
  low: "bg-slate-100 text-slate-600 border-slate-200",
};

const BAND_PILL: Record<string, string> = {
  Hot: "bg-red-100 text-red-700 border-red-200",
  Warm: "bg-amber-100 text-amber-800 border-amber-200",
  Cool: "bg-slate-100 text-slate-600 border-slate-200",
};

const DIMENSION_MAX: Record<string, number> = {
  industry_fit: 40,
  geography: 25,
  contact_quality: 20,
  engagement_recency: 15,
};

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-2xl font-semibold text-slate-900">{value}</div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
    </div>
  );
}

export function LeadFinderClient({ payload }: { payload: LeadsPayload }) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [briefs, setBriefs] = useState<Record<string, Brief>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState<string | null>(null);
  const [enrichBusy, setEnrichBusy] = useState<string | null>(null);
  const [enrichments, setEnrichments] = useState<Record<string, Enrichment>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  async function toggle(lead: LeadRow) {
    if (lead.disqualified_reason) return;
    if (openId === lead.id) {
      setOpenId(null);
      return;
    }
    setOpenId(lead.id);
    if (briefs[lead.id]) return;
    setLoadingId(lead.id);
    try {
      const response = await fetch(`/api/leads/${lead.id}/brief`);
      if (response.ok) {
        const brief: Brief = await response.json();
        setBriefs((prev) => ({ ...prev, [lead.id]: brief }));
      }
    } finally {
      setLoadingId(null);
    }
  }

  async function runAiSummary(leadId: string) {
    setAiBusy(leadId);
    setErrors((prev) => ({ ...prev, [leadId]: "" }));
    try {
      const response = await fetch(`/api/leads/${leadId}/brief?ai=true`);
      const brief: Brief = await response.json();
      if (!response.ok) throw new Error("Could not generate the summary.");
      setBriefs((prev) => ({ ...prev, [leadId]: brief }));
    } catch (error) {
      setErrors((prev) => ({
        ...prev,
        [leadId]: error instanceof Error ? error.message : "AI summary failed.",
      }));
    } finally {
      setAiBusy(null);
    }
  }

  async function runEnrich(leadId: string) {
    setEnrichBusy(leadId);
    setErrors((prev) => ({ ...prev, [leadId]: "" }));
    try {
      const response = await fetch(`/api/leads/${leadId}/enrich`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail ?? "Enrichment failed.");
      setEnrichments((prev) => ({ ...prev, [leadId]: data }));
    } catch (error) {
      setErrors((prev) => ({
        ...prev,
        [leadId]: error instanceof Error ? error.message : "Enrichment failed.",
      }));
    } finally {
      setEnrichBusy(null);
    }
  }

  const { stats, leads } = payload;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold text-slate-900">
          Lead Finder &amp; Outreach Matcher
        </h1>
        <p className="text-sm text-slate-600">
          Wave 3 proof of concept. Static fixture transcribed from HubSpot — no
          HubSpot API, no Apollo enrichment, no visitor intelligence. The system
          stops before outreach and drafts no messaging.
        </p>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <Stat label="Contacts" value={stats.total} />
        <Stat label="Scored" value={stats.scored} />
        <Stat label="Hot" value={stats.hot} />
        <Stat label="Warm" value={stats.warm} />
        <Stat label="Disqualified" value={stats.disqualified} />
      </div>

      <ul className="space-y-2">
        {leads.map((lead) => {
          const disqualified = Boolean(lead.disqualified_reason);
          const brief = briefs[lead.id];
          return (
            <li
              key={lead.id}
              className={`rounded-lg border ${
                disqualified ? "border-slate-200 bg-slate-50/60" : "border-slate-200 bg-white"
              }`}
            >
              <button
                type="button"
                onClick={() => toggle(lead)}
                disabled={disqualified}
                className="flex w-full items-center gap-3 px-4 py-3 text-left disabled:cursor-default"
              >
                <span
                  className={`w-12 shrink-0 text-right text-lg font-semibold tabular-nums ${
                    disqualified ? "text-slate-300" : "text-slate-900"
                  }`}
                >
                  {disqualified ? "—" : lead.score}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-slate-900">
                    {lead.name ?? lead.email}
                  </span>
                  <span className="block truncate text-xs text-slate-500">
                    {[lead.company, lead.industry, lead.location]
                      .filter(Boolean)
                      .join(" · ") || lead.email}
                  </span>
                </span>
                {disqualified ? (
                  <span className="flex items-center gap-1.5 text-xs text-slate-500">
                    <Ban className="h-3.5 w-3.5" />
                    {lead.disqualified_reason}
                  </span>
                ) : (
                  <>
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                        BAND_PILL[lead.band] ?? BAND_PILL.Cool
                      }`}
                    >
                      {lead.band}
                    </span>
                    <ChevronDown
                      className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${
                        openId === lead.id ? "rotate-180" : ""
                      }`}
                    />
                  </>
                )}
              </button>

              {openId === lead.id && (
                <div className="space-y-4 border-t border-slate-100 px-4 py-4 text-sm">
                  <div className="space-y-1.5">
                    {Object.entries(lead.breakdown).map(([dimension, points]) => (
                      <div key={dimension} className="flex items-center gap-3">
                        <span className="w-40 shrink-0 text-xs text-slate-500">
                          {dimension.replace(/_/g, " ")}
                        </span>
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                          <span
                            className="block h-full rounded-full bg-slate-800"
                            style={{
                              width: `${(points / (DIMENSION_MAX[dimension] ?? 40)) * 100}%`,
                            }}
                          />
                        </span>
                        <span className="w-12 text-right text-xs tabular-nums text-slate-600">
                          {points}/{DIMENSION_MAX[dimension] ?? "?"}
                        </span>
                      </div>
                    ))}
                  </div>

                  <ul className="list-disc space-y-1 pl-5 text-slate-600">
                    {lead.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>

                  {loadingId === lead.id && (
                    <p className="flex items-center gap-2 text-xs text-slate-500">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Building brief…
                    </p>
                  )}

                  {brief && (
                    <div className="space-y-3 rounded-md bg-slate-50 p-3">
                      <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Outreach prep brief
                      </div>
                      <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-slate-700">
                        <dt className="text-slate-500">Owner</dt>
                        <dd>{lead.owner ?? "—"}</dd>
                        <dt className="text-slate-500">Email</dt>
                        <dd>{lead.email}</dd>
                        <dt className="text-slate-500">Last activity</dt>
                        <dd>{lead.last_activity ?? "—"}</dd>
                        <dt className="text-slate-500">Case studies</dt>
                        <dd>
                          {brief.case_studies.length
                            ? brief.case_studies.join("; ")
                            : "no industry match"}
                        </dd>
                      </dl>
                      {brief.company_data_source !== "hubspot" && (
                        <p className="text-xs text-amber-700">
                          Company firmographics inferred from the email domain, not
                          read from HubSpot. Verify before acting.
                        </p>
                      )}
                      <p className="text-xs text-slate-500">{brief.next_step}</p>

                      <div className="flex flex-wrap gap-2 border-t border-slate-200 pt-3">
                        <button
                          type="button"
                          onClick={() => runAiSummary(lead.id)}
                          disabled={aiBusy === lead.id || brief.ai_available === false}
                          className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                        >
                          {aiBusy === lead.id ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Sparkles className="h-3.5 w-3.5" />
                          )}
                          {brief.ai ? "Regenerate prep notes" : "AI prep notes"}
                        </button>
                        {brief.company_data_source !== "hubspot" && (
                          <button
                            type="button"
                            onClick={() => runEnrich(lead.id)}
                            disabled={enrichBusy === lead.id}
                            className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                          >
                            {enrichBusy === lead.id ? (
                              <Loader2 className="h-3.5 w-3.5 animate-spin" />
                            ) : (
                              <Building2 className="h-3.5 w-3.5" />
                            )}
                            AI enrich company
                          </button>
                        )}
                      </div>

                      {errors[lead.id] && (
                        <p className="text-xs text-red-600">{errors[lead.id]}</p>
                      )}

                      {brief.ai_error && (
                        <p className="text-xs text-red-600">
                          AI summary failed: {brief.ai_error}
                        </p>
                      )}

                      {brief.ai && (
                        <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3">
                          <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                            <Sparkles className="h-3.5 w-3.5" />
                            AI prep notes
                          </div>
                          {brief.ai.summary && (
                            <p className="text-slate-700">{brief.ai.summary}</p>
                          )}
                          {brief.ai.open_questions.length > 0 && (
                            <div>
                              <div className="text-xs font-medium text-slate-500">
                                Find out before reaching out
                              </div>
                              <ul className="list-disc space-y-0.5 pl-5 text-slate-700">
                                {brief.ai.open_questions.map((question) => (
                                  <li key={question}>{question}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          {brief.ai.watch_outs.length > 0 && (
                            <div>
                              <div className="text-xs font-medium text-slate-500">
                                Watch-outs
                              </div>
                              <ul className="list-disc space-y-0.5 pl-5 text-amber-700">
                                {brief.ai.watch_outs.map((watchOut) => (
                                  <li key={watchOut}>{watchOut}</li>
                                ))}
                              </ul>
                            </div>
                          )}
                          <p className="text-xs text-slate-400">
                            Generated from the record above. Not verified, and
                            deliberately not messaging.
                          </p>
                        </div>
                      )}

                      {enrichments[lead.id] && (
                        <div className="space-y-2 rounded-md border border-slate-200 bg-white p-3">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                              <Building2 className="h-3.5 w-3.5" />
                              AI-inferred firmographics
                            </div>
                            <span
                              className={`rounded-full border px-2 py-0.5 text-xs font-medium ${
                                CONFIDENCE_PILL[enrichments[lead.id].confidence] ??
                                CONFIDENCE_PILL.low
                              }`}
                            >
                              {enrichments[lead.id].confidence} confidence
                            </span>
                          </div>
                          <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-slate-700">
                            <dt className="text-slate-500">Company</dt>
                            <dd>{enrichments[lead.id].company_name ?? "unknown"}</dd>
                            <dt className="text-slate-500">Industry</dt>
                            <dd>{enrichments[lead.id].industry ?? "unknown"}</dd>
                            <dt className="text-slate-500">Location</dt>
                            <dd>
                              {[enrichments[lead.id].city, enrichments[lead.id].state]
                                .filter(Boolean)
                                .join(", ") || "unknown"}
                            </dd>
                            <dt className="text-slate-500">Headcount</dt>
                            <dd>{enrichments[lead.id].employee_band ?? "unknown"}</dd>
                            <dt className="text-slate-500">What they do</dt>
                            <dd>{enrichments[lead.id].what_they_do ?? "unknown"}</dd>
                          </dl>
                          <p className="text-xs text-slate-500">
                            Basis: {enrichments[lead.id].basis}
                          </p>
                          <p className="text-xs text-amber-700">
                            Model guess, not a data provider. Verify before it reaches
                            a client-facing document.
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
