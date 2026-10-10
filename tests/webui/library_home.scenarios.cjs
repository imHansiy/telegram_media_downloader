const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

exports.createLibraryFixture = ({ taxonomy, resource, project }) => {
  const fixtures = path.join(project, ".cache/library-browser-fixtures");
  fs.mkdirSync(fixtures, { recursive: true });
  const videoPath = path.join(fixtures, "color-preview.webm");
  if (!fs.existsSync(videoPath))
    execFileSync(
      "ffmpeg",
      [
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=480x270:rate=10",
        "-t",
        "4",
        "-c:v",
        "libvpx-vp9",
        "-an",
        videoPath,
      ],
      { windowsHide: true, timeout: 30000 },
    );
  const video = fs.readFileSync(videoPath);
  const cover = execFileSync(
    "ffmpeg",
    [
      "-nostdin",
      "-v",
      "error",
      "-ss",
      "0.4",
      "-i",
      videoPath,
      "-frames:v",
      "1",
      "-f",
      "image2pipe",
      "-c:v",
      "mjpeg",
      "pipe:1",
    ],
    { windowsHide: true, timeout: 30000 },
  );
  const main = resource("三维动画示例.mp4", "adult", "video", "animation_3d");
  main.analysis.tags = ["室内", "固定机位"];
  const second = resource("摄影棚示例.mp4", "adult", "video", "studio");
  second.analysis.tags = ["室内", "多机位"];
  const entries = [
    main,
    second,
    resource("电影示例.mp4", "movie"),
    resource("Python 入门.mp4", "education"),
    resource("音乐演出.mp4", "music"),
    resource("参考文档.pdf", "document", "document"),
    ...Array.from({ length: 14 }, (_, index) =>
      resource(
        `本地测试 ${String(index).padStart(2, "0")}.mp4`,
        "adult",
        "video",
        "studio",
      ),
    ),
  ];
  const records = {};
  const events = [];
  const deletePlans = new Map();
  let deleteCalls = 0;
  let paused = false,
    failFavorite = false,
    mediaRequests = 0,
    coverRequests = 0,
    videoRequests = 0,
    analyzed = "",
    analysisMode = "",
    polls = 0;
  const matchScope = (entry, scope) =>
    !scope ||
    scope === "all" ||
    (scope === "other"
      ? !["adult", "movie"].includes(entry.analysis.category)
      : scope === entry.analysis.category);
  function handle(req, res, url, json, readBody) {
    if (url.pathname === "/api/library/delete/preview") {
      readBody((body) => {
        const token = `delete-${deletePlans.size + 1}`;
        const targets = ["remote", "local"].flatMap((storage) =>
          [body.path, body.path + ".cover.jpg"].map((name, index) => ({
            id: `${storage}_${index}`,
            label:
              (storage === "remote" ? "云盘" : "本地") +
              (index ? "封面" : "资源"),
            path: (storage === "remote" ? "/资源库/" : "D:/downloads/") + name,
            storage,
            exists: true,
          })),
        );
        deletePlans.set(token, { path: body.path, targets });
        json({
          success: true,
          token,
          targets,
          recycle_path: "D:/downloads/.resource-trash/fixture",
        });
      });
      return true;
    }
    if (url.pathname === "/api/library/delete") {
      readBody((body) => {
        deleteCalls++;
        const plan = deletePlans.get(body.token);
        if (!plan || body.confirmed !== true) {
          json({ success: false, message: "确认无效" }, 400);
          return;
        }
        const fail = deleteCalls === 1;
        setTimeout(() => {
          if (!fail) {
            const index = entries.findIndex(
              (entry) => entry.path === plan.path,
            );
            if (index >= 0) entries.splice(index, 1);
            delete records[plan.path];
          }
          json(
            {
              success: !fail,
              message: fail ? "模拟云盘封面删除失败" : "资源及记录已删除",
              results: plan.targets.map((target, index) => ({
                ...target,
                status: fail
                  ? index === 0
                    ? "deleted"
                    : "pending"
                  : target.storage === "local"
                    ? "recycled"
                    : "deleted",
                destination:
                  !fail && target.storage === "local"
                    ? "D:/downloads/.resource-trash/fixture/" + plan.path
                    : "",
              })),
            },
            fail ? 409 : 200,
          );
        }, 600);
      });
      return true;
    }
    if (url.pathname === "/api/library/taxonomy") {
      json(taxonomy);
      return true;
    }
    if (url.pathname === "/api/library") {
      const args = url.searchParams;
      const view = args.get("view");
      let matches = entries.filter(
        (entry) =>
          matchScope(entry, args.get("scope")) &&
          ["category", "subtype"].every(
            (key) => !args.get(key) || entry.analysis[key] === args.get(key),
          ) &&
          (!args.get("kind") ||
            entry.analysis.resource_kind === args.get("kind")) &&
          (!args.get("status") ||
            (args.get("status") === "review"
              ? entry.analysis.status !== "complete"
              : entry.analysis.status === args.get("status"))) &&
          (!args.get("q") ||
            [entry.name, entry.analysis.summary, ...entry.analysis.tags]
              .join(" ")
              .toLowerCase()
              .includes(args.get("q").toLowerCase())) &&
          (view !== "favorites" || records[entry.path]?.favorite) &&
          (view !== "history" ||
            records[entry.path]?.[
              (args.get("history_kind") || "opened") + "_at"
            ]),
      );
      const tagCounts = {};
      for (const entry of matches)
        for (const tag of entry.analysis.tags)
          tagCounts[tag] = (tagCounts[tag] || 0) + 1;
      matches = matches.filter((entry) =>
        args.getAll("tag").every((tag) => entry.analysis.tags.includes(tag)),
      );
      const files = matches.map((entry) => ({
        ...entry,
        activity: { ...records[entry.path] },
      }));
      const response = {
        files,
        root: "资源库",
        scope_id: "fixture",
        total: files.length,
        indexed_total: entries.length,
        page: 1,
        pages: 1,
        paused,
        counts: {},
        tag_facets: Object.entries(tagCounts)
          .filter(([name]) => name.includes(args.get("tag_q") || ""))
          .map(([name, count]) => ({ name, count })),
      };
      if (args.get("q") === "慢") setTimeout(() => json(response), 400);
      else json(response);
      return true;
    }
    if (url.pathname === "/api/library/activity") {
      if (req.method === "GET") {
        json({ paused });
        return true;
      }
      readBody((body) => {
        events.push(body);
        if (body.action === "favorite" && failFavorite) {
          failFavorite = false;
          json({ success: false, message: "模拟收藏保存失败" }, 503);
          return;
        }
        const now = new Date().toISOString();
        const record = (records[body.path] ||= {});
        if (body.action === "favorite")
          Object.assign(record, { favorite: body.value, favorited_at: now });
        if (body.action === "pause") paused = body.value;
        if (body.action === "open" && !paused) record.opened_at = now;
        if (body.action === "progress" && !paused)
          Object.assign(record, {
            watched_at: now,
            position: body.position,
            duration: body.duration,
            completed: body.position >= body.duration * 0.95,
          });
        if (["clear", "remove"].includes(body.action)) {
          for (const entry of entries) {
            if (
              !matchScope(entry, body.scope) ||
              (body.action === "remove" && entry.path !== body.path)
            )
              continue;
            const target = records[entry.path] || {};
            if (["opened", "all"].includes(body.history_kind))
              delete target.opened_at;
            if (["watched", "all"].includes(body.history_kind))
              for (const key of [
                "watched_at",
                "position",
                "duration",
                "completed",
              ])
                delete target[key];
          }
        }
        json({ paused, activity: records[body.path] || {} });
      });
      return true;
    }
    if (url.pathname === "/api/webdav/preview") {
      mediaRequests++;
      if (url.searchParams.get("path")?.endsWith(".cover.jpg")) {
        coverRequests++;
        res.writeHead(200, {
          "Content-Type": "image/jpeg",
          "Content-Length": cover.length,
          "Cache-Control": "private, max-age=3600",
        });
        res.end(req.method === "HEAD" ? undefined : cover);
        return true;
      }
      videoRequests++;
      const match = /bytes=(\d+)-(\d*)/.exec(req.headers.range || "");
      const from = match ? Number(match[1]) : 0;
      const to = Math.min(
        match?.[2] ? Number(match[2]) : video.length - 1,
        video.length - 1,
      );
      res.writeHead(match ? 206 : 200, {
        "Content-Type": "video/webm",
        "Accept-Ranges": "bytes",
        "Content-Length": to - from + 1,
        ...(match
          ? { "Content-Range": `bytes ${from}-${to}/${video.length}` }
          : {}),
      });
      res.end(req.method === "HEAD" ? undefined : video.subarray(from, to + 1));
      return true;
    }
    if (url.pathname === "/api/webdav/browse") {
      json({
        root: "资源库",
        folders: url.searchParams.get("path")
          ? []
          : [{ name: "来源目录", path: "来源目录" }],
        files: url.searchParams.get("path") ? entries : [],
      });
      return true;
    }
    if (url.pathname === "/api/webdav/summary") {
      const entry = entries.find(
        (item) => item.path === url.searchParams.get("path"),
      );
      const job =
        analyzed === entry.path
          ? { status: ++polls < 2 ? "running" : "done", message: "分析完成" }
          : {};
      json({ analysis: entry.analysis, job });
      return true;
    }
    if (url.pathname === "/api/library/analyze") {
      readBody((body) => {
        analyzed = body.path;
        analysisMode = body.mode || "full";
        polls = 0;
        json({ job: { status: "queued" } }, 202);
      });
      return true;
    }
    return false;
  }

  async function runLayout(page) {
    await page.locator(".poster-card").first().waitFor();
    for (const width of [1440, 1920, 1024, 760, 390, 320]) {
      await page.setViewportSize({ width, height: width < 800 ? 844 : 900 });
      const layout = await page.evaluate(() => {
        const card = document.querySelector(".poster-card");
        const box = card.getBoundingClientRect();
        const content = document
          .querySelector(".cinema-scroll")
          .getBoundingClientRect();
        return {
          top: box.top,
          contentHeight: content.height,
          height: innerHeight,
          contained:
            document.documentElement.scrollWidth <= innerWidth &&
            document.documentElement.scrollHeight <= innerHeight,
          titleSize: parseFloat(
            getComputedStyle(card.querySelector("h2")).fontSize,
          ),
        };
      });
      console.log(
        `Library density ${width}px: first card ${layout.top}px, content ${Math.round((layout.contentHeight / layout.height) * 100)}%`,
      );
      assert.ok(
        layout.top <= (width < 800 ? 245 : 180),
        "resource covers must enter the first quarter of the viewport at " +
          width,
      );
      assert.ok(
        layout.contained,
        "library must stay inside the viewport at " + width,
      );
      assert.ok(
        layout.titleSize >= 14,
        "content text must not be shrunk to reclaim space",
      );
      assert.equal(
        await page.locator(".cinema-footer").count(),
        0,
        "single-page results must not reserve a footer",
      );
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: path.join(project, ".cache/library-density-desktop.png"),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "切换为白天模式", exact: true })
      .click();
    await page.screenshot({
      path: path.join(project, ".cache/library-density-light.png"),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "切换为夜间模式", exact: true })
      .click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(project, ".cache/library-density-mobile.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    assert.equal(
      await page.getByRole("combobox").count(),
      0,
      "low-frequency controls stay out of the home toolbar",
    );
    const filterButton = page.getByRole("button", {
      name: "筛选与排序",
      exact: true,
    });
    await filterButton.click();
    const filters = page.getByRole("dialog", {
      name: "筛选与排序",
      exact: true,
    });
    await filters.getByLabel("资源类型", { exact: true }).selectOption("video");
    await filters
      .getByLabel("分析状态", { exact: true })
      .selectOption("complete");
    await filters.getByLabel("排序方式", { exact: true }).selectOption("size");
    await filters.getByLabel("搜索标签", { exact: true }).fill("室内");
    await filters
      .locator(".library-tag-picker")
      .getByRole("button", { name: /^室内/ })
      .click();
    await filters
      .getByRole("button", { name: "查看 2 项资源", exact: true })
      .click();
    assert.equal(await filterButton.locator(".filter-badge").innerText(), "2");
    await filterButton.click();
    assert.equal(
      await filters.getByLabel("资源类型", { exact: true }).inputValue(),
      "video",
    );
    assert.equal(
      await filters.getByLabel("排序方式", { exact: true }).inputValue(),
      "size",
    );
    await page.keyboard.press("Escape");
    assert.ok(
      await filterButton.evaluate(
        (button) => button === document.activeElement,
      ),
      "filter dialog restores keyboard focus",
    );
    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelectorAll(".poster-card").length > 2,
    );

    // Reproduce the screenshot's sparse library: two resources, no tags, no decodable covers.
    const sparseRoute = /\/api\/library\?/;
    const unavailableMedia = /\/api\/webdav\/preview\?/;
    await page.route(sparseRoute, async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      await route.fulfill({
        json: {
          ...data,
          scope_id: "fixture-sparse",
          total: 2,
          files: data.files.slice(0, 2),
          tag_facets: [],
        },
      });
    });
    await page.route(unavailableMedia, (route) =>
      route.fulfill({ status: 415, body: "Unsupported fixture media" }),
    );
    await page.reload();
    await page.getByText("暂无已保存封面", { exact: true }).first().waitFor();
    assert.equal(
      await page.locator(".filter-options.tags").count(),
      0,
      "no empty tag row",
    );
    assert.equal(
      await page.locator(".cinema-footer").count(),
      0,
      "no redundant 1/1 footer",
    );
    const sparseTop = (await page.locator(".poster-card").first().boundingBox())
      .y;
    assert.ok(
      sparseTop <= 120,
      "sparse library starts immediately after categories",
    );
    assert.ok(
      (await page.locator(".poster-copy").first().boundingBox()).y < 400,
      "missing covers must not push titles below the fold",
    );
    console.log(
      `Library sparse layout: first card ${sparseTop}px, no empty tag or pagination rows`,
    );
    await page
      .getByRole("button", { name: "切换为白天模式", exact: true })
      .click();
    await page.screenshot({
      path: path.join(project, ".cache/library-density-sparse-light.png"),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "切换为夜间模式", exact: true })
      .click();
    await page.unroute(sparseRoute);
    await page.unroute(unavailableMedia);
    await page.route(sparseRoute, async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      const currentPage = Number(
        new URL(route.request().url()).searchParams.get("page") || 1,
      );
      await route.fulfill({
        json: {
          ...data,
          total: 4,
          page: currentPage,
          pages: 2,
          files: data.files.slice((currentPage - 1) * 2, currentPage * 2),
        },
      });
    });
    await page.reload();
    await page.locator(".cinema-footer").waitFor();
    await page.getByRole("button", { name: "下一页资源", exact: true }).click();
    await page
      .locator(".cinema-footer")
      .getByText("2 / 2", { exact: true })
      .waitFor();
    assert.ok(
      await page
        .getByRole("button", { name: "下一页资源", exact: true })
        .isDisabled(),
    );
    await page.getByRole("button", { name: "上一页资源", exact: true }).click();
    await page
      .locator(".cinema-footer")
      .getByText("1 / 2", { exact: true })
      .waitFor();
    await page.unroute(sparseRoute);
    await page.reload();
    await page.locator(".poster-card").first().waitFor();
  }

  async function run(page, port) {
    const scopes = page.getByRole("navigation", { name: "片库切换" });
    const views = page.getByRole("navigation", { name: "资源视图" });
    const card = () =>
      page
        .locator(".poster-card")
        .filter({ has: page.getByTitle(main.name, { exact: true }) });
    const hover = page.getByRole("dialog", { name: "资源悬浮预览" });
    const details = page.getByRole("dialog", { name: "资源详情", exact: true });
    await page.getByTitle(main.name, { exact: true }).waitFor();
    assert.equal(
      await scopes
        .getByRole("button", { name: "NSFW", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(
      await page.getByTitle("电影示例.mp4", { exact: true }).count(),
      0,
    );
    await card().locator(".media-cover img").waitFor({ timeout: 20000 });
    await card()
      .locator(".media-cover img")
      .evaluate((image) => image.decode());
    assert.ok(
      await card()
        .locator(".media-cover img")
        .evaluate((image) => image.naturalWidth > 0),
      "saved JPEG cover decoded",
    );
    assert.ok(coverRequests > 0);
    assert.equal(
      videoRequests,
      0,
      "page visits, missing covers and reloads never read videos",
    );
    const tags = page.locator(".filter-options.tags");
    await tags.getByRole("button", { name: /^室内/ }).click();
    await tags.getByRole("button", { name: /^固定机位/ }).click();
    await page.waitForFunction(
      () => document.querySelectorAll(".poster-card").length === 1,
    );
    assert.equal(
      await page.getByTitle(second.name, { exact: true }).count(),
      0,
    );
    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelectorAll(".poster-card").length > 2,
    );
    const beforeHover = events.length;
    await card().hover();
    await hover.waitFor();
    assert.equal(
      events.length,
      beforeHover,
      "hover/cover loading never writes activity",
    );
    const position = await hover.boundingBox();
    assert.ok(
      position.x >= 0 &&
        position.x + position.width <= 1440 &&
        position.y + position.height <= 1000,
    );
    await hover.hover();
    await page.waitForTimeout(300);
    assert.ok(
      await hover.isVisible(),
      "pointer can enter interactive hover card",
    );
    failFavorite = true;
    await hover.getByRole("button", { name: "收藏", exact: true }).click();
    await page
      .getByRole("alert")
      .filter({ hasText: "模拟收藏保存失败" })
      .waitFor();
    assert.equal(
      await card().locator(".poster-favorite").getAttribute("aria-pressed"),
      "false",
    );
    await hover.getByRole("button", { name: "收藏", exact: true }).click();
    await hover.getByRole("button", { name: "已收藏", exact: true }).waitFor();
    assert.equal(
      await card().locator(".poster-favorite").getAttribute("aria-pressed"),
      "true",
    );
    await page.screenshot({
      path: path.join(project, ".cache/library-home-hover.png"),
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    await hover.waitFor({ state: "detached" });
    await views.getByRole("button", { name: "收藏", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelectorAll(".poster-card").length === 1,
    );
    await page.reload();
    await views.getByRole("button", { name: "收藏", exact: true }).click();
    await card().waitFor();
    assert.equal(
      await card().locator(".poster-favorite").getAttribute("aria-pressed"),
      "true",
      "favorites survive refresh",
    );
    await page.getByTitle(main.name, { exact: true }).click();
    await details.waitFor();
    assert.ok(!(await details.innerText()).includes("曲风与编制"));
    assert.equal(await details.locator(".reader-overview p").innerText(), main.analysis.summary,
      "video details lead with the independent reader synopsis");
    assert.equal(await details.locator(".analysis-facets").isVisible(), false,
      "classification facets are collapsed, not the main synopsis");
    await details.getByText("分析依据与文件信息", { exact: true }).click();
    await details.getByText("发布者描述（未经核实）", { exact: true }).waitFor();
    assert.ok((await details.innerText()).includes("测试发布者原文"));
    assert.equal(await page.evaluate(() => window.publisherExecuted), undefined, "publisher text must be escaped");
    await details.getByRole("button", { name: "深度采样分析", exact: true }).click();
    await page.waitForFunction(() => !document.querySelector('.resource-reader button[aria-label="资源分析进行中"]'));
    assert.equal(analysisMode, "deep");
    await page.screenshot({ path: path.join(project, ".cache/library-source-context.png"), fullPage: true });
    assert.equal(await details.locator(".analysis-facets").isVisible(), true);
    assert.ok((await details.locator(".summary-evidence").innerText()).includes("00:12"));
    await details.getByText("分析依据与文件信息", { exact: true }).click();
    await details.getByRole("button", { name: "重新分析此资源" }).click();
    await details
      .getByRole("button", { name: "重新分析此资源" })
      .waitFor({ timeout: 12000 });
    assert.equal(analyzed, main.path);
    assert.equal(analysisMode, "full");
    const playedBefore = events.filter(
      (item) => item.action === "progress",
    ).length;
    assert.equal(
      videoRequests,
      0,
      "hover, filters, favorites and details never read videos",
    );
    await details
      .getByRole("button", { name: "预览资源", exact: true })
      .click();
    const preview = page.getByRole("dialog", { name: "资源预览", exact: true });
    await preview.waitFor();
    await page.waitForFunction(
      () => document.querySelector(".media-stage video")?.readyState >= 2,
    );
    assert.ok(videoRequests > 0, "only explicit player loads the video");
    assert.equal(
      events.filter((item) => item.action === "progress").length,
      playedBefore,
      "metadata load is not watching",
    );
    await preview.locator("video").evaluate(async (video) => {
      await video.play();
      video.currentTime = 1.5;
    });
    await page.waitForTimeout(350);
    await preview.locator("video").evaluate((video) => video.pause());
    await page.waitForTimeout(200);
    await page.keyboard.press("Escape");
    await details.waitFor();
    await page.keyboard.press("Escape");
    await details.waitFor({ state: "detached" });
    assert.ok(
      records[main.path].position >= 1.5,
      "pause/close flushes playback position",
    );
    await views.getByRole("button", { name: "历史记录", exact: true }).click();
    await page.locator(".history-entry").first().waitFor();
    await page.getByRole("button", { name: "观看记录", exact: true }).click();
    await page.getByRole("button", { name: "继续观看", exact: true }).click();
    await preview.waitFor();
    await page.waitForFunction(
      () => document.querySelector(".media-stage video")?.currentTime >= 1,
    );
    await page.keyboard.press("Escape");
    await details.waitFor();
    await page.keyboard.press("Escape");
    await details.waitFor({ state: "detached" });
    await page.getByRole("button", { name: "暂停记录", exact: true }).click();
    await page
      .locator(".history-toolbar")
      .getByRole("button", { name: "恢复记录", exact: true })
      .waitFor();
    const openBefore = records[second.path]?.opened_at;
    await views.getByRole("button", { name: "全部资源", exact: true }).click();
    await page.getByTitle(second.name, { exact: true }).click();
    await details.waitFor();
    await page.keyboard.press("Escape");
    await details.waitFor({ state: "detached" });
    assert.equal(
      records[second.path]?.opened_at,
      openBefore,
      "paused history ignores opens",
    );
    await page.reload();
    await page
      .getByText("历史记录已暂停，收藏不受影响。", { exact: false })
      .waitFor();
    await page
      .locator(".history-paused")
      .getByRole("button", { name: "恢复记录" })
      .click();
    await views.getByRole("button", { name: "历史记录", exact: true }).click();
    await page.getByRole("button", { name: "观看记录", exact: true }).click();
    await page.locator(".history-entry").waitFor();
    await page.screenshot({
      path: path.join(project, ".cache/library-history.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "清空记录", exact: true }).click();
    const clear = page.getByRole("dialog", {
      name: "清空历史记录",
      exact: true,
    });
    await clear.getByRole("button", { name: "取消", exact: true }).click();
    assert.ok(records[main.path].watched_at, "cancel does not clear");
    await page.getByRole("button", { name: "清空记录", exact: true }).click();
    await clear.getByRole("button", { name: "确认清空", exact: true }).click();
    await clear.waitFor({ state: "detached" });
    await page
      .getByRole("heading", { name: "还没有历史记录", exact: true })
      .waitFor();
    assert.ok(
      records[main.path].favorite && records[main.path].opened_at,
      "clear watch history preserves favorites and opens",
    );
    await page.getByRole("button", { name: "最近打开", exact: true }).click();
    await page.locator(".history-entry").waitFor();
    await page
      .getByRole("button", { name: "移除记录 " + main.name, exact: true })
      .click();
    await page
      .getByRole("heading", { name: "还没有历史记录", exact: true })
      .waitFor();
    await views.getByRole("button", { name: "全部资源", exact: true }).click();
    await card().waitFor();
    await page.getByRole("button", { name: "隐私模式", exact: true }).click();
    await page.waitForTimeout(400);
    assert.equal(await page.locator(".poster-card img").count(), 0);
    const requestsBefore = mediaRequests;
    await page
      .getByRole("button", { name: "查看简介 此资源", exact: true })
      .first()
      .click();
    await hover.waitFor();
    assert.ok((await hover.innerText()).includes("隐私模式已开启"));
    assert.equal(await hover.locator("img").count(), 0);
    assert.equal(
      mediaRequests,
      requestsBefore,
      "masked hover cannot request media",
    );
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "隐私模式", exact: true }).click();
    await tags.getByRole("button", { name: /^固定机位/ }).click();
    await scopes.getByRole("button", { name: "电影", exact: true }).click();
    await page.getByTitle("电影示例.mp4", { exact: true }).waitFor();
    assert.equal(await page.getByTitle(main.name, { exact: true }).count(), 0);
    await scopes.getByRole("button", { name: "NSFW", exact: true }).click();
    await page
      .getByRole("button", { name: "移除标签 固定机位", exact: true })
      .waitFor();
    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    await page.getByLabel("搜索资源", { exact: true }).fill("慢");
    await page.waitForTimeout(260);
    await page.getByLabel("搜索资源", { exact: true }).fill("三维动画示例");
    await card().waitFor();
    await page.waitForTimeout(600);
    assert.equal(
      await page.locator(".poster-card").count(),
      1,
      "late stale response is ignored",
    );
    await page.getByRole("button", { name: "清除筛选", exact: true }).click();
    await card().waitFor();
    await page.screenshot({
      path: path.join(project, ".cache/library-home-desktop.png"),
      fullPage: true,
    });
    for (const width of [390, 320, 1920]) {
      await page.setViewportSize({ width, height: width < 500 ? 844 : 1080 });
      assert.ok(
        await page.evaluate(
          () =>
            document.documentElement.scrollWidth <= innerWidth &&
            document.documentElement.scrollHeight <= innerHeight,
        ),
        "contained library at " + width,
      );
      if (width === 390) {
        await page
          .getByRole("button", { name: "查看简介 " + main.name, exact: true })
          .click();
        await hover.waitFor();
        const rect = await hover.boundingBox();
        assert.ok(rect.x >= 0 && rect.x + rect.width <= width);
        await page.screenshot({
          path: path.join(project, ".cache/library-home-mobile.png"),
          fullPage: true,
        });
        await page.keyboard.press("Escape");
      }
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: "筛选与排序", exact: true }).click();
    await page.getByRole("button", { name: "来源目录", exact: true }).click();
    const directory = page.getByRole("dialog", {
      name: "来源目录",
      exact: true,
    });
    await directory
      .getByRole("button", { name: "来源目录", exact: true })
      .click();
    await directory.getByRole("button", { name: /^参考文档.pdf/ }).click();
    await details
      .getByRole("heading", { name: "文档主题", exact: true })
      .waitFor();
    assert.ok(!(await details.innerText()).includes("渲染与动画形式"));
    await page.keyboard.press("Escape");
    const originalAnalysis = second.analysis;
    second.analysis = {
      ...originalAnalysis,
      status: "failed",
      reason: "model_timeout",
      failure_stage: "classification",
      summary: "分类识别未完成：模型请求超过 120 秒（已重试 1 次）。",
      facets: {},
      retryable: true,
      diagnostics: {
        threshold: 0.4,
        attempts: [
          {
            stage: "classification",
            attempt: 1,
            timeout_seconds: 60,
            elapsed_ms: 60000,
            status: "failed",
            reason: "model_timeout",
          },
          {
            stage: "classification",
            attempt: 2,
            timeout_seconds: 120,
            elapsed_ms: 120000,
            status: "failed",
            reason: "model_timeout",
          },
        ],
      },
    };
    await page.getByTitle(second.name, { exact: true }).click();
    await details
      .getByRole("heading", { name: "分析失败原因", exact: true })
      .waitFor();
    assert.ok((await details.innerText()).includes("超过 120 秒"));
    await details.getByText("分析依据与文件信息", { exact: true }).click();
    await details.getByLabel("模型请求记录", { exact: true }).waitFor();
    assert.ok(
      (
        await details.getByLabel("模型请求记录", { exact: true }).innerText()
      ).includes("第 2 次"),
    );
    await page.keyboard.press("Escape");
    second.analysis = {
      ...second.analysis,
      status: "needs_review",
      failure_stage: "",
      reason: "model_uncertain",
      summary: "模型已读取内容，但无法确定主分类；这不是接口失败。",
      review_note: "画面信息不足，无法确认主要用途。",
      content_description: "可见蓝色几何图形，画面没有可读文字。",
      sampling: {
        method: "visual_diversity",
        candidate_count: 12,
        duration_seconds: 20,
        timestamps: [0.2, 9.109, 14.455, 19.8],
      },
      confidence: 0.4,
    };
    await page.getByTitle(second.name, { exact: true }).click();
    await details
      .getByRole("heading", { name: "待确认原因", exact: true })
      .waitFor();
    assert.ok(
      (await details.innerText()).includes("模型说明（待核实）：画面信息不足"),
    );
    await details
      .getByRole("heading", { name: "已识别内容", exact: true })
      .waitFor();
    assert.ok(
      (await details.innerText()).includes(second.analysis.content_description),
    );
    await details.getByText("分析依据与文件信息", { exact: true }).click();
    assert.ok((await details.innerText()).includes("12 个候选画面中选取 4 帧"));
    assert.ok((await details.innerText()).includes("9.109 秒"));
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: path.join(project, ".cache/analysis-review-mobile.png"),
      fullPage: true,
    });
    await page.keyboard.press("Escape");
    second.analysis = originalAnalysis;
    console.log(
      "Analysis recovery UI: phase-specific timeout, request attempts, review note and mobile layout passed.",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page
      .getByRole("button", { name: "删除资源 " + main.name, exact: true })
      .click();
    const deletion = page.getByRole("dialog", {
      name: "删除资源",
      exact: true,
    });
    await deletion.getByLabel("删除范围", { exact: true }).waitFor();
    assert.equal(
      await deletion.locator(".resource-delete-targets li").count(),
      4,
    );
    assert.ok(
      await deletion
        .getByRole("button", { name: "确认删除资源", exact: true })
        .isDisabled(),
    );
    assert.equal(
      deleteCalls,
      0,
      "opening confirmation cannot delete resources",
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "deletion paths fit mobile width",
    );
    await page.screenshot({
      path: path.join(project, ".cache/library-delete-mobile.png"),
      fullPage: true,
    });
    await deletion.getByRole("button", { name: "取消", exact: true }).click();
    assert.equal(deleteCalls, 0, "cancel is non-destructive");
    await card().getByTitle(main.name, { exact: true }).click();
    await details
      .getByRole("button", { name: "删除资源", exact: true })
      .click();
    await deletion.getByLabel("删除范围", { exact: true }).waitFor();
    await deletion
      .getByRole("checkbox", {
        name: "确认删除云盘资源并回收本地副本",
        exact: true,
      })
      .check();
    await deletion
      .getByRole("button", { name: "确认删除资源", exact: true })
      .click();
    assert.ok(
      await deletion
        .getByRole("button", { name: "正在删除…", exact: true })
        .isDisabled(),
      "duplicate submit is disabled",
    );
    await page.keyboard.press("Escape");
    assert.ok(
      await deletion.isVisible(),
      "cannot dismiss an in-flight deletion",
    );
    await deletion
      .getByRole("alert")
      .filter({ hasText: "模拟云盘封面删除失败" })
      .waitFor();
    await deletion
      .getByText("云盘资源 · 云盘已删除", { exact: true })
      .waitFor();
    assert.equal(
      await card().count(),
      1,
      "partial failure must not hide the card",
    );
    assert.ok(
      records[main.path].favorite,
      "partial failure must not discard favorites",
    );
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: path.join(project, ".cache/library-delete-partial.png"),
      fullPage: true,
    });
    await deletion
      .getByRole("button", { name: "重试未完成项目", exact: true })
      .click();
    await deletion
      .getByText("删除完成，收藏和历史记录已清理。", { exact: true })
      .waitFor();
    assert.equal(deleteCalls, 2);
    assert.equal(records[main.path], undefined);
    await deletion.getByRole("button", { name: "完成", exact: true }).click();
    await page.reload();
    await page.getByTitle(second.name, { exact: true }).waitFor();
    assert.equal(
      await card().count(),
      0,
      "deleted resources stay absent after reload",
    );
    console.log(
      "Resource deletion: card/detail entry points, mobile confirmation, cancel, busy guard, partial results, retry and persistence passed.",
    );
    console.log(
      "Library browser: real covers, tag intersection, interactive hover, favorites/error recovery, watch/resume, private history controls, isolation and mobile layouts passed.",
    );
  }
  async function runSummaryQuality(page) {
    const original = structuredClone(main.analysis);
    const details = page.getByRole("dialog", { name: "资源详情", exact: true });
    const hover = page.getByRole("dialog", { name: "资源悬浮预览" });
    for (const mode of ["ready", "story", "legacy", "review", "insufficient"]) {
      main.analysis = structuredClone(original);
      if (mode === "story") {
        main.analysis.narrative.mode = "story";
        main.analysis.narrative.events.forEach((event, index) => { event.phase = index ? "outcome" : "setup"; });
      }
      if (mode === "legacy") { delete main.analysis.narrative; main.analysis.summary_quality.version = 1; }
      if (mode === "review") main.analysis.summary_quality.status = "review";
      if (mode === "insufficient") {
        main.analysis.summary_quality.status = "review";
        main.analysis.narrative = { version: 1, mode: "insufficient", issues: ["narrative_evidence_insufficient"], events: [] };
      }
      await page.reload();
      const title = page.getByTitle(main.name, { exact: true });
      await title.waitFor();
      const card = page.locator(".poster-card").filter({ has: title });
      await card.hover();
      await hover.waitFor();
      const hoverSummary = await hover.locator(".hover-summary").innerText();
      assert.equal(hoverSummary === original.summary, mode === "ready" || mode === "story");
      if (mode !== "ready" && mode !== "story") {
        const expected = mode === "legacy" ? "旧版简介" : "剧情待确认";
        assert.ok((await card.innerText()).includes(expected));
        assert.ok((await hover.innerText()).includes(expected));
      }
      await hover.getByRole("button", { name: /打开详情/ }).click();
      await details.waitFor();
      assert.equal(await details.locator(".reader-overview p").innerText(), hoverSummary,
        "hover and details use the same synopsis state");
      assert.equal(await details.locator(".analysis-facets").isVisible(), false);
      assert.equal(await details.locator(".reader-overview h3").innerText(),
        mode === "story" ? "剧情梗概" : mode === "ready" ? "片段内容" : mode === "legacy" ? "剧情总结待更新" : "剧情待确认");
      assert.ok(!(await details.locator(".reader-overview p").innerText()).includes("没有剧情"));
      if (mode === "ready") {
        await details.getByRole("button", { name: "更新简介", exact: true }).click();
        await page.waitForFunction(() => !document.querySelector('.reanalyze-label')?.textContent.includes('分析中'));
        assert.equal(analysisMode, "summary", "summary refresh is explicit, not a hidden full reclassification");
        await page.screenshot({ path: path.join(project, ".cache/library-summary-quality.png") });
      }
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(await details.locator(".reader-overview p").isVisible(), true);
      await details.getByRole("button", { name: "关闭资源详情" }).click();
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    main.analysis = original;
    console.log("Video synopsis UI: independent overview, shared hover/details, folded evidence, legacy/review labels and mobile passed.");
  }
  return { handle, run, runLayout, runSummaryQuality };
};
