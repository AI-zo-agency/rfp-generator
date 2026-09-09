"use client";

import { AnimatePresence, motion } from "motion/react";
import { Menu } from "lucide-react";
import { expoOutEase } from "@/lib/motion";

interface FinancialHeaderProps {
  title: string;
  subtitle: string;
  onOpenNav?: () => void;
}

export function FinancialHeader({ title, subtitle, onOpenNav }: FinancialHeaderProps) {
  return (
    <header className="flex min-w-0 shrink-0 items-start gap-3">
      {onOpenNav ? (
        <motion.button
          type="button"
          onClick={onOpenNav}
          className="shell-icon-btn mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center border md:hidden"
          aria-label="Open financial sections"
          whileTap={{ scale: 0.94 }}
        >
          <Menu className="h-4 w-4" strokeWidth={1.75} />
        </motion.button>
      ) : null}
      <div className="min-w-0 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={title}
            initial={{ opacity: 0, y: 12, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
            transition={{ duration: 0.38, ease: expoOutEase }}
          >
            <h1 className="font-heading text-[1.125rem] leading-none tracking-tight text-foreground">
              {title}
            </h1>
            <p className="mt-1.5 text-[13px] leading-snug text-[var(--zo-text-secondary)]">{subtitle}</p>
          </motion.div>
        </AnimatePresence>
      </div>
    </header>
  );
}
