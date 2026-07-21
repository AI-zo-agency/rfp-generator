"use client";

import { motion } from "motion/react";
import { authTransition, expoOutEase } from "@/lib/motion";

const fadeUp = {
  hidden: { opacity: 0, y: 32, filter: "blur(10px)" },
  visible: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: authTransition,
  },
};

const cardReveal = {
  hidden: { opacity: 0, y: 48, scale: 0.92 },
  visible: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { duration: 0.9, ease: expoOutEase },
  },
};

const fieldReveal = {
  hidden: { opacity: 0, x: -16 },
  visible: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.55, ease: expoOutEase },
  },
};

type AuthPageShellProps = Readonly<{
  headline: string;
  formTitle: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  onSubmit?: (e: React.FormEvent<HTMLFormElement>) => void;
  formAutoComplete?: string;
  formNoValidate?: boolean;
}>;

const staggerFields = {
  visible: { transition: { staggerChildren: 0.09, delayChildren: 0.35 } },
};

export function AuthPageShell({
  headline,
  formTitle,
  children,
  footer,
  onSubmit,
  formAutoComplete,
  formNoValidate,
}: AuthPageShellProps) {
  return (
    <>
      <motion.header
        className="auth-header mb-8 text-center"
        variants={fadeUp}
        initial="hidden"
        animate="visible"
      >
        <motion.div
          className="auth-eyebrow text-[14px] uppercase tracking-[0.34em] text-white font-bold"
          initial={{ letterSpacing: "0.5em", opacity: 0 }}
          animate={{ letterSpacing: "0.34em", opacity: 1 }}
          transition={{ duration: 1, ease: expoOutEase, delay: 0.25 }}
        >
          ZO AGENCY
        </motion.div>
        <h1 className="auth-headline mt-3 text-3xl font-heading font-light text-white md:text-4xl">
          {headline}
        </h1>
        <motion.div
          className="auth-headline-rule mx-auto mt-4 h-px w-16 bg-gradient-to-r from-transparent via-[var(--zo-primary)] to-transparent"
          initial={{ scaleX: 0, opacity: 0 }}
          animate={{ scaleX: 1, opacity: 1 }}
          transition={{ duration: 0.8, ease: expoOutEase, delay: 0.45 }}
        />
      </motion.header>

      <motion.div
        className="auth-card zo-card w-full max-w-md p-8 space-y-6"
        variants={cardReveal}
        initial="hidden"
        animate="visible"
      >
        <motion.h2
          className="text-2xl font-bold text-center font-heading"
          variants={fadeUp}
          initial="hidden"
          animate="visible"
        >
          {formTitle}
        </motion.h2>

        {onSubmit ? (
          <motion.form
            className="space-y-5"
            initial="hidden"
            animate="visible"
            variants={staggerFields}
            onSubmit={onSubmit}
            autoComplete={formAutoComplete}
            noValidate={formNoValidate}
          >
            {children}
          </motion.form>
        ) : (
          <motion.div
            className="space-y-5"
            initial="hidden"
            animate="visible"
            variants={staggerFields}
          >
            {children}
          </motion.div>
        )}

        {footer ? (
          <motion.div
            variants={fadeUp}
            initial="hidden"
            animate="visible"
            transition={{ delay: 0.15 }}
          >
            {footer}
          </motion.div>
        ) : null}
      </motion.div>
    </>
  );
}

export function AuthField({
  children,
  className = "",
}: Readonly<{ children: React.ReactNode; className?: string }>) {
  return (
    <motion.div
      className={className}
      variants={fieldReveal}
      initial="hidden"
      animate="visible"
    >
      {children}
    </motion.div>
  );
}

export function AuthSubmitButton({
  children,
  disabled,
  className = "zo-btn w-full !py-3 !mt-2",
}: Readonly<{
  children: React.ReactNode;
  disabled?: boolean;
  className?: string;
}>) {
  return (
    <motion.div
      variants={{
        hidden: { opacity: 0, y: 20, scale: 0.96 },
        visible: {
          opacity: 1,
          y: 0,
          scale: 1,
          transition: { duration: 0.65, ease: expoOutEase },
        },
      }}
      initial="hidden"
      animate="visible"
    >
      <motion.button
        type="submit"
        disabled={disabled}
        className={`auth-submit-btn ${className}`}
        whileHover={disabled ? undefined : { y: -3, scale: 1.01 }}
        whileTap={disabled ? undefined : { scale: 0.98 }}
        transition={{ duration: 0.22, ease: expoOutEase }}
      >
        {children}
      </motion.button>
    </motion.div>
  );
}
