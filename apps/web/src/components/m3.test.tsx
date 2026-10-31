import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "@/app/page";
import LearningPathsPage from "@/app/learning-paths/page";
import type { ProgressOut } from "@/lib/api/client";
import { anonymous, makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { AdminShell } from "./admin/AdminShell";
import { AuditLog } from "./admin/AuditLog";
import { ContentQueue } from "./admin/ContentQueue";
import { Dashboard } from "./olympiad/Dashboard";
import { ProblemBank } from "./olympiad/ProblemBank";
import { TopicList } from "./olympiad/TopicList";
import { PrivacyPanel } from "./PrivacyPanel";
import { Providers } from "./Providers";
import { SiteHeader } from "./SiteHeader";

const nav = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => nav.pathname,
}));

beforeEach(() => {
  nav.pathname = "/";
});

const emptyPage = { items: [], total: 0, limit: 20, offset: 0 };

const progress = (overrides: Partial<ProgressOut> = {}): ProgressOut => ({
  judge_available: false,
  pending_not_judged: 0,
  topics: [
    { slug: "sorting", name: "Sorting", stages: [{ stage: 1, unaided_by_band: {}, with_hints: 0, demonstrated: false }] },
  ],
  hint_reliance_30d: null,
  median_attempts_to_accept: null,
  demonstrated_topics: 0,
  total_topics: 13,
  demonstrated_threshold: 2,
  self_reported: [],
  ...overrides,
});

describe("public pages", () => {
  it("landing explains the mission and the current status honestly", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<HomePage />);
    expect(screen.getByText(/free and open source/)).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("automatic checking of solutions is not connected yet");
    expect(await screen.findByRole("link", { name: "Get started" })).toHaveAttribute("href", "/register");
  });

  it("landing sends signed-in users to their dashboard", async () => {
    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser()) });
    renderWithProviders(<HomePage />);
    expect(await screen.findByRole("link", { name: "Go to your dashboard" })).toHaveAttribute("href", "/dashboard");
  });

  it("learning paths say they are not available instead of showing fake content", () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<LearningPathsPage />);
    expect(screen.getByText(/Learning paths are being prepared/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse topics" })).toHaveAttribute("href", "/topics");
  });

  it("topic list shows prerequisites and links to the filtered catalogue", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "GET /api/v1/topics": {
        status: 200,
        body: [
          { slug: "sorting", name: "Sorting", summary_md: "Order things.", stage_count: 4, prerequisites: [] },
          {
            slug: "two-pointers",
            name: "Two pointers",
            summary_md: null,
            stage_count: 4,
            prerequisites: [
              { stage: 1, requires: "sorting", requires_stage: 1, strength: "required" },
              { stage: 1, requires: "prefix-sums", requires_stage: 1, strength: "helpful" },
            ],
          },
        ],
      },
    });
    renderWithProviders(<TopicList />);
    const card = (await screen.findByRole("heading", { name: "Two pointers" })).closest("li")!;
    expect(within(card).getByText("Recommended first: Sorting (stage 1)")).toBeInTheDocument();
    expect(within(card).getByText("Also helpful: prefix-sums (stage 1)")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /Problems in this topic/ })).toHaveAttribute(
      "href",
      "/problems?topic=two-pointers",
    );
    expect(screen.getByText("A good place to start.")).toBeInTheDocument();
  });

  it("catalogue applies filters from the URL", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/auth/session": anonymous,
      "GET /api/v1/topics": { status: 200, body: [] },
      "GET /api/v1/problems": { status: 200, body: emptyPage },
    });
    renderWithProviders(<ProblemBank initial={{ topic: "greedy", band: "bogus" }} />);
    await screen.findByText(/No problems match your filters/);
    const urls = fetchMock.mock.calls.map(([req]) => (req as Request).url).filter((u) => u.includes("/problems"));
    expect(urls.some((u) => u.includes("topic=greedy"))).toBe(true);
    expect(urls.every((u) => !u.includes("band="))).toBe(true); // invalid band ignored
  });
});

describe("student pages", () => {
  it("dashboard shows empty states without fabricated data", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ display_name: "Aidai" })),
      "GET /api/v1/submissions": { status: 200, body: emptyPage },
      "GET /api/v1/me/progress": { status: 200, body: progress() },
    });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByRole("heading", { name: "Welcome, Aidai" })).toBeInTheDocument();
    expect(screen.getByText(/Learning paths are not available yet/)).toBeInTheDocument();
    expect(await screen.findByText(/You have not submitted any solutions yet/)).toBeInTheDocument();
    expect(await screen.findByText(/No checked solutions yet/)).toBeInTheDocument();
  });

  it("dashboard lists recent attempts and only topics with judged evidence", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/submissions": {
        status: 200,
        body: {
          items: [
            {
              id: "00000000-0000-4000-8000-0000000000cc",
              problem_slug: "demo",
              problem_title: "Demo problem",
              language: "python",
              state: "not_judged",
              verdict: null,
              score: null,
              hints_revealed_count: 0,
              created_at: "2026-10-24T00:00:00Z",
            },
          ],
          total: 1,
          limit: 5,
          offset: 0,
        },
      },
      "GET /api/v1/me/progress": {
        status: 200,
        body: progress({
          pending_not_judged: 1,
          topics: [
            { slug: "sorting", name: "Sorting", stages: [{ stage: 1, unaided_by_band: { easy: 2 }, with_hints: 0, demonstrated: true }] },
            { slug: "greedy", name: "Greedy", stages: [{ stage: 1, unaided_by_band: {}, with_hints: 0, demonstrated: false }] },
          ],
        }),
      },
    });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByRole("link", { name: "Demo problem" })).toHaveAttribute(
      "href",
      "/submissions/00000000-0000-4000-8000-0000000000cc",
    );
    expect(screen.getByText("Not checked yet")).toBeInTheDocument();
    expect(await screen.findByText("Stages demonstrated: 1")).toBeInTheDocument();
    expect(screen.queryByText("Greedy")).not.toBeInTheDocument();
    expect(screen.getByText("Solutions waiting for checking: 1")).toBeInTheDocument();
  });

  it("dashboard requires sign-in", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<Dashboard />);
    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
  });

  it("privacy panel deletes only this device's code drafts", async () => {
    window.localStorage.setItem("ks:draft:a:cpp", "x");
    window.localStorage.setItem("ks:draft:b:python", "y");
    window.localStorage.setItem("unrelated", "keep");
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<PrivacyPanel />);
    expect(screen.getByText(/Drafts on this device: 2/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Delete drafts on this device" }));
    expect(screen.getByRole("status")).toHaveTextContent("Drafts deleted from this device.");
    expect(window.localStorage.getItem("ks:draft:a:cpp")).toBeNull();
    expect(window.localStorage.getItem("unrelated")).toBe("keep");
    expect(screen.getByRole("button", { name: "Delete drafts on this device" })).toBeDisabled();
    window.localStorage.clear();
  });
});

describe("navigation", () => {
  it("marks the current page and shows Admin only to admins", async () => {
    nav.pathname = "/topics";
    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser()) });
    const { unmount } = renderWithProviders(<SiteHeader />);
    expect(await screen.findByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Topics" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Problems" })).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
    unmount();

    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })) });
    render(
      <Providers locale="en">
        <SiteHeader />
      </Providers>,
    );
    expect(await screen.findByRole("link", { name: "Admin" })).toHaveAttribute("href", "/admin");
  });
});

describe("admin pages", () => {
  it("are forbidden to students (UI guard only; the API enforces)", async () => {
    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser()) });
    renderWithProviders(
      <AdminShell>
        <p>secret admin content</p>
      </AdminShell>,
    );
    expect(await screen.findByRole("heading", { name: "No access" })).toBeInTheDocument();
    expect(screen.queryByText("secret admin content")).not.toBeInTheDocument();
  });

  it("content queue asks for problems in review and links to the review page", async () => {
    nav.pathname = "/admin/problems";
    const fetchMock = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      "GET /api/v1/problems": {
        status: 200,
        body: {
          items: [
            {
              id: "00000000-0000-4000-8000-0000000000dd",
              slug: "sample-sum",
              title: "Sample Sum",
              locale: "en",
              status: "in_review",
              difficulty_band: "intro",
              difficulty_status: "author_estimate",
              topics: [],
              tags: [],
              origin: "original",
              license: "CC-BY-4.0",
              my_status: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
        },
      },
    });
    renderWithProviders(
      <AdminShell>
        <ContentQueue />
      </AdminShell>,
    );
    expect(await screen.findByRole("link", { name: "Sample Sum" })).toHaveAttribute("href", "/problems/sample-sum");
    expect(screen.getByRole("link", { name: "Content review" })).toHaveAttribute("aria-current", "page");
    const url = fetchMock.mock.calls.map(([r]) => (r as Request).url).find((u) => u.includes("/problems"));
    expect(url).toContain("status=in_review");
  });

  it("audit log labels actions and filters by action", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      "GET /api/v1/admin/audit-logs": {
        status: 200,
        body: {
          items: [
            {
              id: "00000000-0000-4000-8000-0000000000ee",
              actor_id: "12345678-0000-4000-8000-000000000000",
              action: "user.role_changed",
              target_type: "user",
              target_id: "abcdef12-0000-4000-8000-000000000000",
              details: { from: "student", to: "teacher" },
              created_at: "2026-10-24T10:00:00Z",
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
        },
      },
    });
    renderWithProviders(<AuditLog />);
    expect(await screen.findByText("Role changed", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("By user 12345678")).toBeInTheDocument();
    expect(screen.getByText("from: student · to: teacher")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Action"), "problem.published");
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([r]) => (r as Request).url.includes("action=problem.published")),
      ).toBe(true),
    );
  });
});
