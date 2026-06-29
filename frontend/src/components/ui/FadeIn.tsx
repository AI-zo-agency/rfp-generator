"use client";

import { motion } from "motion/react";
import { expoOutEase } from "@/lib/motion";

type FadeInProps = Readonly<{
  children: React.ReactNode;
  className?: string;
  delay?: number;
}>;

export function FadeIn({ children, className = "", delay = 0 }: FadeInProps) {
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.42, ease: expoOutEase, delay }}
    >
      {children}
    </motion.div>
  );
}

export function FadeInStagger({
  children,
  className = "",
}: Readonly<{ children: React.ReactNode; className?: string }>) {
  return (
    <motion.div
      className={className}
      initial="hidden"
      animate="visible"
      variants={{
        hidden: {},
        visible: { transition: { staggerChildren: 0.07, delayChildren: 0.04 } },
      }}
    >
      {children}
    </motion.div>
  );
}

export function FadeInItem({
  children,
  className = "",
}: Readonly<{ children: React.ReactNode; className?: string }>) {
  return (
    <motion.div
      className={className}
      variants={{
        hidden: { opacity: 0, y: 8 },
        visible: {
          opacity: 1,
          y: 0,
          transition: { duration: 0.38, ease: expoOutEase },
        },
      }}
    >
      {children}
    </motion.div>
  );
}
