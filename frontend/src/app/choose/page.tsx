"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { ZoLogo } from "@/components/ZoLogo";
import {
  IconArrowRight,
  IconFinancial,
  IconPipeline,
  IconRfp,
} from "@/components/ui/icons";
import { expoOutEase } from "@/lib/motion";

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
          <h1 className="font-heading text-3xl leading-[1.05] text-foreground sm:text-4xl md:text-5xl">
            {name ? `Welcome back, ${name}.` : "Welcome back."}
          </h1>
          <p className="mt-3 max-w-xl text-base leading-7 text-zo-text-muted md:text-lg">
            Choose where you want to work.
          </p>
        </motion.div>

        <div className="grid gap-6 md:grid-cols-3 md:gap-6">
          <WorkspaceCard
            href="/rfp-dashboard"
            tone="rfp"
            icon={<IconRfp className="h-7 w-7" />}
            title="RFP Intelligence"
            description="Sync opportunities, run Go/No-Go against the knowledge base, and draft proposals grounded in real agency evidence."
            capabilities={["Go/No-Go", "Proposal Drafting", "Knowledge Base"]}
            delay={0.08}
          />
          <WorkspaceCard
            href="/financial-insights"
            tone="financial"
            icon={<IconFinancial className="h-7 w-7" />}
            title="Financial Dashboard"
            description="Review ingested timesheets, AI-classified tasks, and audit queues for scope risk and review amounts."
            capabilities={["Timesheets", "Task Classification", "Audit Queues"]}
            delay={0.18}
          />
          <WorkspaceCard
            href="/lead-finder"
            tone="leads"
            icon={<IconPipeline className="h-7 w-7" />}
            title="Prospect Outreach"
            description="Prioritize contacts by sector fit and engagement, then open a prep brief before you reach out. Drafts no messaging."
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
  { hover: string; glow: string; iconBox: string; pill: string; arrow: string }
> = {
  rfp: {
    hover:
      "hover:shadow-[0_24px_60px_rgba(239,80,24,0.18)] focus-visible:ring-[var(--zo-primary)]",
    glow: "bg-[#ef5018]/15",
    iconBox: "bg-black/[0.06] text-black",
    pill: "bg-black/[0.06] text-black/70",
    arrow: "bg-black text-white group-hover:bg-[var(--zo-primary)]",
  },
  financial: {
    hover:
      "hover:shadow-[0_24px_60px_rgba(60,90,86,0.22)] focus-visible:ring-[#3C5A56]",
    glow: "bg-[#3C5A56]/12",
    iconBox: "bg-[#3C5A56]/10 text-[#3C5A56]",
    pill: "bg-[#3C5A56]/10 text-[#3C5A56]",
    arrow: "bg-[#3C5A56] text-white group-hover:bg-[#2e4744]",
  },
  leads: {
    hover:
      "hover:shadow-[0_24px_60px_rgba(39,71,66,0.22)] focus-visible:ring-[#274742]",
    glow: "bg-[#274742]/12",
    iconBox: "bg-[#274742]/10 text-[#274742]",
    pill: "bg-[#274742]/10 text-[#274742]",
    arrow: "bg-[#274742] text-white group-hover:bg-[#1a2f2c]",
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
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: expoOutEase, delay }}
    >
      <Link
        href={href}
        className={`group transition-smooth zo-panel-white relative flex min-h-[360px] flex-col overflow-hidden rounded-2xl p-8 hover:-translate-y-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 sm:min-h-[420px] sm:p-10 ${palette.hover}`}
      >
        <div
          className={`pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full blur-3xl ${palette.glow}`}
        />

        <div
          className={`relative z-10 mb-6 flex h-14 w-14 items-center justify-center rounded-xl ${palette.iconBox}`}
        >
          {icon}
        </div>

        <h2 className="font-heading relative z-10 text-2xl font-semibold leading-[1.05] text-black sm:text-3xl md:text-4xl">
          {title}
        </h2>

        <p className="relative z-10 mt-4 max-w-md text-sm leading-relaxed text-black/70 sm:text-base">
          {description}
        </p>

        <div className="relative z-10 mt-6 flex flex-wrap gap-2">
          {capabilities.map((capability) => (
            <div
              key={capability}
              className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.06em] ${palette.pill}`}
            >
              {capability}
            </div>
          ))}
        </div>

        <div className="relative z-10 mt-auto flex items-center justify-end pt-8">
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
