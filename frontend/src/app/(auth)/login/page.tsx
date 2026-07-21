"use client";

import { useState } from "react";
import { loginUser } from "@/lib/api/auth";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AuthField,
  AuthPageShell,
  AuthSubmitButton,
} from "@/components/AuthPageShell";
import { AnimatePresence, motion } from "motion/react";
import { expoOutEase } from "@/lib/motion";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const data = await loginUser(email, password);
      localStorage.setItem("auth_token", data.session.access_token);
      localStorage.setItem("auth_user", JSON.stringify(data.user));
      router.push("/");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to log in";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthPageShell
      headline="Welcome Back"
      formTitle="Log In"
      onSubmit={handleLogin}
      footer={
        <p className="text-sm text-center text-[var(--zo-text-muted)] pt-4">
          Don&apos;t have an account?{" "}
          <Link
            href="/signup"
            className="auth-link text-[var(--zo-primary)] font-medium"
          >
            Sign up
          </Link>
        </p>
      }
    >
      <AnimatePresence>
        {error ? (
          <motion.div
            key="login-error"
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
          name="login-email"
          required
          autoComplete="username"
          className="auth-input zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Enter your email"
        />
      </AuthField>

      <AuthField>
        <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
          Password
        </label>
        <div className="relative">
          <input
            type={showPassword ? "text" : "password"}
            name="login-password"
            required
            autoComplete="current-password"
            className="auth-input zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth pr-12"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter your password"
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
      </AuthField>

      <AuthSubmitButton disabled={loading}>
        {loading ? "LOGGING IN..." : "LOG IN"}
      </AuthSubmitButton>
    </AuthPageShell>
  );
}
