"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type Load<T> =
  | { status: "loading" }
  | { status: "error"; error: unknown }
  | { status: "ready"; data: T };

const LOADING = { status: "loading" } as const;

/**
 * Run `fetcher` on mount and whenever `deps` change (compared by value).
 * Results from a previous `deps` value are never shown for the current one.
 */
export function useLoad<T>(fetcher: () => Promise<T>, deps: readonly unknown[]) {
  const key = JSON.stringify(deps);
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });
  const [result, setResult] = useState<{ key: string; load: Load<T> }>({ key, load: LOADING });

  const run = useCallback((forKey: string) => {
    let cancelled = false;
    fetcherRef.current().then(
      (data) => !cancelled && setResult({ key: forKey, load: { status: "ready", data } }),
      (error: unknown) => !cancelled && setResult({ key: forKey, load: { status: "error", error } }),
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => run(key), [key, run]);

  const reload = useCallback(async () => {
    setResult({ key, load: LOADING });
    try {
      setResult({ key, load: { status: "ready", data: await fetcherRef.current() } });
    } catch (error) {
      setResult({ key, load: { status: "error", error } });
    }
  }, [key]);

  const setData = useCallback((data: T) => setResult({ key, load: { status: "ready", data } }), [key]);
  const state: Load<T> = result.key === key ? result.load : LOADING;
  return { state, reload, setData };
}
