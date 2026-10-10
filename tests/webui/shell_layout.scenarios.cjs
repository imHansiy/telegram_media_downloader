const assert = require("node:assert/strict");
const path = require("node:path");

// UI-only fixtures: no real media, accounts, favorites or history are touched.
exports.runShellLayout = async (page, project) => {
  const failures = [];
  const check = (condition, message) => {
    if (!condition) failures.push(message);
  };
  const libraryRoute = /\/api\/library\?/;
  const mediaRoute = /\/api\/webdav\/preview\?/;
  await page.route(libraryRoute, async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    const entry = data.files[0];
    await route.fulfill({
      json: {
        ...data,
        total: 6,
        tag_facets: [],
        files: Array.from({ length: 6 }, (_, index) => ({
          ...entry,
          path: `layout-check/${index}.mp4`,
          name:
            index % 2
              ? `用于验证多行标题不会撑高卡片的本地测试素材_${index}_版本信息与文件名.mp4`
              : `测试 ${index}.mp4`,
          analysis: {
            ...entry.analysis,
            subtype_label: index % 2 ? "三维与游戏动画" : "专业制作",
            status: index % 2 ? "legacy" : "complete",
          },
        })),
      },
    });
  });
  await page.route(mediaRoute, (route) =>
    route.fulfill({ status: 415, body: "Unavailable test cover" }),
  );
  await page.reload();
  await page.locator(".poster-card").first().waitFor();
  for (const width of [1440, 1024, 760, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const cards = await page.locator(".poster-card").evaluateAll((elements) =>
      elements.map((element) => {
        const rect = element.getBoundingClientRect();
        const title = element.querySelector("h2");
        const titleRect = title.getBoundingClientRect();
        return {
          width: rect.width,
          height: rect.height,
          titleHeight: titleRect.height,
          titleFont: parseFloat(getComputedStyle(title).fontSize),
        };
      }),
    );
    const heights = cards.map((card) => card.height);
    const widths = cards.map((card) => card.width);
    console.log(
      `Card geometry ${width}px: heights ${heights.map(Math.round).join(", ")}`,
    );
    check(
      Math.max(...heights) - Math.min(...heights) <= 1,
      `equal card heights for short/long titles at ${width}px`,
    );
    check(
      Math.max(...widths) - Math.min(...widths) <= 1,
      `equal card widths at ${width}px`,
    );
    check(
      cards.every((card) => card.titleFont >= 14),
      `readable card titles at ${width}px`,
    );
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  const readCardHeights = () =>
    page
      .locator(".poster-card")
      .evaluateAll((cards) =>
        cards.map((card) => card.getBoundingClientRect().height),
      );
  const unavailableHeights = await readCardHeights();
  await page.unroute(mediaRoute);
  await page.reload();
  await page
    .locator(".poster-card .media-cover img")
    .first()
    .waitFor({ timeout: 20000 });
  check(
    JSON.stringify(await readCardHeights()) ===
      JSON.stringify(unavailableHeights),
    "decoding a cover does not resize cards",
  );
  await page.getByRole("button", { name: "隐私模式", exact: true }).click();
  check(
    JSON.stringify(await readCardHeights()) ===
      JSON.stringify(unavailableHeights),
    "privacy placeholders do not resize cards",
  );
  await page.getByRole("button", { name: "隐私模式", exact: true }).click();
  await page
    .getByRole("button", { name: "切换为白天模式", exact: true })
    .click();
  await page.screenshot({
    path: path.join(project, ".cache/library-equal-cards.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "切换为夜间模式", exact: true })
    .click();
  await page.unroute(libraryRoute);
  await page.reload();
  const nav = page.getByRole("navigation", { name: "主导航" });
  const readShell = () =>
    page.locator(".app-header").evaluate((header) => {
      const rect = header.getBoundingClientRect();
      const content = document
        .getElementById("workspace")
        .getBoundingClientRect();
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        contentX: content.x,
        contentY: content.y,
        navigation: Array.from(
          header.querySelectorAll("nav button"),
          (button) => {
            const box = button.getBoundingClientRect();
            return [box.x, box.y, box.width, box.height];
          },
        ),
      };
    });
  for (const width of [1440, 1024, 760, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const baseline = await readShell();
    for (const [label, activeClass, pathname] of [
      ["传输任务", "page-dashboard", "/tasks"],
      ["账号", "page-accounts", "/tg_login"],
      ["设置", "page-config", "/config"],
      ["资源库", "page-files", "/files"],
    ]) {
      await nav.getByRole("button", { name: label, exact: true }).click();
      await page.locator("#workspace." + activeClass).waitFor();
      const layout = await readShell();
      check(
        JSON.stringify(layout) === JSON.stringify(baseline),
        `navigation and workspace do not move on ${label} at ${width}px`,
      );
      check(
        new URL(page.url()).pathname === pathname,
        `one click opens ${label} directly`,
      );
      check(
        await page.evaluate(
          () =>
            document.documentElement.scrollWidth <= innerWidth &&
            document.documentElement.scrollHeight <= innerHeight,
        ),
        `contained workspace for ${label} at ${width}px`,
      );
      if (width === 1440)
        console.log(
          `Navigation ${label}: ${Math.round(layout.width)}×${Math.round(layout.height)} at (${layout.x}, ${layout.y})`,
        );
      if (width === 1440) {
        await page.reload();
        await page.locator("#workspace." + activeClass).waitFor();
        check(
          JSON.stringify(await readShell()) === JSON.stringify(baseline),
          `direct load keeps the same navigation for ${label}`,
        );
      }
      if (label === "传输任务" && [1440, 390].includes(width))
        await page.screenshot({
          path: path.join(project, `.cache/persistent-navigation-${width}.png`),
          fullPage: true,
        });
    }
    await page.goBack();
    await page.locator("#workspace.page-config").waitFor();
    check(
      JSON.stringify(await readShell()) === JSON.stringify(baseline),
      `back navigation keeps the shell at ${width}px`,
    );
    await page.goForward();
    await page.locator("#workspace.page-files").waitFor();
    check(
      JSON.stringify(await readShell()) === JSON.stringify(baseline),
      `forward navigation keeps the shell at ${width}px`,
    );
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  assert.deepEqual(
    failures,
    [],
    "card geometry and persistent navigation regression",
  );
};
