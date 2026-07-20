"use client";

import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { expoOutEase } from "@/lib/motion";

export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <motion.div
      key={pathname}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.38, ease: expoOutEase }}
      className="flex min-h-0 w-full min-w-0 flex-1 flex-col"
    >
      {children}
    </motion.div>
  );
}
