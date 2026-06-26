"use client";

import { useState } from "react";
import { loginUser } from "@/lib/api/auth";
import Link from "next/link";
import { useRouter } from "next/navigation";
import bgImage from "../../../../assets/zo_Grid.webp";
import skateboardBg from "../../../../assets/skateboard-bg-e1752515802592.webp";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const data = await loginUser(email, password);
      // Store the token in localStorage
      localStorage.setItem("auth_token", data.session.access_token);
      localStorage.setItem("auth_user", JSON.stringify(data.user));
      
      // Redirect to home/dashboard
      router.push("/");
    } catch (err: any) {
      setError(err.message || "Failed to log in");
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
        <div className="text-[14px] uppercase tracking-[0.34em] text-[#4a1805] drop-shadow-md font-bold">
          ZO AGENCY
        </div>
        <h1 className="mt-2 text-3xl font-heading font-light text-white drop-shadow-md">Welcome Back</h1>
      </div>

      <div className="zo-card w-full max-w-md p-8 space-y-6">
        <h2 className="text-2xl font-bold text-center font-heading">Log In</h2>
        
        {error && (
          <div className="p-4 text-sm text-red-700 bg-red-100 rounded-lg border border-red-200">
            {error}
          </div>
        )}
        
        <form className="space-y-5" onSubmit={handleLogin}>
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
          </div>
          
          <button
            type="submit"
            disabled={loading}
            className="zo-btn w-full !py-3 !mt-6"
          >
            {loading ? "LOGGING IN..." : "LOG IN"}
          </button>
        </form>

        <p className="text-sm text-center text-[var(--zo-text-muted)] pt-4">
          Don't have an account?{" "}
          <Link href="/signup" className="text-[var(--zo-primary)] hover:underline font-medium">
            Sign up
          </Link>
        </p>
      </div>
    </div>
  );
}
