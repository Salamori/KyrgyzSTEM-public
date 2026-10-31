import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ProblemDetail, ProgressOut } from "@/lib/api/client";
import { anonymous, makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { Markdown } from "../Markdown";
import { ProblemBank } from "./ProblemBank";
import { ProblemView } from "./ProblemView";
import { ProgressView } from "./ProgressView";

function makeProblem(overrides: Partial<ProblemDetail> = {}): ProblemDetail {
  return {
    id: "00000000-0000-4000-8000-0000000000aa",
    slug: "demo",
    title: "Demo problem",
    locale: "en",
    status: "published",
    difficulty_band: "easy",
    difficulty_status: "author_estimate",
    topics: [{ slug: "sorting", name: "Sorting", stage: 1, is_primary: true }],
    tags: [],
    origin: "original",
    license: "CC-BY-4.0",
    my_status: null,
    is_fallback: false,
    available_locales: ["ru", "en"],
    legend_md: "Find the `answer`.",
    input_md: "One line.",
    output_md: "One number.",
    notes_md: null,
    time_limit_ms: 1000,
    memory_limit_mb: 256,
    scoring: "ioi",
    groups: [{ ordinal: 1, points: 100, description_md: "All" }],
    samples: [{ input: "1 2\n", output: "3\n" }],
    difficulty_rationale: "One idea.",
    attribution_md: "Original problem for tests.",
    authors_md: "Test suite",
    generated_with_ai: true,
    source: null,
    hint_count: 2,
    revealed_hint_count: 0,
    editorial_available: true,
    editorial_unlocked: false,
    languages: ["cpp", "python"],
    judge_available: false,
    review: null,
    ...overrides,
  };
}

const problemRoute = (p: ProblemDetail) => ({ "GET /api/v1/problems/demo": { status: 200, body: p } });

describe("ProblemBank", () => {
  it("lists problems with band provenance and status", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "GET /api/v1/topics": { status: 200, body: [] },
      "GET /api/v1/problems": {
        status: 200,
        body: { items: [{ ...makeProblem(), my_status: "solved" }], total: 1, limit: 20, offset: 0 },
      },
    });
    renderWithProviders(<ProblemBank />);
    expect(await screen.findByRole("link", { name: "Demo problem" })).toHaveAttribute("href", "/problems/demo");
    expect(screen.getByText("Author's estimate")).toBeInTheDocument();
    expect(screen.getByText("Solved")).toBeInTheDocument();
  });

  it("explains an empty bank honestly", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "GET /api/v1/topics": { status: 200, body: [] },
      "GET /api/v1/problems": { status: 200, body: { items: [], total: 0, limit: 20, offset: 0 } },
    });
    renderWithProviders(<ProblemBank />);
    expect(await screen.findByText(/appear here after a human review/)).toBeInTheDocument();
  });
});

describe("ProblemView", () => {
  it("shows statement, samples, licence and sign-in prompts for visitors", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous, ...problemRoute(makeProblem()) });
    renderWithProviders(<ProblemView slug="demo" />);
    expect(await screen.findByRole("heading", { name: "Demo problem" })).toBeInTheDocument();
    expect(screen.getByText("answer").tagName).toBe("CODE");
    expect(screen.getByText(/CC-BY-4.0/)).toBeInTheDocument();
    expect(screen.getByText("This problem was drafted with AI assistance.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in to use hints." })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sign in to submit solutions." })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Review and publishing" })).not.toBeInTheDocument();
  });

  it("flags fallback statements and unpublished problems", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      ...problemRoute(makeProblem({ is_fallback: true, locale: "ru", status: "in_review" })),
    });
    renderWithProviders(<ProblemView slug="demo" />, "ky");
    expect(await screen.findByText(/Көрсөтүлгөнү: Русский/)).toBeInTheDocument();
    expect(screen.getByText(/Текшерүүдө/)).toBeInTheDocument();
  });

  it("shows not found for a missing problem", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<ProblemView slug="demo" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Page not found");
  });

  it("reveals hints one at a time", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      ...problemRoute(makeProblem()),
      "GET /api/v1/problems/demo/hints": { status: 200, body: { total: 2, revealed: [] } },
      "POST /api/v1/problems/demo/hints/1/reveal": {
        status: 200,
        body: { total: 2, revealed: [{ ordinal: 1, locale: "en", body_md: "Think about parity." }] },
      },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    await userEvent.click(await screen.findByRole("button", { name: "Show hint 1 of 2" }));
    expect(await screen.findByText("Think about parity.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show hint 2 of 2" })).toBeInTheDocument();
  });

  it("asks for confirmation before giving up", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      ...problemRoute(makeProblem({ hint_count: 0 })),
      "POST /api/v1/problems/demo/editorial/unlock": {
        status: 200,
        body: { locale: "en", is_fallback: false, body_md: "Sort first.", reviewed: false, unlock_reason: "gave_up" },
      },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    await userEvent.click(await screen.findByRole("button", { name: "I give up — show the editorial" }));
    expect(screen.getByText(/Giving up is recorded/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Yes, show it" }));
    expect(await screen.findByText("Sort first.")).toBeInTheDocument();
    expect(screen.getByText("This editorial has not been reviewed by a person yet.")).toBeInTheDocument();
  });

  it("keeps a local draft and explains that checking is unavailable", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      ...problemRoute(makeProblem({ hint_count: 0 })),
      "POST /api/v1/submissions": {
        status: 201,
        body: {
          id: "00000000-0000-4000-8000-0000000000bb",
          problem_slug: "demo",
          problem_title: "Demo problem",
          language: "cpp",
          state: "not_judged",
          verdict: null,
          score: null,
          hints_revealed_count: 0,
          created_at: "2026-10-24T00:00:00Z",
          source_code: "int main(){}",
          group_results: [],
        },
      },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    const editor = await screen.findByLabelText("Your code");
    await userEvent.type(editor, "int main(){{}"); // "{{" types a literal "{"
    await waitFor(() => expect(window.localStorage.getItem("ks:draft:demo:cpp")).toBe("int main(){}"));
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Automatic checking is not available yet.");
    expect(screen.getByRole("link", { name: "View my submissions" })).toHaveAttribute(
      "href",
      "/submissions/00000000-0000-4000-8000-0000000000bb",
    );
  });

  it("follows a judged submission until its verdict", async () => {
    const id = "00000000-0000-4000-8000-0000000000bc";
    const base = {
      id,
      problem_slug: "demo",
      problem_title: "Demo problem",
      language: "cpp",
      verdict: null,
      score: null,
      hints_revealed_count: 0,
      created_at: "2026-10-24T00:00:00Z",
      source_code: "int main(){}",
      group_results: [],
    };
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      ...problemRoute(makeProblem({ hint_count: 0, judge_available: true })),
      "POST /api/v1/submissions": { status: 201, body: { ...base, state: "queued" } },
      [`GET /api/v1/submissions/${id}`]: {
        status: 200,
        body: { ...base, state: "done", verdict: "wrong_answer", score: 0 },
      },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    await userEvent.type(await screen.findByLabelText("Your code"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByText(/Checking your solution/)).toBeInTheDocument();
    // The first poll comes after 2 s (real timers here); the verdict replaces the notice.
    expect(await screen.findByText(/^Wrong answer/, {}, { timeout: 4_000 })).toBeInTheDocument();
    expect(screen.queryByText(/Checking your solution/)).not.toBeInTheDocument();
  });

  it("shows the review panel with blockers to admins", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      ...problemRoute(
        makeProblem({
          status: "in_review",
          review: {
            reviewed_locales: [],
            rights_verified_at: null,
            author_linked: true,
            editorial_locales: [],
            reviewed_editorial_locales: [],
            hint_review: [],
            publish_blockers: ["statement_unreviewed", "difficulty_not_reviewed"],
            blocking_items: [
              { code: "statement_unreviewed", item: "statement", locale: "ru", ordinal: null },
              { code: "difficulty_not_reviewed", item: "difficulty", locale: null, ordinal: null },
            ],
          },
        }),
      ),
      "GET /api/v1/admin/problems/demo/editorials": { status: 200, body: [] },
      "GET /api/v1/admin/problems/demo/hints": { status: 200, body: [] },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    expect(await screen.findByRole("heading", { name: "Review and publishing" })).toBeInTheDocument();
    expect(screen.getByText("Reviewed: statements 0 of 2, editorials 0 of 0, hints 0 of 0")).toBeInTheDocument();
    expect(screen.getByText("A reviewed statement in every language it exists in: Русский")).toBeInTheDocument();
    expect(screen.getByText("A reviewed difficulty")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Publish" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Mark Русский statement as reviewed" })).toBeInTheDocument();
  });

  it("needs a deliberate confirmation before rights are verified", async () => {
    const fetch = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      ...problemRoute(
        makeProblem({
          status: "in_review",
          origin: "adapted",
          license: "CC-BY-SA-4.0",
          source: { title: "Old task", author: "Someone", url: "https://example.com/t", license: "CC-BY-SA-4.0" },
          review: {
            reviewed_locales: [],
            rights_verified_at: null,
            author_linked: false,
            editorial_locales: [],
            reviewed_editorial_locales: [],
            hint_review: [],
            publish_blockers: ["rights_not_verified"],
            blocking_items: [{ code: "rights_not_verified", item: "rights", locale: null, ordinal: null }],
          },
        }),
      ),
      "GET /api/v1/admin/problems/demo/editorials": { status: 200, body: [] },
      "GET /api/v1/admin/problems/demo/hints": { status: 200, body: [] },
      "POST /api/v1/admin/problems/demo/rights/verify": { status: 200, body: makeProblem() },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    expect(await screen.findByText("A person confirmed the right to publish under this license")).toBeInTheDocument();
    expect(screen.getByText(/rights in AI-drafted text are uncertain/)).toBeInTheDocument();
    expect(screen.getAllByText(/Source: Old task by Someone \(CC-BY-SA-4.0\)/)).toHaveLength(2);
    expect(screen.getByText(/cannot check that you are not an author/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "Confirm rights" });
    expect(confirm).toBeDisabled();
    await userEvent.click(screen.getByLabelText(/we have the right to publish this problem/));
    expect(confirm).toBeDisabled(); // the author attestation is still missing
    await userEvent.click(screen.getByLabelText(/I am not an author or co-author/));
    await userEvent.click(confirm);
    await waitFor(() => {
      const call = fetch.mock.calls
        .map(([r]) => r as Request)
        .find((r) => r.url.endsWith("/admin/problems/demo/rights/verify"));
      expect(call).toBeDefined();
    });
    const sent = fetch.mock.calls
      .map(([r]) => r as Request)
      .find((r) => r.url.endsWith("/admin/problems/demo/rights/verify"));
    expect(await sent?.json()).toEqual({ attest_not_author: true });
  });

  it("shows each editorial before it can be marked reviewed, without unlocking it", async () => {
    const reviewState = {
      reviewed_locales: [],
      rights_verified_at: null,
      author_linked: false,
      editorial_locales: ["en" as const],
      reviewed_editorial_locales: [],
      hint_review: [],
      publish_blockers: ["rights_not_verified"],
      blocking_items: [{ code: "rights_not_verified", item: "rights", locale: null, ordinal: null }],
    };
    const fetch = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      ...problemRoute(makeProblem({ status: "in_review", review: reviewState })),
      "GET /api/v1/admin/problems/demo/hints": { status: 200, body: [] },
      "GET /api/v1/admin/problems/demo/editorials": {
        status: 200,
        body: [
          { locale: "en", body_md: "Sort, then sweep once.", body_sha256: "0".repeat(64), reviewed: false, reviewed_at: null },
        ],
      },
      "POST /api/v1/admin/problems/demo/editorials/en/review": {
        status: 200,
        body: makeProblem({
          status: "in_review",
          review: { ...reviewState, reviewed_editorial_locales: ["en" as const] },
        }),
      },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    const item = await screen.findByRole("region", { name: "English editorial" });
    expect(within(item).getByText("Sort, then sweep once.")).toBeInTheDocument();
    expect(within(item).getByText(/not reviewed yet/)).toBeInTheDocument();
    const mark = within(item).getByRole("button", { name: "Mark English editorial as reviewed" });
    expect(mark).toBeDisabled();
    await userEvent.click(within(item).getByLabelText(/I have read the English editorial/));
    expect(mark).toBeDisabled(); // the author attestation is still missing
    await userEvent.click(within(item).getByLabelText(/I am not an author or co-author/));
    await userEvent.click(mark);
    expect(await within(item).findByText(/: reviewed/)).toBeInTheDocument();

    const requests = fetch.mock.calls.map(([r]) => r as Request);
    const review = requests.find((r) => r.url.endsWith("/admin/problems/demo/editorials/en/review"));
    expect(review?.method).toBe("POST");
    expect(await review?.json()).toEqual({ attest_not_author: true });
    // The student editorial endpoints are never touched by the reviewer UI.
    expect(requests.some((r) => /\/problems\/demo\/editorial(\/unlock)?(\?|$)/.test(r.url))).toBe(false);
  });
  it("reviews hints per language without ever revealing them", async () => {
    const reviewState = {
      reviewed_locales: [],
      rights_verified_at: null,
      author_linked: false,
      editorial_locales: [],
      reviewed_editorial_locales: [],
      hint_review: [{ locale: "en" as const, total: 2, reviewed: 0 }],
      publish_blockers: ["rights_not_verified"],
      blocking_items: [{ code: "rights_not_verified", item: "rights", locale: null, ordinal: null }],
    };
    const hint = (ordinal: number, reviewed: boolean) => ({
      locale: "en",
      ordinal,
      body_md: `Think about step ${ordinal}.`,
      body_sha256: "0".repeat(64),
      reviewed,
      reviewed_at: reviewed ? "2026-10-26T00:00:00Z" : null,
    });
    let listCalls = 0;
    const fetch = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      ...problemRoute(makeProblem({ status: "in_review", review: reviewState })),
      "GET /api/v1/admin/problems/demo/editorials": { status: 200, body: [] },
      "GET /api/v1/admin/problems/demo/hints": () => {
        listCalls += 1;
        return { status: 200, body: listCalls === 1 ? [hint(1, false), hint(2, false)] : [hint(1, true), hint(2, false)] };
      },
      "POST /api/v1/admin/problems/demo/hints/en/1/review": {
        status: 200,
        body: makeProblem({ status: "in_review", review: { ...reviewState, hint_review: [{ locale: "en", total: 2, reviewed: 1 }] } }),
      },
      "POST /api/v1/admin/problems/demo/hints/en/review": {
        status: 200,
        body: makeProblem({ status: "in_review", review: { ...reviewState, hint_review: [{ locale: "en", total: 2, reviewed: 2 }] } }),
      },
    });
    renderWithProviders(<ProblemView slug="demo" />);
    await userEvent.click(await screen.findByText("English: 0 of 2 hints reviewed"));
    const section = screen.getByRole("region", { name: "English hints" });
    expect(within(section).getByText("Think about step 1.")).toBeInTheDocument();
    expect(within(section).getByText("Think about step 2.")).toBeInTheDocument();
    const one = within(section).getByRole("button", { name: "Mark hint 1 (English) as reviewed" });
    const all = within(section).getByRole("button", { name: "Mark all English hints as reviewed" });
    expect(one).toBeDisabled();
    expect(all).toBeDisabled();
    await userEvent.click(within(section).getByLabelText(/I have read all English hints/));
    expect(one).toBeDisabled(); // the author attestation is still missing
    await userEvent.click(within(section).getByLabelText(/I am not an author or co-author/));
    await userEvent.click(one);
    expect(await within(section).findByText(/Hint 1 · reviewed/)).toBeInTheDocument();
    await userEvent.click(all);

    const urls = () => fetch.mock.calls.map(([r]) => (r as Request).url);
    await waitFor(() => expect(urls().some((u) => u.endsWith("/admin/problems/demo/hints/en/review"))).toBe(true));
    const requests = fetch.mock.calls.map(([r]) => r as Request);
    const single = requests.find((r) => r.url.endsWith("/admin/problems/demo/hints/en/1/review"));
    const bulk = requests.find((r) => r.url.endsWith("/admin/problems/demo/hints/en/review"));
    expect(single?.method).toBe("POST");
    expect(await single?.json()).toEqual({ attest_not_author: true });
    expect(bulk?.method).toBe("POST");
    expect(await bulk?.json()).toEqual({ attest_not_author: true });
    // Reviewing never reveals a hint: no reveal call, and every write goes to an admin endpoint.
    // (The page's own student hints panel only reads which hints this admin has revealed.)
    expect(urls().some((u) => /\/hints\/\d+\/reveal/.test(u))).toBe(false);
    const writes = fetch.mock.calls.map(([r]) => r as Request).filter((r) => r.method !== "GET");
    expect(writes.length).toBe(2);
    expect(writes.every((r) => r.url.includes("/api/v1/admin/problems/demo/hints/"))).toBe(true);
  });
});

describe("ProgressView", () => {
  const progress: ProgressOut = {
    judge_available: false,
    pending_not_judged: 3,
    topics: [
      {
        slug: "sorting",
        name: "Sorting",
        stages: [
          { stage: 1, unaided_by_band: { easy: 2 }, with_hints: 1, demonstrated: true },
          { stage: 2, unaided_by_band: {}, with_hints: 0, demonstrated: false },
        ],
      },
    ],
    hint_reliance_30d: null,
    median_attempts_to_accept: null,
    demonstrated_topics: 1,
    total_topics: 13,
    demonstrated_threshold: 2,
    self_reported: [],
  };

  it("is honest about missing evidence", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/me/progress": { status: 200, body: progress },
    });
    renderWithProviders(<ProgressView />);
    expect(await screen.findByText(/Saved solutions waiting for checking: 3/)).toBeInTheDocument();
    expect(screen.getAllByText("Not enough data yet")).toHaveLength(2);
    expect(screen.getByText("1 of 13")).toBeInTheDocument();
    expect(screen.getByText("Solved without hints: Easy 2")).toBeInTheDocument();
  });

  it("requires sign-in", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<ProgressView />);
    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
  });
});

describe("Markdown", () => {
  it("never renders raw HTML or images", () => {
    const { container } = render(
      <Markdown>{'Hi <img src=x onerror="alert(1)"> <script>alert(1)</script> ![a](http://x/y.png) **bold**'}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("strong")).toHaveTextContent("bold");
  });

  it("neutralises script URLs and opens links safely", () => {
    const { container } = render(
      <Markdown>{"[a](javascript:alert(1)) [b](https://example.com/x) [c](data:text/html,x)"}</Markdown>,
    );
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.some((h) => /^(javascript|data):/i.test(h))).toBe(false);
    const safe = container.querySelector('a[href="https://example.com/x"]');
    expect(safe).toHaveAttribute("rel", "noopener noreferrer nofollow");
  });

  it("can drop links entirely for unreviewed text", () => {
    const { container } = render(<Markdown links={false}>{"[b](https://example.com/x)"}</Markdown>);
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent("b");
  });
});
