"use client";

import { useState } from "react";
import {
  AlertCircle,
  Info,
  CheckCircle2,
  DollarSign,
  Clock,
  FileText,
  ChevronDown,
  Lightbulb,
  ShieldAlert,
  Check,
  X,
  ReceiptText,
  Tag,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { expoOutEase } from "@/lib/motion";

export interface AuditItem {
  id: string;
  severity: "HIGH" | "MEDIUM" | "LOW" | string;
  type: string;
  source: string;
  age: string;
  client_project: string;
  amount: number;
  hours: number;
  reason: string;
  status: string;
  recommended_action: string;
}

interface AuditQueueTableProps {
  initialAuditItems: AuditItem[];
  onResolveItem?: (id: string, action: string) => Promise<void>;
}

const SEVERITY_CONFIG = {
  HIGH: {
    label: "High",
    icon: ShieldAlert,
    pill: "bg-red-100 text-red-700 border-red-200",
    card: "border-red-200 bg-red-50/30",
    dot: "bg-red-500",
    badge: "text-red-600",
  },
  MEDIUM: {
    label: "Medium",
    icon: AlertCircle,
    pill: "bg-amber-100 text-amber-800 border-amber-200",
    card: "border-amber-200 bg-amber-50/20",
    dot: "bg-amber-500",
    badge: "text-amber-700",
  },
  LOW: {
    label: "Low",
    icon: Info,
    pill: "bg-zinc-100 text-zinc-600 border-zinc-200",
    card: "border-zinc-200 bg-white",
    dot: "bg-zinc-400",
    badge: "text-zinc-500",
  },
} as const;

function getSeverityConfig(severity: string) {
  return SEVERITY_CONFIG[severity as keyof typeof SEVERITY_CONFIG] ?? SEVERITY_CONFIG.LOW;
}

export function AuditQueueTable({
  initialAuditItems,
  onResolveItem,
}: AuditQueueTableProps) {
  const [items, setItems] = useState<AuditItem[]>(initialAuditItems);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const handleAction = async (id: string, action: string) => {
    setResolvingId(id);
    try {
      if (onResolveItem) await onResolveItem(id, action);
      setItems((prev) =>
        prev.map((item) =>
          item.id === id ? { ...item, status: `Resolved (${action})` } : item
        )
      );
    } catch (err) {
      console.error("Audit action error:", err);
    } finally {
      setResolvingId(null);
    }
  };

  const pendingItems = items.filter((i) => !i.status.startsWith("Resolved"));
  const resolvedItems = items.filter((i) => i.status.startsWith("Resolved"));
  // HIGH = actual unbilled scope creep (matches iWorker tab "AI Flagged Scope Risk")
  const highItems = pendingItems.filter((i) => i.severity === "HIGH");
  const medLowItems = pendingItems.filter((i) => i.severity !== "HIGH");
  const scopeRiskAmount = highItems.reduce((acc, i) => acc + i.amount, 0);
  const reviewAmount = medLowItems.reduce((acc, i) => acc + i.amount, 0);
  const highCount = highItems.length;

  return (
    <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden">

      {/* ── Header ────────────────────────────────────────────────────────── */}
      <div className="px-8 py-7 border-b border-zinc-100">
        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
          <div className="space-y-1.5">
            <div className="flex items-center gap-3 flex-wrap">
              <h3 className="font-heading text-xl font-bold text-zinc-900">Audit Queue</h3>
              {pendingItems.length > 0 && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 border border-amber-200 px-3 py-1 text-xs font-bold text-amber-800">
                  <span className="h-1.5 w-1.5 rounded-full bg-amber-500 animate-pulse" />
                  {pendingItems.length} Pending
                </span>
              )}
              {highCount > 0 && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-red-100 border border-red-200 px-3 py-1 text-xs font-bold text-red-700">
                  <ShieldAlert className="h-3 w-3" />
                  {highCount} Critical
                </span>
              )}
            </div>
            <p className="text-sm text-zinc-500 leading-relaxed">
              Human-reviewed resolution for every flagged contractor and timesheet anomaly.
              Derived live from iWorker Google Sheets data.
            </p>
          </div>
        </div>
      </div>

      {/* ── Items ─────────────────────────────────────────────────────────── */}
      <div className="px-8 py-7 space-y-4">

        {items.length === 0 && (
          <div className="rounded-xl border border-dashed border-zinc-300 bg-zinc-50 px-8 py-12 text-center space-y-2">
            <div className="flex justify-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-emerald-600">
                <CheckCircle2 className="h-6 w-6" />
              </div>
            </div>
            <p className="text-sm font-semibold text-zinc-700">No audit flags detected</p>
            <p className="text-xs text-zinc-400">
              All timesheet entries are within scope. No anomalies found.
            </p>
          </div>
        )}

        {items.map((item) => {
          const isResolved = item.status.startsWith("Resolved");
          const isExpanded = expandedId === item.id;
          const cfg = getSeverityConfig(item.severity);
          const SeverityIcon = cfg.icon;

          return (
            <div
              key={item.id}
              className={`rounded-xl border transition-all duration-200 overflow-hidden ${
                isResolved
                  ? "border-zinc-200 bg-zinc-50 opacity-60"
                  : cfg.card
              }`}
            >
              {/* Card Top Row */}
              <div
                className="px-6 py-5 cursor-pointer"
                onClick={() => setExpandedId(isExpanded ? null : item.id)}
              >
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">

                  {/* Left: Severity + Type + Project */}
                  <div className="flex items-start gap-4 flex-1 min-w-0">
                    {/* Severity Badge */}
                    <div className={`shrink-0 flex h-10 w-10 items-center justify-center rounded-xl border ${cfg.pill}`}>
                      <SeverityIcon className="h-5 w-5" />
                    </div>

                    <div className="min-w-0 space-y-1">
                      <div className="flex items-center gap-2.5 flex-wrap">
                        <span className={`text-xs font-bold uppercase tracking-wider ${cfg.badge}`}>
                          {item.severity}
                        </span>
                        <span className="text-zinc-300 text-xs">·</span>
                        <span className="text-xs font-semibold text-zinc-500">{item.type}</span>
                        {isResolved && (
                          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 border border-emerald-200 px-2.5 py-0.5 text-[11px] font-bold text-emerald-700">
                            <Check className="h-3 w-3" />
                            {item.status}
                          </span>
                        )}
                      </div>
                      <p className="font-semibold text-sm text-zinc-900 leading-snug">
                        {item.client_project}
                      </p>
                      <div className="flex items-center gap-3 text-xs text-zinc-400">
                        <span className="flex items-center gap-1">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                          {item.source}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Right: Financials + expand toggle */}
                  <div className="flex items-center gap-6 shrink-0">
                    <div className="text-right space-y-0.5">
                      <div className="flex items-center gap-1.5 justify-end text-emerald-600 font-mono font-bold text-base">
                        <DollarSign className="h-3.5 w-3.5" />
                        {item.amount.toFixed(2)}
                      </div>
                      <div className="flex items-center gap-1 justify-end text-xs text-zinc-400">
                        <Clock className="h-3 w-3" />
                        {item.hours} hrs
                      </div>
                    </div>
                    <div className={`flex h-7 w-7 items-center justify-center rounded-lg border transition-colors ${
                      isExpanded ? "bg-zinc-900 border-zinc-900 text-white" : "bg-zinc-100 border-zinc-200 text-zinc-500"
                    }`}>
                      <ChevronDown
                        className="h-4 w-4 transition-transform duration-300"
                        style={{ transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)" }}
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* Expanded Detail + Actions */}
              <AnimatePresence initial={false}>
                {isExpanded && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.3, ease: expoOutEase }}
                    className="overflow-hidden"
                  >
                <div className="border-t border-zinc-100 bg-white px-6 py-5 space-y-5">

                  {/* Flag Reason */}
                  <div className="flex gap-3">
                    <div className="shrink-0 mt-0.5">
                      <FileText className="h-4 w-4 text-zinc-400" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-[11px] font-bold uppercase tracking-widest text-zinc-400">
                        Flag Reason
                      </p>
                      <p className="text-sm text-zinc-700 leading-relaxed">{item.reason}</p>
                    </div>
                  </div>

                  {/* AI Recommendation */}
                  <div className="flex gap-3">
                    <div className="shrink-0 mt-0.5">
                      <Lightbulb className="h-4 w-4 text-[#3C5A56]" />
                    </div>
                    <div className="space-y-1">
                      <p className="text-[11px] font-bold uppercase tracking-widest text-zinc-400">
                        AI Recommendation
                      </p>
                      <p className="text-sm font-semibold text-[#3C5A56] leading-relaxed">
                        {item.recommended_action}
                      </p>
                    </div>
                  </div>

                  {/* Action Buttons */}
                  {!isResolved && (
                    <div className="pt-2 border-t border-zinc-100 flex flex-wrap items-center gap-2.5">
                      <p className="text-[11px] font-bold uppercase tracking-widest text-zinc-400 w-full mb-1">
                        Resolve Flag
                      </p>
                      <button
                        disabled={resolvingId === item.id}
                        onClick={() => handleAction(item.id, "ACCEPT")}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-zinc-900 text-white px-4 py-2 text-xs font-bold hover:bg-zinc-700 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Check className="h-3.5 w-3.5" />
                        Accept
                      </button>
                      <button
                        disabled={resolvingId === item.id}
                        onClick={() => handleAction(item.id, "RECLASSIFY")}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-zinc-100 border border-zinc-200 text-zinc-700 px-4 py-2 text-xs font-bold hover:bg-zinc-200 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Tag className="h-3.5 w-3.5" />
                        Reclassify
                      </button>
                      <button
                        disabled={resolvingId === item.id}
                        onClick={() => handleAction(item.id, "BILL")}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-[#3C5A56] text-white px-4 py-2 text-xs font-bold hover:bg-[#2e4744] transition-colors disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <ReceiptText className="h-3.5 w-3.5" />
                        Bill Client
                      </button>
                      <button
                        disabled={resolvingId === item.id}
                        onClick={() => handleAction(item.id, "DISMISS")}
                        className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg text-zinc-400 px-4 py-2 text-xs font-semibold hover:text-zinc-600 transition-colors disabled:cursor-not-allowed disabled:opacity-50 ml-auto"
                      >
                        <X className="h-3.5 w-3.5" />
                        Dismiss
                      </button>
                    </div>
                  )}
                </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}

        {/* Resolved count summary */}
        {resolvedItems.length > 0 && (
          <div className="pt-2 flex items-center gap-2 text-xs text-zinc-400">
            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />
            {resolvedItems.length} flag{resolvedItems.length > 1 ? "s" : ""} resolved this session
          </div>
        )}
      </div>
    </div>
  );
}
