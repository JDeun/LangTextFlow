import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("langtextflow:onboarding:v1", "complete");
  });
});

test("operator can run a complete mock caption session", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByText("LangTextFlow", { exact: true })).toBeVisible();
  await expect(page.getByText("서버 연결됨")).toBeVisible({ timeout: 10_000 });

  await page.getByLabel("음성 인식 엔진").selectOption("mock");
  await expect(page.getByLabel("번역 엔진")).toHaveValue("demo");

  await page.getByRole("button", { name: "세션 시작" }).click();
  await expect(page.getByRole("button", { name: "자막 중지" })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/오늘 우리가 볼 말씀/).first()).toBeVisible({ timeout: 10_000 });

  await page.getByRole("button", { name: "자막 중지" }).click();
  await expect(page.getByRole("button", { name: "세션 시작" })).toBeVisible({ timeout: 10_000 });
});
