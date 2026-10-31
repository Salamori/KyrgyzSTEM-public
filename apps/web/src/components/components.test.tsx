import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { anonymous, makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { AdminUsers } from "./AdminUsers";
import { LoginForm, RegisterForm } from "./AuthForms";
import { ProfileView } from "./ProfileView";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

describe("LoginForm", () => {
  it("shows a localized error and stays on the page for bad credentials", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "POST /api/v1/auth/login": {
        status: 401,
        body: { error: { code: "invalid_credentials", message: "server text", details: null } },
      },
    });
    renderWithProviders(<LoginForm />, "ru");

    await userEvent.type(await screen.findByLabelText("Электронная почта"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Пароль"), "wrong password");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Неверная электронная почта или пароль.",
    );
    expect(screen.queryByText("server text")).not.toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("signs in, applies the user's preferred locale and redirects", async () => {
    const user = makeUser({ preferred_locale: "ky" });
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "POST /api/v1/auth/login": sessionFor(user),
    });
    renderWithProviders(<LoginForm />, "en");

    await userEvent.type(await screen.findByLabelText("Email"), user.email);
    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/dashboard"));
    expect(document.documentElement.lang).toBe("ky");
    expect(document.cookie).toContain("ks_locale=ky");
  });
});

describe("RegisterForm", () => {
  it("shows the duplicate-email error", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "POST /api/v1/auth/register": {
        status: 409,
        body: { error: { code: "email_taken", message: "x", details: null } },
      },
    });
    renderWithProviders(<RegisterForm />);

    await userEvent.type(await screen.findByLabelText("Name shown to others"), "Aidai");
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "long enough password");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "An account with this email already exists.",
    );
  });

  it("does not register without consent, and links the documents", async () => {
    const fetchMock = mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<RegisterForm />);

    await userEvent.type(await screen.findByLabelText("Name shown to others"), "A");
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "long enough password");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByText("Accept the terms to create an account.")).toBeInTheDocument();
    const posted = fetchMock.mock.calls.some(([request]) => (request as Request).method === "POST");
    expect(posted).toBe(false);
    expect(screen.getByRole("link", { name: "Terms of Service" })).toHaveAttribute("href", "/terms");
    expect(screen.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", "/privacy");
    expect(screen.getByRole("link", { name: "Students who are minors" })).toHaveAttribute("href", "/minors");
  });

  it("marks fields reported invalid by the server", async () => {
    mockApi({
      "GET /api/v1/auth/session": anonymous,
      "POST /api/v1/auth/register": {
        status: 422,
        body: {
          error: {
            code: "validation_error",
            message: "x",
            details: [{ loc: ["body", "password"], msg: "short", type: "string_too_short" }],
          },
        },
      },
    });
    renderWithProviders(<RegisterForm />);
    await userEvent.type(await screen.findByLabelText("Name shown to others"), "A");
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "short");
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    await waitFor(() =>
      expect(screen.getByLabelText("Password")).toHaveAttribute("aria-invalid", "true"),
    );
    expect(screen.getByLabelText("Email")).not.toHaveAttribute("aria-invalid");
  });
});

describe("ProfileView", () => {
  it("asks anonymous visitors to sign in", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<ProfileView />);
    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
  });

  it("shows a retryable error when the session check fails", async () => {
    mockApi({
      "GET /api/v1/auth/session": {
        status: 500,
        body: { error: { code: "error", message: "boom", details: null } },
      },
    });
    renderWithProviders(<ProfileView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("saves the display name and confirms success", async () => {
    const user = makeUser();
    mockApi({
      "GET /api/v1/auth/session": sessionFor(user),
      "PATCH /api/v1/users/me": { status: 200, body: { ...user, display_name: "New name" } },
    });
    renderWithProviders(<ProfileView />);
    const name = await screen.findByLabelText("Name shown to others");
    await userEvent.clear(name);
    await userEvent.type(name, "New name");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
  });
});

describe("AdminUsers", () => {
  it("is forbidden for students", async () => {
    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser()) });
    renderWithProviders(<AdminUsers />);
    expect(await screen.findByRole("heading", { name: "No access" })).toBeInTheDocument();
  });

  it("requires sign-in for anonymous visitors", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<AdminUsers />);
    expect(await screen.findByRole("heading", { name: "Sign in required" })).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "admin" })),
      "GET /api/v1/admin/users": {
        status: 200,
        body: { items: [], total: 0, limit: 20, offset: 0 },
      },
    });
    renderWithProviders(<AdminUsers />);
    expect(await screen.findByText("No users match your filters.")).toBeInTheDocument();
  });

  it("lists users and prevents editing yourself", async () => {
    const admin = makeUser({ role: "admin", display_name: "Me" });
    const other = {
      ...makeUser({ id: "00000000-0000-4000-8000-000000000002", display_name: "Other" }),
      is_active: true,
      is_synthetic: true,
    };
    mockApi({
      "GET /api/v1/auth/session": sessionFor(admin),
      "GET /api/v1/admin/users": {
        status: 200,
        body: {
          items: [{ ...admin, is_active: true, is_synthetic: false }, other],
          total: 2,
          limit: 20,
          offset: 0,
        },
      },
    });
    renderWithProviders(<AdminUsers />);
    expect(await screen.findByLabelText("Change role for Me")).toBeDisabled();
    expect(screen.getByLabelText("Change role for Other")).toBeEnabled();
    expect(screen.getByText("Synthetic")).toBeInTheDocument();
    expect(screen.getByText("1–2 of 2")).toBeInTheDocument();
  });
});
