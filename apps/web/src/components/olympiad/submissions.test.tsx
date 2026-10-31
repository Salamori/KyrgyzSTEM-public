import { act, cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SubmissionDetail } from "@/lib/api/client";
import { makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { SubmissionView } from "./Submissions";

const ID = "00000000-0000-4000-8000-000000000051";
const DETAIL = `GET /api/v1/submissions/${ID}`;

function submission(overrides: Partial<SubmissionDetail> = {}): SubmissionDetail {
  return {
    id: ID,
    problem_slug: "demo",
    problem_title: "Demo problem",
    language: "python",
    state: "queued",
    verdict: null,
    score: null,
    hints_revealed_count: 0,
    created_at: "2026-10-27T00:00:00Z",
    source_code: "print(3)",
    group_results: [],
    ...overrides,
  };
}

const DONE = submission({
  state: "done",
  verdict: "accepted",
  score: 100,
  group_results: [{ ordinal: 1, points: 100, verdict: "accepted", score: 100 }],
});

/** Serve the detail route from a list of answers (the last one repeats); count calls. */
function sequence(answers: { status: number; body: unknown }[]) {
  const calls = { n: 0 };
  const fetchMock = mockApi({
    "GET /api/v1/auth/session": sessionFor(makeUser()),
    [DETAIL]: () => answers[Math.min(calls.n++, answers.length - 1)]!,
  });
  return { calls, fetchMock };
}

const ok = (s: SubmissionDetail) => ({ status: 200, body: s });

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("submission checking status", () => {
  it("polls while queued or judging and stops at the final verdict", async () => {
    const { calls } = sequence([
      ok(submission()),
      ok(submission()),
      ok(submission({ state: "judging" })),
      ok(DONE),
    ]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    expect(screen.getByText("Checking your solution… This page updates by itself.")).toBeInTheDocument();
    expect(calls.n).toBe(1);
    await advance(2_000); // first poll: still queued
    expect(calls.n).toBe(2);
    await advance(3_000); // second poll, 1.5x later: judging
    expect(calls.n).toBe(3);
    expect(screen.getByText("Checking…")).toBeInTheDocument();
    await advance(4_500); // third: done
    expect(calls.n).toBe(4);
    expect(screen.getAllByText("Accepted").length).toBeGreaterThan(0);
    expect(screen.queryByText(/Checking your solution/)).not.toBeInTheDocument();
    await advance(10 * 60_000);
    expect(calls.n).toBe(4); // no polling after a final state
  });

  it("does not poll a final state at all", async () => {
    const { calls } = sequence([ok(DONE)]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(60_000);
    expect(calls.n).toBe(1);
  });

  it("stops polling when the page is left", async () => {
    const { calls } = sequence([ok(submission())]);
    const view = renderWithProviders(<SubmissionView id={ID} />);
    await advance(2_000);
    const before = calls.n;
    view.unmount();
    await advance(5 * 60_000);
    expect(calls.n).toBe(before);
  });

  it("never sends a request while one is still running", async () => {
    let detailCalls = 0;
    let inFlight = 0;
    let maxInFlight = 0;
    const waiting: (() => void)[] = [];
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const req = input instanceof Request ? input : new Request(input);
        if (new URL(req.url).pathname === "/api/v1/auth/session") return json(sessionFor(makeUser()).body);
        detailCalls++;
        if (detailCalls === 1) return json(submission()); // the page's first load
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        await new Promise<void>((resolve) => waiting.push(resolve)); // a slow network
        inFlight--;
        return json(submission());
      }),
    );
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    await advance(2_000); // the first poll starts and hangs
    await advance(5 * 60_000); // long after: still only that poll
    expect(detailCalls).toBe(2);
    expect(maxInFlight).toBe(1);
    waiting.shift()?.(); // it answers; the next poll comes after the next interval
    await advance(3_000);
    expect(detailCalls).toBe(3);
    expect(maxInFlight).toBe(1);
    for (const resolve of waiting) resolve();
  });

  it("retries temporary errors and reaches the verdict", async () => {
    const failure = { status: 503, body: { error: { code: "unavailable", message: "x", details: null } } };
    const { calls } = sequence([ok(submission()), failure, failure, ok(DONE)]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    await advance(2_000 + 3_000 + 4_500);
    expect(calls.n).toBe(4);
    expect(screen.getAllByText("Accepted").length).toBeGreaterThan(0);
  });

  it("stops and says so when the session has expired", async () => {
    const expired = { status: 401, body: { error: { code: "not_authenticated", message: "x", details: null } } };
    const { calls } = sequence([ok(submission()), expired]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    await advance(2_000);
    expect(screen.getByRole("alert")).toHaveTextContent("Please sign in to continue.");
    await advance(5 * 60_000);
    expect(calls.n).toBe(2);
  });

  it("gives up after ten minutes and offers to check again", async () => {
    const { calls } = sequence([ok(submission())]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    await advance(11 * 60_000);
    expect(screen.getByText(/Still checking/)).toBeInTheDocument();
    const bounded = calls.n;
    expect(bounded).toBeLessThanOrEqual(50); // at most every 15 s once slowed down
    await advance(10 * 60_000);
    expect(calls.n).toBe(bounded);
    await act(async () => {
      screen.getByRole("button", { name: "Check again" }).click();
    });
    await advance(2_000);
    expect(calls.n).toBe(bounded + 1);
  });

  it("explains a checker failure without any internal detail", async () => {
    sequence([ok(submission({ state: "judge_error" }))]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    expect(screen.getByText(/This is not a problem with your solution/)).toBeInTheDocument();
    expect(screen.queryByText(/Checking your solution/)).not.toBeInTheDocument();
  });

  it("explains a submission that is not checked", async () => {
    sequence([ok(submission({ state: "not_judged" }))]);
    renderWithProviders(<SubmissionView id={ID} />);
    await advance(0);
    expect(screen.getByText(/Automatic checking is not available yet/)).toBeInTheDocument();
  });
});
