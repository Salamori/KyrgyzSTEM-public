"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError, unwrap, type SubmissionDetail } from "@/lib/api/client";
import type { Locale } from "@/lib/i18n";

/** States in which the judge worker may still change the submission. */
export function isPending(state: SubmissionDetail["state"]): boolean {
  return state === "queued" || state === "judging";
}

/** Bounded polling: first after 2 s, then 1.5× slower up to 15 s; given up after 10 min. */
export const POLL = { firstMs: 2_000, factor: 1.5, maxMs: 15_000, giveUpMs: 10 * 60_000 } as const;

export type PollStop = null | "gave_up" | "expired" | "failed";

/**
 * The live state of a submission. While it is queued or judging, its detail is fetched
 * again (one request at a time, never overlapping) until it reaches a final state.
 * Polling stops on unmount, on a final state, after `POLL.giveUpMs`, and on a response that
 * retrying cannot fix: 401 (`expired`: the session ended), 403 or 404 (`failed`). Network
 * and server errors are retried with the same growing interval.
 */
export function useSubmissionStatus(initial: SubmissionDetail | null, locale: Locale) {
  const [current, setCurrent] = useState<SubmissionDetail | null>(initial);
  const [stopped, setStopped] = useState<PollStop>(null);
  const [round, setRound] = useState(0); // bumped by `retry()` to start polling again
  const [seen, setSeen] = useState(initial);
  if (initial !== seen) {
    // A new submission (or a reload) replaces what was polled before.
    setSeen(initial);
    setCurrent(initial);
    setStopped(null);
  }

  const id = current?.id ?? null;
  const pending = current !== null && isPending(current.state);

  useEffect(() => {
    if (id === null || !pending) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const started = Date.now();
    let delay: number = POLL.firstMs;

    const tick = async () => {
      if (cancelled) return;
      if (Date.now() - started >= POLL.giveUpMs) {
        setStopped("gave_up");
        return;
      }
      try {
        const next = await unwrap(
          api.GET("/api/v1/submissions/{submission_id}", {
            params: { path: { submission_id: id }, query: { locale } },
            signal: controller.signal,
          }),
        );
        if (cancelled) return;
        setCurrent(next);
        if (!isPending(next.state)) return; // final: the effect ends with it
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && [401, 403, 404].includes(err.status)) {
          setStopped(err.status === 401 ? "expired" : "failed");
          return;
        }
        // Temporary (network, 5xx, 429): try again later, at the growing interval.
      }
      delay = Math.min(POLL.maxMs, delay * POLL.factor);
      timer = setTimeout(() => void tick(), delay);
    };

    timer = setTimeout(() => void tick(), delay);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort();
    };
  }, [id, pending, locale, round]);

  const retry = useCallback(() => {
    setStopped(null);
    setRound((r) => r + 1);
  }, []);

  return { submission: current, polling: pending && stopped === null, stopped, retry };
}
