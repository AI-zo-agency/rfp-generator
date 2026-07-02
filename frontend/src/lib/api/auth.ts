export const API_BASE_URL = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.BACKEND_URL || 'http://localhost:8001';

export async function signupUser(email: string, password: string, redirectUrl?: string) {
  const res = await fetch(`${API_BASE_URL}/api/auth/signup`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ email, password, redirect_url: redirectUrl }),
  });

  if (!res.ok) {
    const errorData = await res.json().catch(() => null);
    throw new Error(errorData?.detail || 'Signup failed');
  }

  return res.json();
}

export async function loginUser(email: string, password: string) {
  try {
    const res = await fetch(`${API_BASE_URL}/api/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email, password }),
      signal: AbortSignal.timeout(30_000),
    });

    if (!res.ok) {
      const errorData = await res.json().catch(() => null);
      throw new Error(errorData?.detail || 'Login failed');
    }

    return res.json();
  } catch (err: unknown) {
    throw new Error(loginFetchError(err));
  }
}

function loginFetchError(err: unknown): string {
  if (err instanceof DOMException && err.name === 'TimeoutError') {
    return 'Login timed out — the server may be busy running a proposal. Try again in a moment.';
  }
  if (err instanceof Error && err.name === 'TimeoutError') {
    return 'Login timed out — the server may be busy running a proposal. Try again in a moment.';
  }
  return err instanceof Error ? err.message : 'Login failed';
}
