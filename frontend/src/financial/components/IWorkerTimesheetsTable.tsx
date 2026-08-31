"use client";

import { useState, useMemo, useEffect } from "react";
import {
  Clock,
  DollarSign,
  AlertTriangle,
  SlidersHorizontal,
  Search,
  ExternalLink,
  Grid3X3,
  Package,
  List,
  ChevronDown,
  RotateCcw,
  Info,
  Wifi,
  WifiOff,
  ArrowUpDown,
  CalendarDays,
  X,
  CheckCircle2,
  AlertCircle,
  Bot,
  Minus,
  FileSpreadsheet,
  Loader2,
  ChevronLeft,
  ChevronRight,
  Users,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { expoOutEase } from "@/lib/motion";
import { AnimatedNumber } from "./AnimatedNumber";
import type { PeriodInsights, PeriodHistoryPoint, PeriodGranularity } from "../types/iworker";
import { entryDateInPeriod } from "../lib/iworker-period";

export interface TimesheetEntry {
  id: string;
  contractor?: string;
  day: string;
  date: string;
  start_time: string;
  end_time: string;
  duration: string;
  hours: number;
  rate?: number;
  amount: number;
  task: string;
  week_ending: string;
  ai_classification?: {
    raw_task: string;
    topic: string;
    detected_round: number | null;
    is_edit_task: boolean;
    is_over_scope: boolean;
    work_category: string;
    status_tag: string;
    ai_reasoning: string;
  };
}

export interface ContractorTabInfo {
  name: string;
  rate: number;
  total_hours: number;
  total_spend: number;
  active_entries: number;
}

interface IWorkerTimesheetsTableProps {
  contractor: string;
  source: string;
  tabs?: ContractorTabInfo[];
  selectedContractor?: string;
  onSelectContractor?: (contractorName: string) => void;
  isLoadingContractor?: boolean;
  summary: {
    total_logged_hours: number;
    total_spend_usd: number;
    active_tasks_count: number;
    hourly_rate_usd: number;
  };
  periodInsights: PeriodInsights;
  periodHistory: PeriodHistoryPoint[];
  granularity: PeriodGranularity;
  onGranularityChange: (g: PeriodGranularity) => void;
  onPeriodStartChange: (iso: string | null) => void;
  periodFilterEnabled: boolean;
  onTogglePeriodFilter: () => void;
  timesheets: TimesheetEntry[];
}

function formatDeltaPct(pct: number | null): string {
  if (pct === null) return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${Math.round(pct)}%`;
}

function deltaColorClass(pct: number | null): string {
  if (pct === null) return "text-zinc-400";
  if (pct > 0) return "text-emerald-600";
  if (pct < 0) return "text-red-500";
  return "text-zinc-400";
}

function signalSeverityStyles(severity: string): { border: string; bg: string; icon: string } {
  switch (severity) {
    case "scope":
      return { border: "border-orange-200", bg: "bg-orange-50/60", icon: "text-orange-600" };
    case "capacity":
      return { border: "border-blue-200", bg: "bg-blue-50/60", icon: "text-blue-600" };
    default:
      return { border: "border-red-200", bg: "bg-red-50/60", icon: "text-red-600" };
  }
}

function getMonthYearKey(dateStr: string): string {
  if (!dateStr) return "Other Logs";
  const parts = dateStr.trim().split(",");
  if (parts.length >= 2) {
    const monthPart = parts[0].replace(/[0-9]/g, "").trim();
    const yearPart = parts[1].trim();
    return `${monthPart} ${yearPart}`;
  }
  return dateStr;
}

function extractGenericTopic(taskDescription: string): string {
  if (!taskDescription) return "General Work";
  const cleaned = taskDescription
    .replace(/^(working on|finishing|editing|making|drafting|getting|edits on|edit for|revisions for)\s+/i, "")
    .replace(/\s+(round|r|edit|edits)\s*\d+$/i, "")
    .trim();
  return cleaned || taskDescription;
}

function detectRevisionRound(taskDescription: string): number | null {
  const match = taskDescription.match(/(?:round|r|edit|edits|revision)\s*(\d+)/i);
  if (match && match[1]) return parseInt(match[1], 10);
  return null;
}

function HighlightText({ text, query }: { text: string; query: string }) {
  if (!query || !query.trim() || !text) return <>{text}</>;
  const keywords = query
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (keywords.length === 0) return <>{text}</>;

  try {
    const regex = new RegExp(`(${keywords.join("|")})`, "gi");
    const parts = text.split(regex);

    return (
      <>
        {parts.map((part, i) =>
          keywords.some((kw) => kw.toLowerCase() === part.toLowerCase()) ? (
            <mark key={i} className="bg-amber-200 text-amber-950 font-bold rounded px-0.5 py-0.2 select-none">
              {part}
            </mark>
          ) : (
            part
          )
        )}
      </>
    );
  } catch (e) {
    return <>{text}</>;
  }
}

export function IWorkerTimesheetsTable({
  contractor,
  source,
  tabs,
  selectedContractor,
  onSelectContractor,
  isLoadingContractor = false,
  summary,
  periodInsights,
  periodHistory,
  granularity,
  onGranularityChange,
  onPeriodStartChange,
  periodFilterEnabled,
  onTogglePeriodFilter,
  timesheets,
}: IWorkerTimesheetsTableProps) {
  const [sheetStatus, setSheetStatus] = useState<"connected" | "disconnected">("connected");
  const [viewMode, setViewMode] = useState<"GROUPED" | "DAILY">("GROUPED");
  const [selectedYear, setSelectedYear] = useState<string>("ALL");
  const [selectedMonth, setSelectedMonth] = useState<string>("ALL");
  const [selectedStatusFilter, setSelectedStatusFilter] = useState<"ALL" | "BILLABLE" | "OVER_SCOPE">("ALL");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [hideOffDays, setHideOffDays] = useState<boolean>(true);
  const [showPreview, setShowPreview] = useState<boolean>(false);
  const [showLegend, setShowLegend] = useState<boolean>(false);
  const [showFilters, setShowFilters] = useState<boolean>(false);
  const [sortOrder, setSortOrder] = useState<"DESC" | "ASC">("DESC");
  const [expandedDeliverables, setExpandedDeliverables] = useState<{ [key: string]: boolean }>({});

  useEffect(() => {
    const saved = localStorage.getItem("zo_iworker_sheet_status");
    if (saved === "disconnected") setSheetStatus("disconnected");
  }, []);

  const handleStatusChange = (status: "connected" | "disconnected") => {
    setSheetStatus(status);
    localStorage.setItem("zo_iworker_sheet_status", status);
  };

  const isConnected = sheetStatus === "connected";
  const googleSheetUrl =
    "https://docs.google.com/spreadsheets/d/1KXV3SxEinnxJU6wLMb-QHlQXHhcMQwpJmD5cRFyG-74/edit#gid=2127853076";

  const availableYears = useMemo(() => {
    const years = new Set<string>();
    timesheets.forEach((t) => {
      if (t.date.includes("2026")) years.add("2026");
      if (t.date.includes("2025")) years.add("2025");
    });
    return Array.from(years).sort().reverse();
  }, [timesheets]);

  const activeFiltersCount = useMemo(() => {
    let count = 0;
    if (selectedYear !== "ALL") count++;
    if (selectedMonth !== "ALL") count++;
    if (selectedStatusFilter !== "ALL") count++;
    return count;
  }, [selectedYear, selectedMonth, selectedStatusFilter]);

  const resetFilters = () => {
    setSelectedYear("ALL");
    setSelectedMonth("ALL");
    setSelectedStatusFilter("ALL");
    setSearchQuery("");
  };

  const enrichedEntries = useMemo(() => {
    const chrono = [...timesheets].sort(
      (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
    );
    const topicEditCounters: { [topicKey: string]: number } = {};

    return chrono.map((item) => {
      const ai = item.ai_classification;
      const taskLower = item.task.toLowerCase();
      const topicKey = (ai?.topic || extractGenericTopic(item.task)).toLowerCase();
      const isEditTask = ai
        ? ai.is_edit_task
        : taskLower.includes("edit") ||
          taskLower.includes("edits") ||
          taskLower.includes("round") ||
          taskLower.includes("revision");
      const explicitRound = ai ? ai.detected_round : detectRevisionRound(item.task);

      let editNumber = explicitRound || 0;
      if (isEditTask && !explicitRound && item.hours > 0) {
        topicEditCounters[topicKey] = (topicEditCounters[topicKey] || 0) + 1;
        editNumber = topicEditCounters[topicKey];
      }

      let isOverScope = ai ? ai.is_over_scope : false;
      if (!ai && isEditTask && item.hours > 0) {
        if (editNumber >= 3 || taskLower.includes("round 3") || taskLower.includes("r3") || taskLower.includes("round 4") || taskLower.includes("r4")) {
          isOverScope = true;
        }
      }

      let flagReason = ai?.work_category || "In-Scope Baseline Production";
      let flagSubtext = ai?.ai_reasoning || "Approved Retainer Deliverable";

      if (!ai) {
        if (isOverScope) {
          flagReason = `Unbilled Revision Overage (Edit #${editNumber || 3})`;
          flagSubtext = "Exceeds 2-round retainer cap";
        } else if (isEditTask && item.hours > 0) {
          flagReason = `In-Scope Revision (Edit #${editNumber} of 2)`;
          flagSubtext = "Covered under retainer edit cap";
        }
      }

      return {
        ...item,
        topic: ai?.topic || extractGenericTopic(item.task),
        editNumber,
        isOverScope,
        flagReason,
        flagSubtext,
        ai_reasoning: ai?.ai_reasoning || flagSubtext,
      };
    });
  }, [timesheets]);

  const selectedPeriodIndex = useMemo(() => {
    const periods = periodInsights.available_periods;
    const idx = periods.findIndex((p) => p.start === periodInsights.selected.start);
    return idx >= 0 ? idx : periods.length - 1;
  }, [periodInsights.available_periods, periodInsights.selected.start]);

  const trendHistory = useMemo(
    () => periodHistory.filter((p) => p.granularity === granularity),
    [periodHistory, granularity],
  );

  const maxTrendHours = useMemo(
    () => Math.max(...trendHistory.map((p) => p.hours), 1),
    [trendHistory],
  );

  const periodEmptyCopy =
    granularity === "month" ? "No hours logged this month" : "No hours logged this week";

  const filteredAndSorted = useMemo(() => {
    if (!isConnected) return [];
    let result = [...enrichedEntries];
    if (periodFilterEnabled) {
      result = result.filter((item) =>
        entryDateInPeriod(item.date, periodInsights.selected.start, periodInsights.selected.end),
      );
    }
    if (hideOffDays) result = result.filter((item) => item.hours > 0);
    if (selectedYear !== "ALL") result = result.filter((item) => item.date.includes(selectedYear));
    if (selectedMonth !== "ALL")
      result = result.filter((item) =>
        item.date.toLowerCase().includes(selectedMonth.toLowerCase())
      );
    if (selectedStatusFilter === "BILLABLE")
      result = result.filter((item) => item.hours > 0 && !item.isOverScope);
    else if (selectedStatusFilter === "OVER_SCOPE")
      result = result.filter((item) => item.isOverScope);
    if (searchQuery && searchQuery.trim()) {
      const keywords = searchQuery.toLowerCase().trim().split(/\s+/).filter(Boolean);
      result = result.filter((item) => {
        const statusTerm = item.isOverScope
          ? "over scope overage r3+ unbilled risk flag"
          : "billable in-scope baseline retainer approved";

        const searchableFields = [
          item.task,
          item.topic,
          item.contractor || "",
          item.date,
          item.day,
          item.week_ending ? `week ending ${item.week_ending}` : "",
          item.duration,
          item.hours ? `${item.hours} hrs ${item.hours}h` : "",
          item.amount ? `$${item.amount.toFixed(2)} ${item.amount}` : "",
          item.flagReason,
          item.flagSubtext,
          item.ai_reasoning,
          statusTerm,
        ]
          .join(" ")
          .toLowerCase();

        return keywords.every((kw) => searchableFields.includes(kw));
      });
    }
    result.sort((a, b) => {
      const dateA = new Date(a.date).getTime();
      const dateB = new Date(b.date).getTime();
      return sortOrder === "DESC" ? dateB - dateA : dateA - dateB;
    });
    return result;
  }, [
    enrichedEntries,
    periodFilterEnabled,
    periodInsights.selected.start,
    periodInsights.selected.end,
    hideOffDays,
    selectedYear,
    selectedMonth,
    selectedStatusFilter,
    searchQuery,
    sortOrder,
    isConnected,
  ]);

  const deliverableGroups = useMemo(() => {
    const map: {
      [taskName: string]: {
        taskName: string;
        totalHours: number;
        totalSpend: number;
        isOverScope: boolean;
        entries: typeof enrichedEntries;
        firstDate: string;
        lastDate: string;
      };
    } = {};
    filteredAndSorted.forEach((item) => {
      const key = item.task.trim();
      if (!map[key]) {
        map[key] = {
          taskName: key,
          totalHours: 0,
          totalSpend: 0,
          isOverScope: false,
          entries: [],
          firstDate: item.date,
          lastDate: item.date,
        };
      }
      map[key].totalHours += item.hours;
      map[key].totalSpend += item.amount;
      if (item.isOverScope) map[key].isOverScope = true;
      map[key].entries.push(item);
    });
    return Object.values(map);
  }, [filteredAndSorted]);

  const monthGroups = useMemo(() => {
    const groups: { [key: string]: typeof enrichedEntries } = {};
    filteredAndSorted.forEach((item) => {
      const key = getMonthYearKey(item.date);
      if (!groups[key]) groups[key] = [];
      groups[key].push(item);
    });
    return groups;
  }, [filteredAndSorted]);

  const toggleExpand = (taskName: string) => {
    setExpandedDeliverables((prev) => ({ ...prev, [taskName]: !prev[taskName] }));
  };

  return (
    <div className="space-y-8">

      {/* ─── IDENTITY CARD ───────────────────────────────────────────────────── */}
      <div className="rounded-2xl border border-zo-border bg-white shadow-sm overflow-hidden">
        {/* Teal accent stripe */}
        <div className="h-1 w-full bg-gradient-to-r from-[#3C5A56] via-[#598079] to-[#8fb0a9]" />

        <div className="p-6 sm:p-8 space-y-6">
          {/* Identity */}
          <div className="flex items-center gap-5">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-[#3C5A56]/10 border border-[#3C5A56]/20 font-black text-lg text-[#3C5A56] tracking-tight select-none">
              iW
            </div>
            <div className="space-y-1.5 min-w-0">
              <div className="flex items-center gap-3 flex-wrap">
                <h3 className="font-heading text-xl font-bold text-foreground leading-tight">
                  iWorker Contractor Timesheets
                </h3>
                {isConnected ? (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700 border border-emerald-200">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                    Live & AI Active
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-zinc-100 px-3 py-1 text-xs font-semibold text-zinc-500 border border-zinc-200">
                    <WifiOff className="h-3 w-3" />
                    Disconnected
                  </span>
                )}
              </div>
              <p className="text-xs text-zo-text-muted leading-relaxed">
                Source: <span className="font-semibold text-foreground">iWorker Time Tracker Sheet</span>
                <span className="mx-2 text-zinc-300">·</span>
                Rate: <span className="font-semibold text-foreground">${summary.hourly_rate_usd.toFixed(2)} / hr</span>
                <span className="mx-2 text-zinc-300">·</span>
                <span className="inline-flex items-center gap-1 text-emerald-700 font-semibold">
                  <Bot className="h-3 w-3" />
                  FastAPI AI Classifier Active
                </span>
              </p>
            </div>
          </div>

          {/* Contractor Tabs — shown only when connected and tabs available */}
          {isConnected && tabs && tabs.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted flex items-center gap-2">
                  Contractor Sheets ({tabs.length})
                </p>
                {isLoadingContractor && (
                  <span className="inline-flex items-center gap-1.5 text-xs text-[#3C5A56] font-semibold animate-pulse">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Loading contractor sheet data...
                  </span>
                )}
              </div>
              <div className="flex flex-wrap gap-2">
                {/* All contractors pill */}
                <button
                  onClick={() => onSelectContractor?.("all")}
                  disabled={isLoadingContractor}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold border transition-all ${
                    (!selectedContractor || selectedContractor === "all")
                      ? "bg-[#3C5A56] text-white border-[#3C5A56] shadow-sm"
                      : "bg-zinc-50 text-zinc-600 border-zinc-200 hover:border-[#3C5A56]/40 hover:text-[#3C5A56]"
                  } ${isLoadingContractor ? "cursor-wait opacity-90" : "cursor-pointer"}`}
                >
                  {isLoadingContractor && (!selectedContractor || selectedContractor === "all") ? (
                    <Loader2 className="h-3 w-3 animate-spin text-white" />
                  ) : (
                    <FileSpreadsheet className="h-3 w-3" />
                  )}
                  All
                </button>
                {tabs.map((tab) => {
                  const isActive = selectedContractor === tab.name;
                  const isThisLoading = isLoadingContractor && isActive;
                  return (
                    <button
                      key={tab.name}
                      onClick={() => onSelectContractor?.(tab.name)}
                      disabled={isLoadingContractor}
                      title={`${tab.total_hours} hrs · $${tab.rate}/hr · ${tab.active_entries} entries`}
                      className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-semibold border transition-all ${
                        isActive
                          ? "bg-[#3C5A56] text-white border-[#3C5A56] shadow-sm"
                          : "bg-zinc-50 text-zinc-600 border-zinc-200 hover:border-[#3C5A56]/40 hover:text-[#3C5A56]"
                      } ${isLoadingContractor ? "cursor-wait opacity-90" : "cursor-pointer"}`}
                    >
                      {isThisLoading ? (
                        <Loader2 className="h-3 w-3 animate-spin text-white" />
                      ) : (
                        <FileSpreadsheet className="h-3 w-3" />
                      )}
                      {tab.name}
                      <span className={`ml-0.5 rounded px-1 py-0.5 text-[10px] font-bold ${
                        isActive ? "bg-white/20 text-white" : "bg-zinc-200 text-zinc-500"
                      }`}>
                        ${tab.rate}/hr
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Toolbar — visually separated from identity, grouped by purpose */}
          <div className="flex flex-col gap-4 border-t border-zinc-100 pt-6 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2.5">
              {/* View Mode Toggle */}
              <div className="inline-flex items-center rounded-xl bg-zinc-100 p-1 border border-zinc-200 gap-0.5">
                <button
                  onClick={() => setViewMode("GROUPED")}
                  className={`inline-flex cursor-pointer items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg transition-all ${
                    viewMode === "GROUPED"
                      ? "bg-[#3C5A56] text-white shadow-sm"
                      : "text-zinc-500 hover:text-zinc-800"
                  }`}
                >
                  <Package className="h-3.5 w-3.5" />
                  Grouped
                </button>
                <button
                  onClick={() => setViewMode("DAILY")}
                  className={`inline-flex cursor-pointer items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg transition-all ${
                    viewMode === "DAILY"
                      ? "bg-[#3C5A56] text-white shadow-sm"
                      : "text-zinc-500 hover:text-zinc-800"
                  }`}
                >
                  <List className="h-3.5 w-3.5" />
                  Daily Log
                </button>
              </div>

              {/* Sheet Status */}
              <div className="flex items-center gap-2 rounded-xl border border-zo-border bg-zinc-50 px-3.5 py-2">
                {isConnected ? (
                  <Wifi className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                ) : (
                  <WifiOff className="h-3.5 w-3.5 text-zinc-400 shrink-0" />
                )}
                <select
                  value={sheetStatus}
                  onChange={(e) => handleStatusChange(e.target.value as "connected" | "disconnected")}
                  className="bg-transparent font-cabin text-xs font-semibold text-foreground focus:outline-none cursor-pointer"
                >
                  <option value="connected">iWorker Sheet Connected</option>
                  <option value="disconnected">Disconnected</option>
                </select>
              </div>
            </div>

            {isConnected && (
              <div className="grid grid-cols-2 gap-2.5 sm:flex sm:shrink-0">
                <a
                  href={googleSheetUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-700 transition-colors"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Open Sheet
                </a>
                <button
                  onClick={() => setShowPreview(!showPreview)}
                  className={`inline-flex cursor-pointer items-center justify-center gap-2 rounded-xl px-4 py-2 text-xs font-semibold transition-colors ${
                    showPreview
                      ? "bg-zinc-800 text-white"
                      : "bg-zinc-900 text-white hover:bg-zinc-800"
                  }`}
                >
                  <Grid3X3 className="h-3.5 w-3.5" />
                  {showPreview ? "Hide Grid" : "Sheet Grid"}
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ─── PERIOD OPS KPIs ─────────────────────────────────────────────────── */}
      {isConnected ? (
        <div className="relative space-y-5">
          {isLoadingContractor && (
            <div className="absolute inset-0 bg-white/70 backdrop-blur-[1px] z-20 rounded-2xl flex items-center justify-center transition-all duration-300">
              <div className="inline-flex items-center gap-2.5 rounded-xl bg-white border border-[#3C5A56]/20 px-4 py-2.5 shadow-lg text-xs font-semibold text-[#3C5A56] animate-pulse">
                <Loader2 className="h-4 w-4 animate-spin text-[#3C5A56]" />
                <span>Recalculating contractor hours & spend...</span>
              </div>
            </div>
          )}

          {/* Period controls */}
          <div className={`rounded-2xl border border-zinc-200 bg-white shadow-sm px-5 py-4 transition-all duration-200 ${isLoadingContractor ? "opacity-40" : ""}`}>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="inline-flex items-center rounded-xl bg-zinc-100 p-1 border border-zinc-200 gap-0.5">
                <button
                  onClick={() => onGranularityChange("week")}
                  className={`inline-flex cursor-pointer items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg transition-all ${
                    granularity === "week"
                      ? "bg-[#3C5A56] text-white shadow-sm"
                      : "text-zinc-500 hover:text-zinc-800"
                  }`}
                >
                  Week
                </button>
                <button
                  onClick={() => onGranularityChange("month")}
                  className={`inline-flex cursor-pointer items-center gap-1.5 px-3.5 py-2 text-xs font-semibold rounded-lg transition-all ${
                    granularity === "month"
                      ? "bg-[#3C5A56] text-white shadow-sm"
                      : "text-zinc-500 hover:text-zinc-800"
                  }`}
                >
                  Month
                </button>
              </div>

              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={() => {
                    const prev = periodInsights.available_periods[selectedPeriodIndex - 1];
                    if (prev) onPeriodStartChange(prev.start);
                  }}
                  disabled={selectedPeriodIndex <= 0}
                  className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl border border-zinc-200 bg-zinc-50 text-zinc-600 hover:border-[#3C5A56]/40 hover:text-[#3C5A56] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                  aria-label="Previous period"
                >
                  <ChevronLeft className="h-4 w-4" />
                </button>
                <span className="text-sm font-semibold text-foreground min-w-[10rem] text-center">
                  {periodInsights.selected.label}
                </span>
                <button
                  onClick={() => {
                    const next = periodInsights.available_periods[selectedPeriodIndex + 1];
                    if (next) onPeriodStartChange(next.start);
                  }}
                  disabled={selectedPeriodIndex >= periodInsights.available_periods.length - 1}
                  className="inline-flex h-9 w-9 cursor-pointer items-center justify-center rounded-xl border border-zinc-200 bg-zinc-50 text-zinc-600 hover:border-[#3C5A56]/40 hover:text-[#3C5A56] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                  aria-label="Next period"
                >
                  <ChevronRight className="h-4 w-4" />
                </button>
                {!periodInsights.selected.is_current && (
                  <button
                    onClick={() => onPeriodStartChange(null)}
                    className="inline-flex cursor-pointer items-center gap-1.5 rounded-xl border border-[#3C5A56]/30 bg-[#3C5A56]/5 px-3 py-2 text-xs font-semibold text-[#3C5A56] hover:bg-[#3C5A56]/10 transition-all"
                  >
                    {granularity === "month" ? "This month" : "This week"}
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Hero KPI cards */}
          <div className={`grid grid-cols-1 sm:grid-cols-3 gap-5 transition-all duration-200 ${isLoadingContractor ? "opacity-40" : ""}`}>
            <div className="flex items-center gap-4 rounded-2xl bg-white border border-zinc-200 shadow-sm px-6 py-6">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-blue-100 text-blue-600">
                <Clock className="h-5 w-5" />
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted mb-1">
                  Period Hours
                </p>
                <div className="flex items-baseline gap-2">
                  <p className="font-heading text-3xl font-bold text-foreground leading-none">
                    <AnimatedNumber value={periodInsights.current.hours} decimals={2} />
                    <span className="text-sm font-semibold text-zo-text-muted ml-1.5">hrs</span>
                  </p>
                  <span className={`text-xs font-bold ${deltaColorClass(periodInsights.delta.hours_pct)}`}>
                    {formatDeltaPct(periodInsights.delta.hours_pct)}
                  </span>
                </div>
                <p className="text-[11px] text-zo-text-muted mt-1.5">
                  {periodInsights.current.hours > 0
                    ? periodInsights.selected.label
                    : periodEmptyCopy}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-4 rounded-2xl bg-white border border-zinc-200 shadow-sm px-6 py-6">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-emerald-100 text-emerald-600">
                <DollarSign className="h-5 w-5" />
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted mb-1">
                  Period Spend
                </p>
                <div className="flex items-baseline gap-2">
                  <p className="font-heading text-3xl font-bold text-emerald-600 leading-none">
                    <AnimatedNumber value={periodInsights.current.spend_usd} decimals={2} prefix="$" />
                  </p>
                  <span className={`text-xs font-bold ${deltaColorClass(periodInsights.delta.spend_pct)}`}>
                    {formatDeltaPct(periodInsights.delta.spend_pct)}
                  </span>
                </div>
                <p className="text-[11px] text-zo-text-muted mt-1.5">
                  {periodInsights.current.hours > 0
                    ? periodInsights.selected.label
                    : periodEmptyCopy}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-4 rounded-2xl bg-orange-50 border border-orange-200 shadow-sm px-6 py-6">
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-orange-100 text-orange-600">
                <AlertTriangle className="h-5 w-5" />
              </div>
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted mb-1">
                  Scope Risk
                </p>
                <div className="flex items-baseline gap-2">
                  <p className="font-heading text-3xl font-bold text-orange-600 leading-none">
                    <AnimatedNumber value={periodInsights.current.scope_risk_usd} decimals={2} prefix="$" />
                  </p>
                  <span className={`text-xs font-bold ${deltaColorClass(periodInsights.delta.scope_risk_pct)}`}>
                    {formatDeltaPct(periodInsights.delta.scope_risk_pct)}
                  </span>
                </div>
                <p className="text-[11px] text-zo-text-muted mt-1.5">
                  {periodInsights.current.hours > 0
                    ? periodInsights.selected.label
                    : periodEmptyCopy}
                </p>
              </div>
            </div>
          </div>

          {/* Signals */}
          {periodInsights.signals.length > 0 && (
            <div className={`rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden transition-all duration-200 ${isLoadingContractor ? "opacity-40" : ""}`}>
              <div className="px-6 py-4 border-b border-zinc-100 bg-zinc-50/60">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted">
                  Period Signals
                </p>
              </div>
              <div className="divide-y divide-zinc-100">
                {periodInsights.signals.map((signal) => {
                  const styles = signalSeverityStyles(signal.severity);
                  return (
                    <div key={signal.id} className={`px-6 py-4 ${styles.bg}`}>
                      <div className="flex items-start gap-3">
                        <AlertTriangle className={`h-4 w-4 shrink-0 mt-0.5 ${styles.icon}`} />
                        <div>
                          <p className="text-sm font-semibold text-foreground">{signal.headline}</p>
                          <p className="text-xs text-zo-text-muted mt-1 leading-relaxed">{signal.detail}</p>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Contractor strip */}
          {periodInsights.contractors.length > 0 && (
            <div className={`rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden transition-all duration-200 ${isLoadingContractor ? "opacity-40" : ""}`}>
              <div className="px-6 py-4 border-b border-zinc-100 bg-zinc-50/60 flex items-center gap-2">
                <Users className="h-4 w-4 text-[#3C5A56]" />
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted">
                  Contractor Breakdown
                </p>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="bg-zinc-50 border-b border-zinc-100">
                    <tr className="text-zinc-400 font-semibold uppercase tracking-wider">
                      <th className="px-6 py-3">Contractor</th>
                      <th className="px-4 py-3 text-right">Hours</th>
                      <th className="px-4 py-3 text-right">Spend</th>
                      <th className="px-4 py-3 text-right">Scope</th>
                      <th className="px-4 py-3 text-right">Utilization</th>
                      <th className="px-4 py-3 text-right">Δ Hours</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-50">
                    {periodInsights.contractors.map((c) => (
                      <tr
                        key={c.name}
                        onClick={() => onSelectContractor?.(c.name)}
                        className="hover:bg-zinc-50/80 cursor-pointer transition-colors"
                      >
                        <td className="px-6 py-3.5 font-semibold text-[#3C5A56]">{c.name}</td>
                        <td className="px-4 py-3.5 text-right font-mono font-semibold text-foreground">
                          {c.hours.toFixed(1)}
                        </td>
                        <td className="px-4 py-3.5 text-right font-mono font-semibold text-emerald-600">
                          ${c.spend_usd.toFixed(2)}
                        </td>
                        <td className="px-4 py-3.5 text-right font-mono font-semibold text-orange-600">
                          ${c.scope_risk_usd.toFixed(2)}
                        </td>
                        <td className="px-4 py-3.5 text-right font-semibold text-zinc-600">
                          {c.utilization_pct !== null ? `${Math.round(c.utilization_pct)}%` : "—"}
                        </td>
                        <td className={`px-4 py-3.5 text-right font-semibold ${deltaColorClass(c.hours_delta_pct)}`}>
                          {formatDeltaPct(c.hours_delta_pct)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Trend strip */}
          {trendHistory.length > 0 && (
            <div className={`rounded-2xl border border-zinc-200 bg-white shadow-sm px-6 py-5 transition-all duration-200 ${isLoadingContractor ? "opacity-40" : ""}`}>
              <div className="flex items-center justify-between mb-4">
                <p className="text-[11px] font-semibold uppercase tracking-widest text-zo-text-muted">
                  Hours Trend
                </p>
                <span className="text-xs text-zo-text-muted">
                  {granularity === "month" ? "Monthly" : "Weekly"} history
                </span>
              </div>
              <div className="flex items-end gap-1.5 h-24">
                {trendHistory.map((point) => {
                  const isSelected = point.start === periodInsights.selected.start;
                  const heightPct = Math.max((point.hours / maxTrendHours) * 100, 4);
                  return (
                    <button
                      key={point.start}
                      onClick={() => onPeriodStartChange(point.start)}
                      title={`${point.start}: ${point.hours.toFixed(1)} hrs`}
                      className="group flex flex-1 flex-col items-center gap-1 cursor-pointer min-w-0"
                    >
                      <div className="w-full flex items-end justify-center h-20">
                        <motion.div
                          initial={{ height: 0 }}
                          animate={{ height: `${heightPct}%` }}
                          transition={{ duration: 0.4, ease: expoOutEase }}
                          className={`w-full max-w-[2rem] rounded-t-md transition-colors ${
                            isSelected
                              ? "bg-[#3C5A56]"
                              : "bg-[#3C5A56]/25 group-hover:bg-[#3C5A56]/45"
                          }`}
                        />
                      </div>
                      <span className={`text-[9px] font-semibold truncate w-full text-center ${isSelected ? "text-[#3C5A56]" : "text-zinc-400"}`}>
                        {point.start.slice(5)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-zinc-300 bg-zinc-50 px-8 py-12 text-center space-y-3">
          <div className="flex justify-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-zinc-100 text-zinc-400">
              <WifiOff className="h-7 w-7" />
            </div>
          </div>
          <h4 className="font-heading text-base font-bold text-foreground">Sheet Disconnected</h4>
          <p className="text-sm text-zo-text-muted max-w-sm mx-auto leading-relaxed">
            Switch status above to <strong className="text-foreground">iWorker Sheet Connected</strong> to
            view live timesheet data.
          </p>
        </div>
      )}

      {/* ─── SHEET GRID PREVIEW ──────────────────────────────────────────────── */}
      {isConnected && showPreview && (
        <div className="rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-100 bg-zinc-50">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-600 text-white">
                <Grid3X3 className="h-4 w-4" />
              </div>
              <div>
                <h4 className="font-semibold text-sm text-foreground">Google Sheet Grid View</h4>
                <p className="text-xs text-zo-text-muted">iWorker Time Tracker · Live Ingested Data</p>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <a
                href={googleSheetUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-[#3C5A56] hover:underline"
              >
                Open in Sheets
                <ExternalLink className="h-3 w-3" />
              </a>
              <button
                onClick={() => setShowPreview(false)}
                className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg hover:bg-zinc-200 text-zinc-400 hover:text-zinc-700 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-left text-xs">
              <thead className="bg-zinc-100 sticky top-0 z-10">
                <tr className="border-b border-zinc-200 text-zinc-500 font-semibold uppercase tracking-wider">
                  <th className="px-4 py-3 text-center">Row</th>
                  <th className="px-4 py-3">Day</th>
                  <th className="px-4 py-3">Date</th>
                  <th className="px-4 py-3">Start</th>
                  <th className="px-4 py-3">End</th>
                  <th className="px-4 py-3">Hours</th>
                  <th className="px-4 py-3 text-right">Amount</th>
                  <th className="px-4 py-3">Task / Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-100 bg-white">
                {filteredAndSorted.map((row, idx) => (
                  <tr key={row.id} className="hover:bg-zinc-50 transition-colors">
                    <td className="px-4 py-3 text-center text-zinc-400 font-mono">{4153 + idx}</td>
                    <td className="px-4 py-3 font-medium text-foreground">{row.day}</td>
                    <td className="px-4 py-3 text-zinc-600">{row.date}</td>
                    <td className="px-4 py-3 font-mono text-zinc-500">{row.start_time || "—"}</td>
                    <td className="px-4 py-3 font-mono text-zinc-500">{row.end_time || "—"}</td>
                    <td className="px-4 py-3 font-mono font-semibold text-blue-600">{row.duration}</td>
                    <td className="px-4 py-3 text-right font-mono font-semibold text-emerald-600">
                      ${row.amount.toFixed(2)}
                    </td>
                    <td className="px-4 py-3 text-zinc-700 max-w-xs truncate">{row.task}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ─── SEARCH + FILTER BAR ─────────────────────────────────────────────── */}
      {isConnected && (
        <div className="rounded-2xl border border-zo-border bg-white shadow-sm overflow-hidden">
          <div className="px-6 py-5 flex flex-col sm:flex-row sm:items-center gap-3.5">

            {/* Search Input */}
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400 pointer-events-none" />
              <input
                type="text"
                placeholder="Search tasks, dates, contractors, status…"
                value={searchQuery}
                onKeyDown={(e) => {
                  if (e.key === "Escape") setSearchQuery("");
                }}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-xl bg-zinc-50 pl-10 pr-10 py-2.5 text-sm text-foreground placeholder-zinc-400 border border-zinc-200 focus:border-[#3C5A56] focus:bg-white focus:outline-none focus:ring-2 focus:ring-[#3C5A56]/20 transition-all"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  title="Clear search"
                  className="absolute right-3 top-1/2 -translate-y-1/2 cursor-pointer text-zinc-400 hover:text-zinc-700 p-1 rounded-md hover:bg-zinc-200 transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>

            {/* Filter Button */}
            <button
              onClick={() => setShowFilters(!showFilters)}
              className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-semibold border transition-all cursor-pointer ${
                showFilters || activeFiltersCount > 0
                  ? "bg-[#3C5A56] text-white border-[#3C5A56]"
                  : "bg-zinc-50 text-zinc-700 border-zinc-200 hover:bg-zinc-100"
              }`}
            >
              <SlidersHorizontal className="h-4 w-4" />
              Filters
              {activeFiltersCount > 0 && (
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white text-[#3C5A56] text-[10px] font-bold">
                  {activeFiltersCount}
                </span>
              )}
            </button>

            {/* AI Rules Toggle */}
            <button
              onClick={() => setShowLegend(!showLegend)}
              className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-semibold border transition-all cursor-pointer ${
                showLegend
                  ? "bg-blue-50 text-blue-700 border-blue-200"
                  : "bg-zinc-50 text-zinc-700 border-zinc-200 hover:bg-zinc-100"
              }`}
            >
              <Info className="h-4 w-4" />
              AI Rules
            </button>

            {/* Divider */}
            <div className="hidden sm:block h-6 w-px bg-zinc-200" />

            {/* Results Count */}
            <div className="text-xs text-zinc-500 font-medium whitespace-nowrap ml-auto flex items-center gap-2">
              {periodFilterEnabled && (
                <button
                  onClick={onTogglePeriodFilter}
                  className="inline-flex items-center gap-1 rounded-full bg-[#3C5A56]/10 text-[#3C5A56] font-semibold px-2.5 py-0.5 border border-[#3C5A56]/20 text-[11px] hover:bg-[#3C5A56]/15 transition-colors cursor-pointer"
                >
                  Filtered to {periodInsights.selected.label}
                  <X className="h-3 w-3" />
                </button>
              )}
              {searchQuery && (
                <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 text-emerald-700 font-semibold px-2.5 py-0.5 border border-emerald-200 text-[11px]">
                  Filtered by "{searchQuery}"
                </span>
              )}
              {viewMode === "GROUPED" ? (
                <>
                  <span className="font-bold text-foreground">{deliverableGroups.length}</span> deliverables ·{" "}
                  <span className="font-bold text-foreground">{filteredAndSorted.length}</span> sessions
                </>
              ) : (
                <>
                  <span className="font-bold text-foreground">{filteredAndSorted.length}</span> entries
                </>
              )}
            </div>
          </div>

          {/* ── Filter Drawer ── */}
          {showFilters && (
            <div className="border-t border-zinc-100 bg-zinc-50/60 px-6 py-5">
              <div className="flex flex-wrap items-end gap-4">

                {/* Year */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400 flex items-center gap-1.5">
                    <CalendarDays className="h-3 w-3" />
                    Year
                  </label>
                  <select
                    value={selectedYear}
                    onChange={(e) => setSelectedYear(e.target.value)}
                    className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm font-medium text-foreground focus:outline-none focus:border-[#3C5A56] cursor-pointer min-w-[110px]"
                  >
                    <option value="ALL">All Years</option>
                    {availableYears.map((year) => (
                      <option key={year} value={year}>{year}</option>
                    ))}
                  </select>
                </div>

                {/* Month */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400 flex items-center gap-1.5">
                    <CalendarDays className="h-3 w-3" />
                    Month
                  </label>
                  <select
                    value={selectedMonth}
                    onChange={(e) => setSelectedMonth(e.target.value)}
                    className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm font-medium text-foreground focus:outline-none focus:border-[#3C5A56] cursor-pointer min-w-[130px]"
                  >
                    <option value="ALL">All Months</option>
                    {["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"].map((m) => (
                      <option key={m} value={m}>{m}</option>
                    ))}
                  </select>
                </div>

                {/* Status */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400 flex items-center gap-1.5">
                    <CheckCircle2 className="h-3 w-3" />
                    AI Status Tag
                  </label>
                  <select
                    value={selectedStatusFilter}
                    onChange={(e) => setSelectedStatusFilter(e.target.value as "ALL" | "BILLABLE" | "OVER_SCOPE")}
                    className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm font-medium text-foreground focus:outline-none focus:border-[#3C5A56] cursor-pointer min-w-[190px]"
                  >
                    <option value="ALL">All Status Tags</option>
                    <option value="BILLABLE">✓ Billable Time (In-Scope)</option>
                    <option value="OVER_SCOPE">⚠️ Over Scope (R3+ Revisions)</option>
                  </select>
                </div>

                {/* Sort */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400 flex items-center gap-1.5">
                    <ArrowUpDown className="h-3 w-3" />
                    Sort
                  </label>
                  <select
                    value={sortOrder}
                    onChange={(e) => setSortOrder(e.target.value as "DESC" | "ASC")}
                    className="rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm font-medium text-foreground focus:outline-none focus:border-[#3C5A56] cursor-pointer min-w-[160px]"
                  >
                    <option value="DESC">Newest First ↓</option>
                    <option value="ASC">Oldest First ↑</option>
                  </select>
                </div>

                {/* Daily View: hide off-days toggle */}
                {viewMode === "DAILY" && (
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400">
                      Options
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer bg-white border border-zinc-200 rounded-lg px-3 py-2 text-sm font-medium text-foreground select-none hover:bg-zinc-50 transition-colors">
                      <input
                        type="checkbox"
                        checked={hideOffDays}
                        onChange={(e) => setHideOffDays(e.target.checked)}
                        className="rounded border-zinc-300 text-[#3C5A56] focus:ring-[#3C5A56] cursor-pointer"
                      />
                      Hide zero-hour days
                    </label>
                  </div>
                )}

                {/* Reset */}
                {activeFiltersCount > 0 && (
                  <button
                    onClick={resetFilters}
                    className="inline-flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-zinc-500 hover:text-[#3C5A56] transition-colors ml-auto self-end pb-2"
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Reset filters
                  </button>
                )}
              </div>
            </div>
          )}

          {/* ── AI Rules Drawer ── */}
          {showLegend && (
            <div className="border-t border-blue-100 bg-blue-50/50 px-6 py-5">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-blue-600" />
                  <h4 className="text-sm font-bold text-foreground">FastAPI AI Classification Rules (Round 3+ Threshold)</h4>
                </div>
                <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-emerald-700">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
                  Trace Logs Active
                </span>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                {/* Round 1 */}
                <div className="flex flex-col justify-between bg-white rounded-xl p-4 border border-emerald-200 shadow-sm space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-emerald-700 uppercase tracking-wider">Round 1</span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                      <CheckCircle2 className="h-3 w-3" /> Billable
                    </span>
                  </div>
                  <p className="text-xs font-bold text-foreground">Baseline Production</p>
                  <p className="text-[11px] text-zinc-500 leading-relaxed">
                    Initial creation, drafting, and asset setup for approved retainer deliverables. 100% in-scope.
                  </p>
                </div>

                {/* Round 2 */}
                <div className="flex flex-col justify-between bg-white rounded-xl p-4 border border-emerald-200 shadow-sm space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-emerald-700 uppercase tracking-wider">Round 2</span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                      <CheckCircle2 className="h-3 w-3" /> Billable
                    </span>
                  </div>
                  <p className="text-xs font-bold text-foreground">First Revisions (Retainer Cap)</p>
                  <p className="text-[11px] text-zinc-500 leading-relaxed">
                    Standard edits, copy updates, and client feedback adjustments covered under retainer cap.
                  </p>
                </div>

                {/* Round 3+ */}
                <div className="flex flex-col justify-between bg-white rounded-xl p-4 border border-orange-200 bg-orange-50/20 shadow-sm space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-orange-600 uppercase tracking-wider">Round 3+</span>
                    <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 px-2 py-0.5 text-[10px] font-bold text-orange-600">
                      <AlertCircle className="h-3 w-3" /> Over Scope
                    </span>
                  </div>
                  <p className="text-xs font-bold text-orange-700">Unbilled Scope Risk Flag</p>
                  <p className="text-[11px] text-zinc-500 leading-relaxed">
                    3rd round or subsequent revisions trigger an AI scope risk flag requiring a client change order.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ─── GROUPED DELIVERABLES VIEW ───────────────────────────────────────── */}
      {isConnected && viewMode === "GROUPED" && (
        <div className="relative">
          {isLoadingContractor && (
            <div className="absolute inset-0 bg-white/70 backdrop-blur-[1px] z-20 rounded-2xl flex items-center justify-center p-12 transition-all">
              <div className="inline-flex items-center gap-2.5 rounded-xl bg-white border border-[#3C5A56]/20 px-5 py-3 shadow-lg text-xs font-semibold text-[#3C5A56] animate-pulse">
                <Loader2 className="h-4 w-4 animate-spin text-[#3C5A56]" />
                Loading contractor sheet deliverables...
              </div>
            </div>
          )}
          <div className={`space-y-4 transition-all duration-200 ${isLoadingContractor ? "opacity-40 pointer-events-none" : ""}`}>
            {deliverableGroups.length === 0 ? (
              <div className="rounded-2xl border border-zo-border bg-white p-16 text-center text-sm text-zinc-400">
                No deliverables match your current filters.
              </div>
            ) : (
              deliverableGroups.map((group) => {
                const isExpanded = !!expandedDeliverables[group.taskName];
                return (
                  <div
                    key={group.taskName}
                    className={`rounded-2xl border overflow-hidden transition-all ${
                      group.isOverScope
                        ? "border-orange-200 bg-white"
                        : "border-zinc-200 bg-white hover:border-zinc-300"
                    } shadow-sm`}
                  >
                    {/* Group Header */}
                    <button
                      onClick={() => toggleExpand(group.taskName)}
                      className="w-full cursor-pointer text-left px-6 py-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4 hover:bg-zinc-50/60 transition-colors"
                    >
                      <div className="flex items-start gap-4 flex-1 min-w-0">
                        {/* Expand icon */}
                        <div className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                          isExpanded ? "bg-zinc-900 border-zinc-900 text-white" : "bg-zinc-100 border-zinc-200 text-zinc-500"
                        }`}>
                          <ChevronDown
                            className="h-4 w-4 transition-transform duration-300"
                            style={{ transform: isExpanded ? "rotate(0deg)" : "rotate(-90deg)" }}
                          />
                        </div>

                        <div className="min-w-0">
                          <div className="flex items-center gap-3 flex-wrap">
                            <span className="font-semibold text-sm text-foreground leading-snug truncate">
                              {group.taskName}
                            </span>
                            {group.isOverScope ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 px-2.5 py-0.5 text-[11px] font-bold text-orange-600 border border-orange-200 shrink-0">
                                <AlertTriangle className="h-3 w-3" />
                                Over Scope
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-0.5 text-[11px] font-bold text-emerald-700 border border-emerald-200 shrink-0">
                                <CheckCircle2 className="h-3 w-3" />
                                Billable
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-zinc-400 mt-1.5">
                            {group.entries.length} sessions logged
                          </p>
                        </div>
                      </div>

                      {/* Right: Stats */}
                      <div className="flex items-center gap-8 shrink-0">
                        <div className="text-right">
                          <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400 mb-0.5">
                            Hours
                          </p>
                          <p className="font-heading font-bold text-lg text-foreground leading-none">
                            {group.totalHours.toFixed(1)}
                            <span className="text-sm font-medium text-zinc-400 ml-1">hrs</span>
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="text-[11px] font-semibold uppercase tracking-widest text-zinc-400 mb-0.5">
                            Spend
                          </p>
                          <p className="font-heading font-bold text-lg text-emerald-600 leading-none">
                            ${group.totalSpend.toFixed(2)}
                          </p>
                        </div>
                      </div>
                    </button>

                    {/* Expanded Session Table */}
                    <AnimatePresence initial={false}>
                      {isExpanded && (
                        <motion.div
                          initial={{ height: 0, opacity: 0 }}
                          animate={{ height: "auto", opacity: 1 }}
                          exit={{ height: 0, opacity: 0 }}
                          transition={{ duration: 0.3, ease: expoOutEase }}
                          className="overflow-hidden border-t border-zinc-100"
                        >
                        <table className="w-full text-left text-xs">
                          <thead className="bg-zinc-50 border-b border-zinc-100">
                            <tr className="text-zinc-400 font-semibold uppercase tracking-wider">
                              <th className="px-6 py-3">Day & Date</th>
                              <th className="px-6 py-3">Time Period</th>
                              <th className="px-6 py-3 text-center">Duration</th>
                              <th className="px-6 py-3 text-right">Amount</th>
                              <th className="px-6 py-3">Week Ending</th>
                              <th className="px-6 py-3 text-center">AI Status</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-zinc-50 bg-white">
                            {group.entries.map((session) => (
                              <tr key={session.id} className="hover:bg-zinc-50/80 transition-colors">
                                <td className="px-6 py-3.5 font-medium text-foreground">
                                  {session.day},{" "}
                                  <span className="text-zinc-400 font-normal">{session.date}</span>
                                </td>
                                <td className="px-6 py-3.5 font-mono text-zinc-400">
                                  {session.start_time
                                    ? `${session.start_time} – ${session.end_time}`
                                    : <Minus className="h-3.5 w-3.5 text-zinc-300" />}
                                </td>
                                <td className="px-6 py-3.5 text-center font-mono font-semibold text-zinc-600">
                                  {session.duration}
                                </td>
                                <td className="px-6 py-3.5 text-right font-mono font-bold text-emerald-600">
                                  ${session.amount.toFixed(2)}
                                </td>
                                <td className="px-6 py-3.5 text-zinc-400">{session.week_ending}</td>
                                <td className="px-6 py-3.5 text-center">
                                  {session.isOverScope ? (
                                    <span className="inline-flex items-center gap-1 text-[11px] font-bold text-orange-600">
                                      <AlertTriangle className="h-3 w-3" />
                                      Over Scope
                                    </span>
                                  ) : (
                                    <span className="inline-flex items-center gap-1 text-[11px] font-bold text-emerald-600">
                                      <CheckCircle2 className="h-3 w-3" />
                                      Billable
                                    </span>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        </motion.div>
                      )}
                    </AnimatePresence>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* ─── DAILY LOG VIEW ──────────────────────────────────────────────────── */}
      {isConnected && viewMode === "DAILY" && (
        <div className="relative">
          {isLoadingContractor && (
            <div className="absolute inset-0 bg-white/70 backdrop-blur-[1px] z-20 rounded-2xl flex items-center justify-center p-12 transition-all">
              <div className="inline-flex items-center gap-2.5 rounded-xl bg-white border border-[#3C5A56]/20 px-5 py-3 shadow-lg text-xs font-semibold text-[#3C5A56] animate-pulse">
                <Loader2 className="h-4 w-4 animate-spin text-[#3C5A56]" />
                Loading daily timesheet logs...
              </div>
            </div>
          )}
          <div className={`rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden transition-all duration-200 ${isLoadingContractor ? "opacity-40 pointer-events-none" : ""}`}>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="bg-zinc-900 text-white text-xs font-semibold uppercase tracking-widest">
                    <th className="px-6 py-4">Day & Date</th>
                    <th className="px-6 py-4">Time Period</th>
                    <th className="px-6 py-4 text-center">Duration</th>
                    <th className="px-6 py-4 text-right">Amount</th>
                    <th className="px-8 py-4">Task / Notes</th>
                    <th className="px-6 py-4 text-center">AI Status</th>
                  </tr>
                </thead>

                {Object.keys(monthGroups).length === 0 ? (
                  <tbody>
                    <tr>
                      <td colSpan={6} className="px-6 py-16 text-center text-sm text-zinc-400">
                        No timesheet logs match your filters.
                      </td>
                    </tr>
                  </tbody>
                ) : (
                  Object.entries(monthGroups).map(([monthYear, groupEntries]) => {
                    const groupHours = groupEntries.reduce((acc, curr) => acc + curr.hours, 0);
                    const groupSpend = groupEntries.reduce((acc, curr) => acc + curr.amount, 0);
                    return (
                      <tbody key={monthYear} className="divide-y divide-zinc-100">
                        {/* Month header row */}
                        <tr className="bg-zinc-50 border-y border-zinc-200">
                          <td colSpan={6} className="px-6 py-3">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2.5">
                                <div className="flex h-5 w-5 items-center justify-center rounded bg-[#3C5A56] text-white">
                                  <CalendarDays className="h-3 w-3" />
                                </div>
                                <span className="font-bold text-xs uppercase tracking-widest text-foreground">
                                  {monthYear}
                                </span>
                                <span className="rounded-full bg-zinc-200 px-2 py-0.5 text-[11px] font-semibold text-zinc-500">
                                  {groupEntries.length} entries
                                </span>
                              </div>
                              <div className="flex items-center gap-4 text-xs text-zinc-500 font-medium">
                                <span>
                                  <span className="font-bold text-foreground">{groupHours.toFixed(1)} hrs</span>
                                </span>
                                <span className="text-zinc-300">·</span>
                                <span className="font-bold text-emerald-600 font-mono">
                                  ${groupSpend.toFixed(2)}
                                </span>
                              </div>
                            </div>
                          </td>
                        </tr>

                        {/* Data rows */}
                        {groupEntries.map((row) => (
                          <tr
                            key={row.id}
                            className={`hover:bg-zinc-50/70 transition-colors ${
                              row.isOverScope ? "bg-orange-50/30" : "bg-white"
                            }`}
                          >
                            <td className="px-6 py-5 whitespace-nowrap">
                              <div className="font-semibold text-sm text-foreground">{row.day}</div>
                              <div className="text-xs text-zinc-400 mt-0.5">{row.date}</div>
                            </td>
                            <td className="px-6 py-5 whitespace-nowrap font-mono text-sm text-zinc-400">
                              {row.start_time ? `${row.start_time} – ${row.end_time}` : "—"}
                            </td>
                            <td className="px-6 py-5 whitespace-nowrap text-center">
                              <span className={`inline-block rounded-lg px-3 py-1.5 text-sm font-mono font-semibold ${
                                row.hours > 0
                                  ? "bg-zinc-100 text-zinc-700 border border-zinc-200"
                                  : "text-zinc-300"
                              }`}>
                                {row.duration}
                              </span>
                            </td>
                            <td className="px-6 py-5 whitespace-nowrap text-right">
                              <div className="font-mono font-bold text-emerald-600 text-sm">
                                {row.amount > 0 ? `$${row.amount.toFixed(2)}` : "$0.00"}
                              </div>
                              {row.hours > 0 && (
                                <div className="text-[10px] text-zinc-400 mt-0.5">
                                  {row.hours.toFixed(1)} × ${(row.rate || summary.hourly_rate_usd).toFixed(2)}
                                </div>
                              )}
                            </td>
                            <td className="px-8 py-5">
                              <div className="text-sm font-medium text-foreground leading-snug max-w-sm">
                                {row.task}
                              </div>
                              <div className="text-xs text-zinc-400 mt-1">
                                Week ending {row.week_ending}
                              </div>
                            </td>
                            <td className="px-6 py-5 whitespace-nowrap text-center">
                              {row.isOverScope ? (
                                <div className="space-y-1">
                                  <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-100 px-3 py-1 text-xs font-semibold text-orange-600 border border-orange-200">
                                    <AlertTriangle className="h-3 w-3" />
                                    Over Scope
                                  </span>
                                  <p className="text-[10px] text-zinc-400">Round 3+ flag</p>
                                </div>
                              ) : row.hours > 0 ? (
                                <div className="space-y-1">
                                  <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-100 px-3 py-1 text-xs font-semibold text-emerald-700 border border-emerald-200">
                                    <CheckCircle2 className="h-3 w-3" />
                                    Billable
                                  </span>
                                  <p className="text-[10px] text-zinc-400">AI: In-Scope</p>
                                </div>
                              ) : (
                                <span className="inline-flex items-center gap-1 text-xs text-zinc-400">
                                  <Minus className="h-3 w-3" />
                                  Off Day
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    );
                  })
                )}
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
