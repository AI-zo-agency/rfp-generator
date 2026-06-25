"use client";

import { usePathname } from "next/navigation";
import { motion } from "motion/react";
import { fastTransition } from "@/lib/motion";

export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <motion.div
      key={pathname}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={fastTransition}
    >
      {children}
    </motion.div>
  );
}
