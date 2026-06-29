"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { authStagger, expoOutEase } from "@/lib/motion";

const AUTH_GRID = "/auth/zo_Grid.webp";
const AUTH_SKATE = "/auth/skateboard-bg.webp";

function preloadImage(src: string): Promise<void> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve();
    img.onerror = () => resolve();
    img.src = src;
  });
}

const shellReveal = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { duration: 0.5, ease: expoOutEase },
  },
};

export function AuthGate({ children }: Readonly<{ children: React.ReactNode }>) {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const reveal = () => {
      if (!cancelled) setReady(true);
    };

    Promise.all([preloadImage(AUTH_GRID), preloadImage(AUTH_SKATE)]).then(
      reveal,
    );

    const timeout = globalThis.setTimeout(reveal, 3500);

    return () => {
      cancelled = true;
      globalThis.clearTimeout(timeout);
    };
  }, []);

  return (
    <AnimatePresence mode="wait">
      {!ready ? (
        <motion.div
          key="auth-blocker"
          className="auth-blocker"
          aria-busy="true"
          aria-label="Loading sign in"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.45, ease: expoOutEase }}
        >
          <div className="auth-blocker__glow" />
          <div className="auth-blocker__ring" />
          <motion.span
            className="auth-blocker__label"
            initial={{ opacity: 0.4 }}
            animate={{ opacity: [0.4, 1, 0.4] }}
            transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
          >
            ZO AGENCY
          </motion.span>
        </motion.div>
      ) : (
        <motion.div
          key="auth-shell"
          className="auth-shell"
          variants={shellReveal}
          initial="hidden"
          animate="visible"
        >
          <div className="auth-shell__bg" aria-hidden>
            <div className="auth-shell__bg-photo" />
            <div className="auth-shell__bg-grid" />
          </div>
          <div className="auth-shell__vignette" aria-hidden />
          <div className="auth-shell__glow auth-shell__glow--left" aria-hidden />
          <div
            className="auth-shell__glow auth-shell__glow--right"
            aria-hidden
          />
          <motion.div
            className="auth-shell__content flex min-h-screen flex-col items-center justify-center p-4"
            variants={{ visible: { transition: authStagger } }}
            initial="hidden"
            animate="visible"
          >
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
