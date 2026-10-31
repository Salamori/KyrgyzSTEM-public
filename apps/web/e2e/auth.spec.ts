import { expect, test } from "@playwright/test";

// Critical flow: register → dashboard → profile → sign out → protected page → sign-in.
// Needs the full stack (docker compose up) with a migrated database.

test("student account lifecycle", async ({ page, baseURL }) => {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  const password = "e2e synthetic password";

  await page.context().addCookies([{ name: "ks_locale", value: "en", url: baseURL! }]);
  await page.goto("/register");
  await page.getByLabel("Name shown to others").fill("[Synthetic] E2E Student");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole("heading", { name: "Welcome, [Synthetic] E2E Student" })).toBeVisible();
  await page.getByRole("link", { name: "Profile" }).click();
  await expect(page.getByRole("heading", { name: "My profile" })).toBeVisible();
  await expect(page.getByText("Student", { exact: true })).toBeVisible();

  // Session cookie must not be readable from JavaScript.
  const visible = await page.evaluate(() => document.cookie);
  expect(visible).not.toContain("ks_session");

  await page.getByRole("button", { name: "Sign out" }).click();
  await page.goto("/profile");
  await expect(page.getByRole("heading", { name: "Sign in required" })).toBeVisible();

  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("wrong password!!");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Email or password is incorrect.");

  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);
});

test("students cannot open admin pages", async ({ page, baseURL }) => {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.context().addCookies([{ name: "ks_locale", value: "en", url: baseURL! }]);
  await page.goto("/register");
  await page.getByLabel("Name shown to others").fill("[Synthetic] E2E Student");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e synthetic password");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await expect(page.getByRole("link", { name: "Admin" })).toHaveCount(0);
  for (const path of ["/admin/users", "/admin/problems", "/admin/audit"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "No access" })).toBeVisible();
  }
  // The UI guard is cosmetic; the API is the boundary.
  expect((await page.request.get("/api/v1/admin/audit-logs")).status()).toBe(403);
  const res = await page.request.get("/api/v1/admin/users");
  expect(res.status()).toBe(403);
});
