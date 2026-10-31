import { expect, test, type APIRequestContext } from "@playwright/test";

// Phase 2 critical flow: browse → open → hint → submit (no judge) → history → progress.
// Needs the full stack with seeded users and imported content:
//   docker compose exec api python -m scripts.seed
//   docker compose exec api python -m scripts.import_content --author-unlinked
// E2E_ADMIN_PASSWORD must equal SEED_PASSWORD. The admin step publishes one problem the
// way a human reviewer would (and is audited like one): every statement, editorial and hint
// that exists is reviewed (the publication gate requires all of them), with the
// unlinked-author attestation: the synthetic admin is not the author of the imported content. This attests synthetic test data only; it proves nothing about identity.

const SLUG = "sample-sum";

async function signIn(request: APIRequestContext, email: string, password: string) {
  const res = await request.post("/api/v1/auth/login", { data: { email, password } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).csrf_token as string;
}

test.beforeAll(async ({ playwright, baseURL }) => {
  const password = process.env.E2E_ADMIN_PASSWORD;
  test.skip(!password, "E2E_ADMIN_PASSWORD not set");
  const admin = await playwright.request.newContext({ baseURL });
  const csrf = await signIn(admin, "admin@example.com", password!);
  const headers = { "X-CSRF-Token": csrf };
  const detail = await (await admin.get(`/api/v1/problems/${SLUG}`)).json();
  if (detail.status !== "published") {
    const rights = await admin.post(`/api/v1/admin/problems/${SLUG}/rights/verify`, {
      headers,
      data: { attest_not_author: true },
    });
    expect(rights.ok()).toBeTruthy();
    const attest = { headers, data: { attest_not_author: true } };
    const base = `/api/v1/admin/problems/${SLUG}`;
    for (const locale of detail.available_locales as string[]) {
      expect((await admin.post(`${base}/translations/${locale}/review`, { headers })).ok()).toBeTruthy();
    }
    for (const locale of detail.review.editorial_locales as string[]) {
      expect((await admin.post(`${base}/editorials/${locale}/review`, attest)).ok()).toBeTruthy();
    }
    for (const { locale } of detail.review.hint_review as { locale: string }[]) {
      expect((await admin.post(`${base}/hints/${locale}/review`, attest)).ok()).toBeTruthy();
    }
    await admin.patch(`/api/v1/admin/problems/${SLUG}/difficulty`, {
      headers,
      data: { band: "intro", rationale: "E2E reviewer confirmed: single-pass simulation." },
    });
    const published = await admin.post(`/api/v1/admin/problems/${SLUG}/publish`, { headers });
    expect(published.ok()).toBeTruthy();
  }
  await admin.dispose();
});

test("student practises a problem without a judge", async ({ page, baseURL }) => {
  const email = `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.com`;
  await page.context().addCookies([{ name: "ks_locale", value: "en", url: baseURL! }]);
  await page.goto("/register");
  await page.getByLabel("Name shown to others").fill("[Synthetic] E2E Student");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("e2e synthetic password");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard$/);

  await page.getByRole("link", { name: "Problems" }).click();
  await page.getByLabel("Search by title").fill("Sample");
  await page.getByRole("link", { name: "Sample Sum" }).click();
  await expect(page.getByRole("heading", { name: "Sample Sum" })).toBeVisible();
  await expect(page.getByText(/CC-BY-4.0/)).toBeVisible();

  await page.getByRole("button", { name: /Show hint 1 of/ }).click();
  await expect(page.getByText(/one variable with the running total/)).toBeVisible();

  // The header's interface-language switcher is also labelled "Language": use the problem's
  // own selector, inside the article named by the problem title.
  await page.getByRole("article", { name: "Sample Sum" }).getByLabel("Language").selectOption("python");
  await page.getByLabel("Your code").fill("print(input())");
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByRole("status")).toContainText("Automatic checking is not available yet.");

  await page.goto("/submissions");
  await expect(page.getByRole("link", { name: "Sample Sum" })).toBeVisible();
  await expect(page.getByText("Not checked yet")).toBeVisible();
  await expect(page.getByText("Hints opened: 1")).toBeVisible();

  await page.goto("/progress");
  await expect(page.getByText("Saved solutions waiting for checking: 1.")).toBeVisible();

  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByRole("link", { name: "Sample Sum" })).toBeVisible();
  await expect(page.getByText("Solutions waiting for checking: 1")).toBeVisible();
});
