"use client";

import { useState } from "react";
import {
  Sparkles,
  AlertTriangle,
  TrendingUp,
  Lightbulb,
  Loader2,
  RotateCcw,
  Bot,
  Clock,
} from "lucide-react";
import { FadeInItem, FadeInStagger } from "@/components/ui/FadeIn";

export interface AiInsightsData {
  status: string;
  generated_at: string;
  contractor: string;
  source_data: string;
  provider?: string;
  model?: string;
  /** Present on regenerate responses — "ok" | "failed". */
  generated?: string;
  /** Whether the brief was persisted to Supabase. */
  stored?: boolean;
  summary: {
    top_3_risks: string[];
    top_3_wins: string[];
    margin_recommendations: string[];
    leadership_brief_text: string;
  };
  stats?: {
    total_hours: number;
    total_spend: number;
    active_entries: number;
    over_scope_spend: number;
    over_scope_items: number;
    period_label?: string;
  };
}

interface AiInsightsPanelProps {
  onFetchAiInsights: () => Promise<AiInsightsData>;
  persistedInsights: AiInsightsData | null;
  onInsightsGenerated: (data: AiInsightsData) => void;
}

const LOADING_STEPS = [
  "Connecting to Google Sheets and fetching live timesheet data…",
  "Running AI classification on all contractor entries…",
  "Analyzing revision rounds and scope creep patterns…",
  "Synthesizing leadership brief and margin recommendations…",
  "Finalizing financial risk assessment…",
];

export function AiInsightsPanel({
  onFetchAiInsights,
  persistedInsights,
  onInsightsGenerated,
}: AiInsightsPanelProps) {
  const [loading, setLoading] = useState<boolean>(false);
  const [loadingStep, setLoadingStep] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);

  // Use persisted insights from parent (survives tab switches)
  const insights = persistedInsights;

  const handleGenerate = async () => {
    setLoading(true);
    setError(null);
    setLoadingStep(0);

    // Cycle through loading steps while waiting
    const stepInterval = setInterval(() => {
      setLoadingStep((prev) => (prev < LOADING_STEPS.length - 1 ? prev + 1 : prev));
    }, 900);

    try {
      const data = await onFetchAiInsights();
      onInsightsGenerated(data);
    } catch (err: any) {
      console.error("AI Insights error:", err);
      setError(err?.message || "Failed to generate AI insights. Please try again.");
    } finally {
      clearInterval(stepInterval);
      setLoading(false);
      setLoadingStep(0);
    }
  };

  return (
    <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="px-8 py-7 border-b border-zinc-100">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-6">

          {/* Left: Title */}
          <div className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="flex h-1.5 w-1.5 rounded-full bg-[#3C5A56] animate-ping" />
              <span className="text-xs font-bold uppercase tracking-widest text-[#3C5A56]">
                AI Financial Auditor
              </span>
            </div>
            <h3 className="font-heading text-xl font-bold text-zinc-900">
              Weekly Leadership AI Brief
            </h3>
            <p className="text-sm text-zinc-500 leading-relaxed max-w-xl">
              Real AI analysis of live iWorker contractor logs — scope creep detection, revision
              overages, and margin recommendations generated fresh from Google Sheets data.
            </p>
          </div>

          {/* Right: Generate Button */}
          <div className="shrink-0">
            <button
              onClick={handleGenerate}
              disabled={loading}
              className={`inline-flex items-center gap-2.5 rounded-xl px-6 py-3 text-sm font-semibold text-white shadow-sm transition-all ${
                loading
                  ? "bg-zinc-300 cursor-not-allowed"
                  : "cursor-pointer bg-gradient-to-r from-[#3C5A56] to-[#547d75] hover:from-[#456b64] hover:to-[#3C5A56] hover:shadow-md hover:shadow-[#3C5A56]/20 active:scale-95"
              }`}
            >
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Generating…
                </>
              ) : insights ? (
                <>
                  <RotateCcw className="h-4 w-4" />
                  Re-Run AI Audit
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  Generate AI Financial Insights
                </>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* ── Body ───────────────────────────────────────────────────────────── */}
      <div className="px-8 py-7 space-y-6">

        {/* Loading State */}
        {loading && (
          <div className="rounded-xl border border-[#3C5A56]/20 bg-[#3C5A56]/5 px-6 py-8 text-center space-y-5">
            <div className="flex justify-center">
              <div className="relative">
                <div className="h-14 w-14 rounded-full border-4 border-[#3C5A56]/20 border-t-[#3C5A56] animate-spin" />
                <div className="absolute inset-0 flex items-center justify-center">
                  <Bot className="h-5 w-5 text-[#3C5A56]" />
                </div>
              </div>
            </div>
            <div className="space-y-1.5">
              <h4 className="text-sm font-bold text-zinc-800">AI Auditor is working…</h4>
              <p className="text-xs text-zinc-500 font-mono max-w-sm mx-auto leading-relaxed transition-all duration-500">
                {LOADING_STEPS[loadingStep]}
              </p>
            </div>
            {/* Step dots */}
            <div className="flex items-center justify-center gap-1.5">
              {LOADING_STEPS.map((_, idx) => (
                <div
                  key={idx}
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    idx <= loadingStep ? "w-4 bg-[#3C5A56]" : "w-1.5 bg-zinc-300"
                  }`}
                />
              ))}
            </div>
          </div>
        )}

        {/* Error State */}
        {error && !loading && (
          <div className="rounded-xl border border-red-200 bg-red-50 px-5 py-4 flex items-start gap-3">
            <AlertTriangle className="h-5 w-5 text-red-500 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-red-700">AI generation failed</p>
              <p className="text-xs text-red-500 mt-0.5">{error}</p>
            </div>
          </div>
        )}

        {/* Empty State */}
        {!insights && !loading && !error && (
          <div className="rounded-xl border border-dashed border-zinc-300 bg-zinc-50/60 px-8 py-12 text-center space-y-3">
            <div className="flex justify-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-full bg-zinc-100 text-zinc-400">
                <Sparkles className="h-6 w-6" />
              </div>
            </div>
            <h4 className="text-sm font-semibold text-zinc-700">No AI insights yet</h4>
            <p className="text-xs text-zinc-400 max-w-xs mx-auto leading-relaxed">
              Click <strong className="text-zinc-600">"Generate AI Financial Insights"</strong> to run a
              real AI audit on the latest Sonja Anderson iWorker time log.
            </p>
          </div>
        )}

        {/* Generated Insights */}
        {insights && !loading && (
          <FadeInStagger className="space-y-6">

            {/* Generated At + stats badge row */}
            <FadeInItem className="flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-100 border border-zinc-200 px-3 py-1 text-xs text-zinc-500">
                <Clock className="h-3 w-3" />
                {insights.generated_at}
              </span>
              {insights.stats && (
                <>
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 border border-blue-200 px-3 py-1 text-xs font-semibold text-blue-600">
                    {insights.stats.period_label ? `${insights.stats.period_label} · ` : ""}
                    {insights.stats.active_entries} sessions · {insights.stats.total_hours} hrs · ${insights.stats.total_spend.toFixed(2)}
                  </span>
                  {insights.stats.over_scope_spend > 0 && (
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-50 border border-orange-200 px-3 py-1 text-xs font-semibold text-orange-600">
                      <AlertTriangle className="h-3 w-3" />
                      ${insights.stats.over_scope_spend.toFixed(2)} over-scope risk
                    </span>
                  )}
                </>
              )}
            </FadeInItem>

            {/* Executive Brief */}
            <FadeInItem className="rounded-xl border border-[#3C5A56]/20 bg-[#3C5A56]/[0.06] px-6 py-5 space-y-2">
              <div className="flex items-center gap-2">
                <Bot className="h-4 w-4 text-[#3C5A56]" />
                <span className="text-xs font-bold uppercase tracking-widest text-[#3C5A56]">
                  Executive Brief
                </span>
              </div>
              <p className="text-sm text-zinc-800 leading-relaxed font-medium">
                {insights.summary.leadership_brief_text}
              </p>
            </FadeInItem>

            {/* Risks + Wins Grid */}
            <FadeInItem className="grid grid-cols-1 md:grid-cols-2 gap-5">

              {/* Top Risks */}
              <div className="rounded-xl border border-red-200 bg-red-50/40 p-5 space-y-4">
                <div className="flex items-center gap-2">
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-red-100 text-red-600">
                    <AlertTriangle className="h-4 w-4" />
                  </div>
                  <h4 className="text-xs font-bold uppercase tracking-widest text-red-700">
                    Top Financial Risks
                  </h4>
                </div>
                <ol className="space-y-3">
                  {insights.summary.top_3_risks.map((risk, idx) => (
                    <li
                      key={idx}
                      className="flex items-start gap-3 bg-white rounded-lg px-4 py-3 border border-red-100 shadow-xs"
                    >
                      <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-red-100 text-red-600 text-[11px] font-bold mt-0.5">
                        {idx + 1}
                      </span>
                      <span className="text-xs text-zinc-700 leading-relaxed">{risk}</span>
                    </li>
                  ))}
                </ol>
              </div>

              {/* Top Wins */}
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/40 p-5 space-y-4">
                <div className="flex items-center gap-2">
                  <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-emerald-100 text-emerald-600">
                    <TrendingUp className="h-4 w-4" />
                  </div>
                  <h4 className="text-xs font-bold uppercase tracking-widest text-emerald-700">
                    Operational Wins
                  </h4>
                </div>
                <ol className="space-y-3">
                  {insights.summary.top_3_wins.map((win, idx) => (
                    <li
                      key={idx}
                      className="flex items-start gap-3 bg-white rounded-lg px-4 py-3 border border-emerald-100 shadow-xs"
                    >
                      <span className="shrink-0 flex h-5 w-5 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 text-[11px] font-bold mt-0.5">
                        {idx + 1}
                      </span>
                      <span className="text-xs text-zinc-700 leading-relaxed">{win}</span>
                    </li>
                  ))}
                </ol>
              </div>
            </FadeInItem>

            {/* Margin Recommendations */}
            <FadeInItem className="rounded-xl border border-amber-200 bg-amber-50/40 p-5 space-y-4">
              <div className="flex items-center gap-2">
                <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-amber-100 text-amber-600">
                  <Lightbulb className="h-4 w-4" />
                </div>
                <h4 className="text-xs font-bold uppercase tracking-widest text-amber-700">
                  Actionable Margin Recommendations
                </h4>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {insights.summary.margin_recommendations.map((rec, idx) => (
                  <div
                    key={idx}
                    className="flex items-start gap-3 bg-white rounded-lg px-4 py-3.5 border border-amber-100 shadow-xs"
                  >
                    <span className="shrink-0 text-[#3C5A56] font-bold text-sm mt-0.5">→</span>
                    <span className="text-xs text-zinc-700 leading-relaxed">{rec}</span>
                  </div>
                ))}
              </div>
            </FadeInItem>

          </FadeInStagger>
        )}
      </div>
    </div>
  );
}
