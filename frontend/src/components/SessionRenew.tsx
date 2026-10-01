"use client";

import { useEffect } from "react";
import { clearSession, freshAccessToken } from "@/lib/auth-session";
import { ZoAmuletLoader } from "./ZoAmuletLoader";

/**
 * A server-rendered page got 401 because the `zo_token` cookie expired while
 * the tab was idle. Renew the token (which rewrites the cookie) and reload.
 */
export function SessionRenew() {
  useEffect(() => {
    freshAccessToken(fetch, true).then((token) => {
      if (token) {
        window.location.reload();
      } else {
        clearSession();
        window.location.assign("/login");
      }
    });
  }, []);
  return <ZoAmuletLoader fullScreen label="Refreshing your session" />;
}
