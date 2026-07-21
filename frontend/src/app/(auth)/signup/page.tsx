"use client";

import { useEffect, useState } from "react";
import { signupUser } from "@/lib/api/auth";
import Link from "next/link";
import {
  AuthField,
  AuthPageShell,
  AuthSubmitButton,
} from "@/components/AuthPageShell";
import { AnimatePresence, motion } from "motion/react";
import { expoOutEase } from "@/lib/motion";

const SIGNUP_DOMAIN_ERROR = "Different domain not allowed.";

function isZoAgencyEmail(email: string): boolean {
  const trimmed = email.trim().toLowerCase();
  const at = trimmed.lastIndexOf("@");
  if (at < 0) return false;
  return trimmed.slice(at + 1) === "zo.agency";
}

/** Inline error only after a full non-@zo.agency domain (not while typing @ or zo.agency). */
function shouldShowSignupDomainError(email: string): boolean {
  const trimmed = email.trim().toLowerCase();
  const at = trimmed.lastIndexOf("@");
  if (at < 0) return false;
  const domain = trimmed.slice(at + 1);
  if (!domain) return false;
  if (domain === "zo.agency") return false;
  if ("zo.agency".startsWith(domain)) return false;
  if (!domain.includes(".")) return false;
  return domain !== "zo.agency";
}

function PasswordHint({
  ok,
  label,
}: Readonly<{ ok: boolean; label: string }>) {
  return (
    <motion.p
      className={ok ? "text-green-600" : "text-gray-500"}
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, ease: expoOutEase }}
    >
      {ok ? "✓" : "○"} {label}
    </motion.p>
  );
}

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [fieldsReady, setFieldsReady] = useState(false);

  useEffect(() => {
    setEmail("");
    setPassword("");
    setConfirmPassword("");
    setFieldsReady(false);
  }, []);

  const hasMinLength = password.length >= 8;
  const hasLowercase = /[a-z]/.test(password);
  const hasUppercase = /[A-Z]/.test(password);
  const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(password);
  const isPasswordValid =
    hasMinLength && hasLowercase && hasUppercase && hasSpecial;

  const handleSignup = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!isZoAgencyEmail(email)) {
      setError(SIGNUP_DOMAIN_ERROR);
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (!isPasswordValid) {
      setError("Please meet all password requirements");
      return;
    }

    setLoading(true);

    try {
      const redirectUrl = `${globalThis.location.origin}/login`;
      const data = await signupUser(email, password, redirectUrl);
      setSuccess(
        data.message ||
          "Signup successful. Please check your email to confirm.",
      );
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to sign up";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  const domainInvalid = shouldShowSignupDomainError(email);

  if (success) {
    return (
      <AuthPageShell headline="Create an Account" formTitle="Sign Up">
        <motion.div
          className="p-6 text-sm text-[var(--zo-teal)] bg-[#e6f3f0] rounded-xl border border-[var(--zo-teal)] shadow-sm text-center"
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.55, ease: expoOutEase }}
        >
          {success}
          <div className="mt-6">
            <Link href="/login" className="zo-btn secondary !py-2 w-full">
              GO TO LOGIN
            </Link>
          </div>
        </motion.div>
      </AuthPageShell>
    );
  }

  return (
    <AuthPageShell
      headline="Create an Account"
      formTitle="Sign Up"
      formAutoComplete="off"
      formNoValidate
      onSubmit={handleSignup}
      footer={
        <p className="text-sm text-center text-[var(--zo-text-muted)] pt-4">
          Already have an account?{" "}
          <Link
            href="/login"
            className="auth-link text-[var(--zo-primary)] font-medium"
          >
            Log in
          </Link>
        </p>
      }
    >
      <AnimatePresence>
        {error ? (
          <motion.div
            key="signup-error"
            className="p-4 text-sm text-red-700 bg-red-100 rounded-lg border border-red-200"
            initial={{ opacity: 0, y: -8, height: 0 }}
            animate={{ opacity: 1, y: 0, height: "auto" }}
            exit={{ opacity: 0, y: -8, height: 0 }}
            transition={{ duration: 0.35, ease: expoOutEase }}
          >
            {error}
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AuthField>
        <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
          Email
        </label>
        <input
          type="email"
          name="signup-email"
          required
          readOnly={!fieldsReady}
          autoComplete="off"
          className={`auth-input zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth ${
            domainInvalid ? "border-red-500 ring-1 ring-red-200" : ""
          }`}
          value={email}
          onFocus={() => setFieldsReady(true)}
          onChange={(e) => {
            setEmail(e.target.value);
            if (error === SIGNUP_DOMAIN_ERROR) setError(null);
          }}
          placeholder="name@zo.agency"
          aria-invalid={domainInvalid}
        />
        {domainInvalid ? (
          <p className="mt-2 text-sm text-red-600" role="alert">
            {SIGNUP_DOMAIN_ERROR}
          </p>
        ) : null}
      </AuthField>

      <AuthField>
        <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
          Password
        </label>
        <div className="relative">
          <input
            type={showPassword ? "text" : "password"}
            name="signup-password"
            required
            readOnly={!fieldsReady}
            autoComplete="new-password"
            className="auth-input zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth pr-12"
            value={password}
            onFocus={() => setFieldsReady(true)}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Create a password"
          />
          <button
            type="button"
            className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 focus:outline-none"
            onClick={() => setShowPassword(!showPassword)}
          >
            {showPassword ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" x2="22" y1="2" y2="22"/></svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
            )}
          </button>
        </div>
        <AnimatePresence>
          {password ? (
            <motion.div
              className="mt-2 text-xs space-y-1"
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              transition={{ duration: 0.35, ease: expoOutEase }}
            >
              <PasswordHint ok={hasMinLength} label="At least 8 characters" />
              <PasswordHint
                ok={hasLowercase}
                label="At least 1 lowercase letter"
              />
              <PasswordHint
                ok={hasUppercase}
                label="At least 1 uppercase letter"
              />
              <PasswordHint
                ok={hasSpecial}
                label="At least 1 special character"
              />
            </motion.div>
          ) : null}
        </AnimatePresence>
      </AuthField>

      <AuthField>
        <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
          Confirm Password
        </label>
        <div className="relative">
          <input
            type={showConfirmPassword ? "text" : "password"}
            name="signup-password-confirm"
            required
            readOnly={!fieldsReady}
            autoComplete="new-password"
            className="auth-input zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth pr-12"
            value={confirmPassword}
            onFocus={() => setFieldsReady(true)}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="Confirm your password"
          />
          <button
            type="button"
            className="absolute right-4 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 focus:outline-none"
            onClick={() => setShowConfirmPassword(!showConfirmPassword)}
          >
            {showConfirmPassword ? (
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/><line x1="2" x2="22" y1="2" y2="22"/></svg>
            ) : (
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
            )}
          </button>
        </div>
      </AuthField>

      <AuthSubmitButton disabled={loading}>
        {loading ? "SIGNING UP..." : "SIGN UP"}
      </AuthSubmitButton>
    </AuthPageShell>
  );
}
