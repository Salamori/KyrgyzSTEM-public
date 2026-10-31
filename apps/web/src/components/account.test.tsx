import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { en } from "@/lib/i18n/en";
import { ky } from "@/lib/i18n/ky";
import { ru } from "@/lib/i18n/ru";
import { anonymous, makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { AccountActions } from "./AccountActions";
import { ResetPasswordForm } from "./AuthForms";
import { LegalPage } from "./LegalPage";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const signedIn = sessionFor(makeUser());

describe("ResetPasswordForm", () => {
  afterEach(() => window.history.replaceState(null, "", "/"));

  it("explains how to get a link when there is no token", async () => {
    mockApi({});
    renderWithProviders(<ResetPasswordForm />);
    expect(await screen.findByText(/We do not send email yet/)).toBeInTheDocument();
    expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
  });

  it("takes the token from the fragment, removes it from the URL and sends it", async () => {
    window.history.replaceState(null, "", "/reset-password#token=abc");
    const fetchMock = mockApi({ "POST /api/v1/auth/password/reset": { status: 204 } });
    renderWithProviders(<ResetPasswordForm />);

    await userEvent.type(await screen.findByLabelText("New password"), "a brand new password");
    expect(window.location.hash).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Set new password" }));

    expect(await screen.findByText(/Password changed/)).toBeInTheDocument();
    const request = fetchMock.mock.calls
      .map(([r]) => r as Request)
      .find((r) => r.url.endsWith("/auth/password/reset"));
    expect(await request?.clone().json()).toEqual({ token: "abc", new_password: "a brand new password" });
  });

  it("shows the invalid link error", async () => {
    window.history.replaceState(null, "", "/reset-password#token=old");
    mockApi({
      "POST /api/v1/auth/password/reset": {
        status: 400,
        body: { error: { code: "invalid_reset_token", message: "x", details: null } },
      },
    });
    renderWithProviders(<ResetPasswordForm />);
    await userEvent.type(await screen.findByLabelText("New password"), "a brand new password");
    await userEvent.click(screen.getByRole("button", { name: "Set new password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is invalid or has expired.");
  });
});

describe("AccountActions", () => {
  beforeEach(() => window.localStorage.setItem("ks:draft:demo:python", "print(1)"));
  afterEach(() => window.localStorage.clear());

  it("deleting the account sends the password and clears drafts on this device", async () => {
    let session = signedIn;
    const fetchMock = mockApi({
      "GET /api/v1/auth/session": () => session,
      "POST /api/v1/users/me/delete": () => {
        session = anonymous;
        return { status: 204 };
      },
    });
    renderWithProviders(<AccountActions />);

    await userEvent.type(await screen.findByLabelText("Password, to confirm"), "my password here");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Delete my account" }));

    await waitFor(() => expect(window.localStorage.getItem("ks:draft:demo:python")).toBeNull());
    const request = fetchMock.mock.calls
      .map(([r]) => r as Request)
      .find((r) => r.url.endsWith("/users/me/delete"));
    expect(await request?.clone().json()).toEqual({ password: "my password here" });
  });

  it("shows the error and keeps drafts when deletion is refused", async () => {
    mockApi({
      "GET /api/v1/auth/session": signedIn,
      "POST /api/v1/users/me/delete": {
        status: 401,
        body: { error: { code: "invalid_credentials", message: "x", details: null } },
      },
    });
    renderWithProviders(<AccountActions />);
    await userEvent.type(await screen.findByLabelText("Password, to confirm"), "wrong");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Delete my account" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(window.localStorage.getItem("ks:draft:demo:python")).toBe("print(1)");
  });
});

describe("legal pages", () => {
  it("always show that the text is an unreviewed draft, and mark open decisions", () => {
    mockApi({});
    renderWithProviders(<LegalPage doc="privacy" sections={[{ id: "operator", needsDecision: true }]} />);
    expect(screen.getByRole("note")).toHaveTextContent("has not been reviewed by a lawyer");
    expect(screen.getByText(/name the legal entity/)).toBeInTheDocument();
  });

  it("state no invented age, address or retention period in any language", () => {
    for (const dictionary of [en, ru, ky]) {
      for (const [key, value] of Object.entries(dictionary)) {
        if (!key.startsWith("legal.")) continue;
        expect(value, key).not.toMatch(/\d/);
        expect(value, key).not.toMatch(/@/);
      }
    }
  });
});
