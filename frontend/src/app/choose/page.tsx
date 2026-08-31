"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Instrument_Serif } from "next/font/google";
import { motion } from "motion/react";
import { ZoLogo } from "@/components/ZoLogo";
import {
  IconArrowRight,
  IconFinancial,
  IconPipeline,
  IconRfp,
} from "@/components/ui/icons";
import { expoOutEase } from "@/lib/motion";

const chooseSerif = Instrument_Serif({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
});

interface StoredUser {
  email?: string;
  user_metadata?: { full_name?: string; name?: string };
}

function readGreetingName(): string | null {
  try {
    const raw = localStorage.getItem("auth_user");
    if (!raw) return null;
    const user: StoredUser = JSON.parse(raw);
    return (
      user.user_metadata?.full_name ||
      user.user_metadata?.name ||
      user.email?.split("@")[0] ||
      null
    );
  } catch {
    return null;
  }
}

export default function ChooseWorkspacePage() {
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const [name, setName] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("auth_token");
    if (!token) {
      router.replace("/login");
      return;
    }
    setName(readGreetingName());
    setReady(true);
  }, [router]);

  const handleLogout = () => {
    localStorage.removeItem("auth_token");
    localStorage.removeItem("auth_user");
    router.push("/login");
  };

  if (!ready) {
    return (
      <div className="flex h-dvh w-full items-center justify-center bg-[var(--zo-bg)]">
        <div className="flex flex-col items-center gap-4">
          <div className="h-10 w-10 animate-spin rounded-full border-[3px] border-[var(--zo-primary)] border-t-transparent" />
          <span className="text-sm font-medium tracking-widest uppercase text-[var(--zo-text-muted)]">
            ZO AGENCY
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-dvh bg-[var(--zo-bg)]">
      <header className="mx-auto flex max-w-[1200px] items-center justify-between px-6 py-6 sm:px-8 md:px-10">
        <ZoLogo size="compact" />
        <button
          type="button"
          onClick={handleLogout}
          className="zo-btn secondary !px-4 !py-2.5 text-xs"
        >
          Log out
        </button>
      </header>

      <main className="mx-auto flex max-w-[1200px] flex-col px-6 pb-16 pt-6 sm:px-8 md:px-10 md:pt-10">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: expoOutEase }}
          className="mb-10 md:mb-14"
        >
          <h1
            className={`${chooseSerif.className} text-3xl leading-[1.05] tracking-tight text-foreground sm:text-4xl md:text-[3.25rem]`}
          >
            {name ? `Welcome back, ${name}.` : "Welcome back."}
          </h1>
          <p className="mt-3 max-w-xl text-base leading-7 text-zo-text-muted md:text-lg">
            Choose where you want to work.
          </p>
        </motion.div>

        <div className="grid gap-5 sm:gap-6 lg:grid-cols-3">
          <WorkspaceCard
            href="/rfp-dashboard"
            tone="rfp"
            icon={<IconRfp className="h-6 w-6" />}
            title="RFP Intelligence"
            description="Sync opportunities, run Go/No-Go against the knowledge base, and draft proposals grounded in real agency evidence."
            capabilities={["Go/No-Go", "Proposal Drafting", "Knowledge Base"]}
            delay={0.08}
          />
          <WorkspaceCard
            href="/financial-insights"
            tone="financial"
            icon={<IconFinancial className="h-6 w-6" />}
            title="Financial Dashboard"
            description="Review ingested timesheets, AI-classified tasks, and audit queues for scope risk and review amounts."
            capabilities={["Timesheets", "Task Classification", "Audit Queues"]}
            delay={0.18}
          />
          <WorkspaceCard
            href="/lead-finder"
            tone="leads"
            icon={<IconPipeline className="h-6 w-6" />}
            title="Prospect Outreach"
            description="Prioritize contacts by sector fit and engagement, then open a prep brief before you reach out."
            capabilities={["Lead Scoring", "AI Enrichment", "Prep Briefs"]}
            delay={0.28}
          />
        </div>
      </main>
    </div>
  );
}

type WorkspaceTone = "rfp" | "financial" | "leads";

const TONES: Record<
  WorkspaceTone,
  { card: string; hover: string; iconBox: string; pill: string; arrow: string }
> = {
  rfp: {
    card: "bg-[#FFF4EE] border-[#ef5018]/12",
    hover:
      "hover:shadow-[0_20px_48px_rgba(239,80,24,0.16)] focus-visible:ring-[var(--zo-primary)]",
    iconBox: "bg-[#ef5018]/12 text-[#ef5018]",
    pill: "bg-[#ef5018]/10 text-[#c43d0f]",
    arrow: "bg-[#ef5018] text-white group-hover:bg-[#d94512]",
  },
  financial: {
    card: "bg-[#EEF3F1] border-[#274742]/12",
    hover:
      "hover:shadow-[0_20px_48px_rgba(39,71,66,0.18)] focus-visible:ring-[#274742]",
    iconBox: "bg-[#274742]/12 text-[#274742]",
    pill: "bg-[#274742]/10 text-[#274742]",
    arrow: "bg-[#274742] text-white group-hover:bg-[#1e3632]",
  },
  leads: {
    card: "bg-[#F3F0EB] border-[#6B5744]/14",
    hover:
      "hover:shadow-[0_20px_48px_rgba(107,87,68,0.18)] focus-visible:ring-[#6B5744]",
    iconBox: "bg-[#6B5744]/12 text-[#6B5744]",
    pill: "bg-[#6B5744]/10 text-[#6B5744]",
    arrow: "bg-[#6B5744] text-white group-hover:bg-[#554433]",
  },
};

function WorkspaceCard({
  href,
  tone,
  icon,
  title,
  description,
  capabilities,
  delay,
}: Readonly<{
  href: string;
  tone: WorkspaceTone;
  icon: React.ReactNode;
  title: string;
  description: string;
  capabilities: string[];
  delay: number;
}>) {
  const palette = TONES[tone];

  return (
    <motion.div
      className="h-full"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: expoOutEase, delay }}
    >
      <Link
        href={href}
        className={`group transition-smooth relative flex h-full min-h-[340px] flex-col overflow-hidden rounded-2xl border p-7 shadow-[0_8px_28px_rgba(15,23,42,0.06)] hover:-translate-y-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 sm:min-h-[400px] sm:p-8 ${palette.card} ${palette.hover}`}
      >
        <div
          className={`mb-6 flex h-12 w-12 items-center justify-center rounded-xl ${palette.iconBox}`}
        >
          {icon}
        </div>

        <h2
          className={`${chooseSerif.className} text-2xl leading-[1.1] tracking-tight text-[#0a0f1a] sm:text-[1.75rem]`}
        >
          {title}
        </h2>

        <p className="mt-3 line-clamp-4 max-w-md text-sm leading-relaxed text-[#0a0f1a]/65 sm:text-[15px] sm:leading-6">
          {description}
        </p>

        <div className="mt-5 flex flex-wrap gap-2">
          {capabilities.map((capability) => (
            <div
              key={capability}
              className={`rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.06em] ${palette.pill}`}
            >
              {capability}
            </div>
          ))}
        </div>

        <div className="mt-auto flex items-center justify-end pt-8">
          <div
            className={`transition-smooth flex h-11 w-11 items-center justify-center rounded-full group-hover:translate-x-1 ${palette.arrow}`}
          >
            <IconArrowRight className="h-[18px] w-[18px]" />
          </div>
        </div>
      </Link>
    </motion.div>
  );
}
