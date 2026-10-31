"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, ApiError, unwrap, type UserPrivate } from "./api/client";
import { clearDrafts } from "./drafts";

export type SessionState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "error"; error: ApiError }
  | { status: "authenticated"; user: UserPrivate };

interface SessionValue {
  state: SessionState;
  refresh: () => Promise<void>;
  setUser: (user: UserPrivate) => void;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });

  const refresh = useCallback(async () => {
    try {
      const info = await unwrap(api.GET("/api/v1/auth/session"));
      setState({ status: "authenticated", user: info.user });
    } catch (err) {
      const error = err instanceof ApiError ? err : new ApiError(0, "generic", String(err));
      setState(error.status === 401 ? { status: "anonymous" } : { status: "error", error });
    }
  }, []);

  useEffect(() => {
    // Initial session fetch: setState happens after the request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh();
  }, [refresh]);

  const setUser = useCallback((user: UserPrivate) => setState({ status: "authenticated", user }), []);

  const signOut = useCallback(async () => {
    try {
      await unwrap(api.POST("/api/v1/auth/logout"));
    } finally {
      clearDrafts();
      setState({ status: "anonymous" });
    }
  }, []);

  const value = useMemo(() => ({ state, refresh, setUser, signOut }), [state, refresh, setUser, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}
