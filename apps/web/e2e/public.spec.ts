import { expect, test } from "@playwright/test";

// Acceptance criterion: a new (anonymous) user can navigate the public site.
// Needs the full stack with imported curriculum (scripts.import_content).

test.beforeEach(async ({ page, baseURL }) => {
  await page.context().addCookies([{ name: "ks_locale", value: "en", url: baseURL! }]);
});

test("visitor navigates landing → about → topics → filtered catalogue → paths", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByText(/free and open source/)).toBeVisible();

  await page.getByRole("link", { name: "How the platform works" }).click();
  await expect(page.getByRole("heading", { name: "About KyrgyzSTEM" })).toBeVisible();
  await expect(page.getByRole("link", { name: "About" }).first()).toHaveAttribute("aria-current", "page");

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Topics" }).click();
  await expect(page.getByRole("heading", { name: "Prefix sums" })).toBeVisible();
  const prefix = page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: "Prefix sums" }) });
  await prefix.getByRole("link", { name: /Problems in this topic/ }).click();

  await expect(page).toHaveURL(/\/problems\?topic=prefix-sums$/);
  await expect(page.getByLabel("Topic")).toHaveValue("prefix-sums");

  await page.goto("/learning-paths");
  await expect(page.getByText(/Learning paths are being prepared/)).toBeVisible();
});

test("keyboard users can skip to the main content", async ({ page }) => {
  await page.goto("/about");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to main content" });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#main$/);
});

test("private pages ask visitors to sign in", async ({ page }) => {
  for (const path of ["/dashboard", "/submissions", "/progress", "/profile"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Sign in required" })).toBeVisible();
  }
});
