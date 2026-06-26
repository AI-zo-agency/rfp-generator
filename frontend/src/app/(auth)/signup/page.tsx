"use client";

import { useState } from "react";
import { signupUser } from "@/lib/api/auth";
import Link from "next/link";
import { useRouter } from "next/navigation";
import bgImage from "../../../../assets/zo_Grid.webp";
import skateboardBg from "../../../../assets/skateboard-bg-e1752515802592.webp";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const hasMinLength = password.length >= 8;
  const hasLowercase = /[a-z]/.test(password);
  const hasUppercase = /[A-Z]/.test(password);
  const hasSpecial = /[!@#$%^&*(),.?":{}|<>]/.test(password);
  const isPasswordValid = hasMinLength && hasLowercase && hasUppercase && hasSpecial;

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

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
      const redirectUrl = `${window.location.origin}/login`;
      const data = await signupUser(email, password, redirectUrl);
      setSuccess(data.message || "Signup successful. Please check your email to confirm.");
    } catch (err: any) {
      setError(err.message || "Failed to sign up");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div 
      className="shell-app flex min-h-screen flex-col items-center justify-center p-4"
      style={{ 
        backgroundImage: `url(${bgImage.src}), url(${skateboardBg.src})`, 
        backgroundSize: 'cover, cover', 
        backgroundPosition: 'center, center' 
      }}
    >
      
      <div className="mb-8 text-center">
        <div className="text-[14px] uppercase tracking-[0.34em] text-white drop-shadow-md font-bold">
          ZO AGENCY
        </div>
        <h1 className="mt-2 text-3xl font-heading font-light text-white drop-shadow-md">Create an Account</h1>
      </div>

      <div className="zo-card w-full max-w-md p-8 space-y-6">
        <h2 className="text-2xl font-bold text-center font-heading">Sign Up</h2>
        
        {error && (
          <div className="p-4 text-sm text-red-700 bg-red-100 rounded-lg border border-red-200">
            {error}
          </div>
        )}
        
        {success ? (
          <div className="p-6 text-sm text-[var(--zo-teal)] bg-[#e6f3f0] rounded-xl border border-[var(--zo-teal)] shadow-sm text-center">
            {success}
            <div className="mt-6">
              <Link href="/login" className="zo-btn secondary !py-2 w-full">
                GO TO LOGIN
              </Link>
            </div>
          </div>
        ) : (
          <form className="space-y-5" onSubmit={handleSignup}>
            <div>
              <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
                Email
              </label>
              <input
                type="email"
                required
                className="zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Enter your email"
              />
            </div>
            
            <div>
              <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
                Password
              </label>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  required
                  className="zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth pr-12"
                  value={password}
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
              {password && (
                <div className="mt-2 text-xs space-y-1">
                  <p className={hasMinLength ? "text-green-600" : "text-gray-500"}>
                    {hasMinLength ? "✓" : "○"} At least 8 characters
                  </p>
                  <p className={hasLowercase ? "text-green-600" : "text-gray-500"}>
                    {hasLowercase ? "✓" : "○"} At least 1 lowercase letter
                  </p>
                  <p className={hasUppercase ? "text-green-600" : "text-gray-500"}>
                    {hasUppercase ? "✓" : "○"} At least 1 uppercase letter
                  </p>
                  <p className={hasSpecial ? "text-green-600" : "text-gray-500"}>
                    {hasSpecial ? "✓" : "○"} At least 1 special character
                  </p>
                </div>
              )}
            </div>

            <div>
              <label className="block mb-2 text-sm font-medium text-[var(--zo-text-secondary)]">
                Confirm Password
              </label>
              <div className="relative">
                <input
                  type={showConfirmPassword ? "text" : "password"}
                  required
                  className="zo-input w-full px-4 py-3 focus:outline-none focus:ring-2 focus:ring-[var(--zo-primary)] focus:border-transparent transition-smooth pr-12"
                  value={confirmPassword}
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
            </div>
            
            <button
              type="submit"
              disabled={loading}
              className="zo-btn w-full !py-3 !mt-6"
            >
              {loading ? "SIGNING UP..." : "SIGN UP"}
            </button>
          </form>
        )}

        {!success && (
          <p className="text-sm text-center text-[var(--zo-text-muted)] pt-4">
            Already have an account?{" "}
            <Link href="/login" className="text-[var(--zo-primary)] hover:underline font-medium">
              Log in
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
