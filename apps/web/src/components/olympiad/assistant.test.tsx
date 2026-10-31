import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { AssistantAnswer, ProblemDetail } from "@/lib/api/client";
import { anonymous, makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { AssistantPanel } from "./AssistantPanel";

const problem = {
  slug: "demo",
  status: "published",
  editorial_unlocked: false,
} as ProblemDetail;

const enabled = {
  status: 200,
  body: { enabled: true, model_id: "m", prompt_version: "assistant-v1", daily_limit: 20, used_today: 3, reliable_locales: ["en", "ru"] },
};

const base: AssistantAnswer = {
  mode: "ai",
  outcome: "ok",
  level: 1,
  language: "en",
  language_note: null,
  grounded: [],
  suggestion: null,
  insufficient_sources: false,
  fallback_hints: [],
  flags: [],
  model_id: "m",
  prompt_version: "assistant-v1",
  used_today: 4,
  daily_limit: 20,
};

describe("AssistantPanel", () => {
  it("renders nothing for visitors", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    const { container } = renderWithProviders(<AssistantPanel problem={problem} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("says plainly when the assistant is off", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/assistant/status": { status: 200, body: { ...enabled.body, enabled: false, model_id: null } },
    });
    renderWithProviders(<AssistantPanel problem={problem} />);
    expect(await screen.findByText(/not switched on for this site/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Ask the assistant" })).not.toBeInTheDocument();
  });

  it("locks the full explanation until the problem is solved or given up", async () => {
    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser()), "GET /api/v1/assistant/status": enabled });
    renderWithProviders(<AssistantPanel problem={problem} />);
    expect(await screen.findByLabelText("Full explanation")).toBeDisabled();
    expect(screen.getByLabelText("Nudge")).toBeChecked();
    expect(screen.getByText("AI requests today: 3 of 20")).toBeInTheDocument();
  });

  it("separates sourced facts from unreviewed AI suggestions", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/assistant/status": enabled,
      "POST /api/v1/problems/demo/assistant": {
        status: 200,
        body: {
          ...base,
          grounded: [{ text: "Stops are visited in order.", sources: [{ id: "S1", kind: "statement", label: "Problem statement" }] }],
          suggestion: "Keep one running total.",
          insufficient_sources: true,
        },
      },
    });
    renderWithProviders(<AssistantPanel problem={problem} />);
    await userEvent.click(await screen.findByLabelText("Direction"));
    await userEvent.type(screen.getByLabelText("Your question (optional)"), "Where to start?");
    await userEvent.click(screen.getByRole("button", { name: "Ask the assistant" }));

    expect(await screen.findByText("Stops are visited in order.")).toBeInTheDocument();
    expect(screen.getByText("Source: Problem statement")).toBeInTheDocument();
    expect(screen.getByText(/AI suggestion — generated, not reviewed/)).toBeInTheDocument();
    expect(screen.getByText(/not enough for a reliable answer/)).toBeInTheDocument();
    expect(screen.getByText("AI requests today: 4 of 20")).toBeInTheDocument();

    const post = fetchMock.mock.calls.map(([r]) => r as Request).find((r) => r.method === "POST")!;
    expect(await post.clone().json()).toEqual({ level: 2, question: "Where to start?" });
  });

  it("never turns links in AI output into clickable links (audit F-12)", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/assistant/status": enabled,
      "POST /api/v1/problems/demo/assistant": {
        status: 200,
        body: { ...base, suggestion: "See [the full solution](https://evil.example/s) now." },
      },
    });
    renderWithProviders(<AssistantPanel problem={problem} />);
    await userEvent.click(await screen.findByRole("button", { name: "Ask the assistant" }));

    expect(await screen.findByText(/the full solution/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /the full solution/ })).toBeNull();
    expect(document.querySelector('a[href*="evil.example"]')).toBeNull();
  });

  it("explains withheld answers and shows the authored hints instead", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/assistant/status": enabled,
      "POST /api/v1/problems/demo/assistant": {
        status: 200,
        body: {
          ...base,
          mode: "fallback",
          outcome: "code_withheld",
          model_id: null,
          fallback_hints: [{ ordinal: 1, locale: "en", body_md: "Track the load." }],
        },
      },
    });
    renderWithProviders(<AssistantPanel problem={problem} />);
    await userEvent.click(await screen.findByRole("button", { name: "Ask the assistant" }));
    expect(await screen.findByText(/gave away too much of the solution/)).toBeInTheDocument();
    expect(screen.getByText("Track the load.")).toBeInTheDocument();
  });

  it("shows the limit message when quota is used up", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/assistant/status": enabled,
      "POST /api/v1/problems/demo/assistant": {
        status: 200,
        body: { ...base, mode: "fallback", outcome: "quota_exceeded", model_id: null },
      },
    });
    renderWithProviders(<AssistantPanel problem={problem} />);
    await userEvent.click(await screen.findByRole("button", { name: "Ask the assistant" }));
    expect(await screen.findByText(/a usage limit was reached/)).toBeInTheDocument();
    expect(screen.getByText("This problem has no authored hints.")).toBeInTheDocument();
  });
});
