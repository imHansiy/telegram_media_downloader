/* Real browser smoke tests with local fixtures, never production accounts or files.
 * Run after building webui: node tests/webui/resource_library.browser.cjs
 */
const assert = require("node:assert/strict");
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { chromium } = require("playwright");

const project = path.resolve(__dirname, "../..");
const python =
  process.env.TEST_PYTHON ||
  path.join(
    project,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
const taxonomy = JSON.parse(
  execFileSync(
    python,
    [
      "-c",
      "import json; from module.resource_analysis import taxonomy_payload; print(json.dumps(taxonomy_payload()))",
    ],
    { cwd: project, encoding: "utf8", windowsHide: true },
  ),
);
const root = path.join(project, "module/static/newui");
function resource(
  name,
  category,
  kind = "video",
  subtype = "",
  status = "complete",
) {
  const profile = subtype
    ? taxonomy.adult_subtypes.find((item) => item.id === subtype)
    : taxonomy.categories.find((item) => item.id === category);
  const facets = Object.fromEntries(
    profile.directions.map((direction) => [
      direction,
      `${name} 对应的${direction}，以实际证据为准。`,
    ]),
  );
  return {
    name,
    path: `来源目录/${name}`,
    size: 1280000,
    modified: "2026-09-05T01:00:00Z",
    content_type: "",
    source_context: { caption: "测试发布者原文\n<script>window.publisherExecuted = true</script>", original_name: "original-fixture.mp4" },
    analysis: {
      schema_version: 2,
      resource_kind: kind,
      category,
      category_label: taxonomy.categories.find((item) => item.id === category)
        .label,
      subtype,
      subtype_label: subtype ? profile.label : "",
      status,
      confidence: 0.92,
      directions: profile.directions,
      facets,
      summary: Object.entries(facets)
        .map(([key, text]) => `${key}：${text}`)
        .join("\n"),
      ...(kind === "video" && status === "complete" ? {
        summary: "一人拿起工具组装模型，另一人递出零件，两人在桌边配合完成操作。后段两人收起剩余零件，带着完成的模型离开工作台。",
        narrative: { version: 2, mode: "activity", issues: [], events: [
          { id: "event_1", phase: "activity", action: "一人拿起工具组装模型，另一人递出零件。", start: 12, end: 12, evidence_refs: ["frame_1"] },
          { id: "event_2", phase: "activity", action: "两人收起剩余零件，带着模型离开。", start: 90, end: 90, evidence_refs: ["frame_2"] },
        ] },
        summary_quality: { version: 5, status: "ready", audit: { version: 2, status: "passed", issues: [] }, issues: [], evidence_catalog: [
          { id: "frame_1", kind: "video_frames", timestamp: 12 },
          { id: "frame_2", kind: "video_frames", timestamp: 90 },
        ] },
        summary_sentences: [
          { text: "一人拿起工具组装模型，另一人递出零件，两人在桌边配合完成操作。", evidence_refs: ["frame_1"], event_refs: ["event_1"] },
          { text: "后段两人收起剩余零件，带着完成的模型离开工作台。", evidence_refs: ["frame_2"], event_refs: ["event_2"] },
        ],
      } : {}),
      tags: ["测试素材"],
      evidence_sources: ["video_frames"],
      limitations: ["仅用于离线界面测试，不是真实用户资源。"],
    },
  };
}
const entries = [
  resource("Python 入门.mp4", "education"),
  resource("音乐演出.mp4", "music"),
  resource("参考文档.pdf", "document", "document"),
  resource("三维动画示例.mp4", "adult", "video", "animation_3d"),
  resource("旧版示例.mp4", "adult", "video", "", "legacy"),
];
const library = require("./library_home.scenarios.cjs").createLibraryFixture({
  taxonomy,
  resource,
  project,
});
let analyzedPath = "",
  polls = 0;
const account = {
  accounts: [
    {
      id: "main",
      profileName: "主账号",
      username: "fixture_main",
      phoneNumber: "+860000000000",
      firstName: "主账号",
      status: "connected",
      hasSession: true,
      isRunning: true,
      sessionName: "fixture-main",
      runtimeStatus: "running",
    },
    {
      id: "spare",
      profileName: "备用账号",
      username: "",
      phoneNumber: "",
      firstName: "",
      status: "disconnected",
      hasSession: false,
      isRunning: false,
      sessionName: "",
    },
  ],
  account: null,
  session_exists: true,
};
let fixtureConfig = {
  upload_drive: {
    webdav_url: "https://fixture.invalid/dav",
    webdav_username: "fixture",
    remote_dir: "/资源库",
  },
};
let configAttempts = 0,
  lastControl = null,
  lastLogin = null,
  lastBotAccess = null;
let unlockAttempts = 0,
  unlockPayload = "";
const tasks = Array.from({ length: 23 }, (_, index) => ({
  chat: "示例频道",
  id: String(index + 1),
  filename:
    index === 0
      ? "需要重传的长文件名_设计工作坊完整记录与参考资料.mp4"
      : `工作坊_${String(index + 1).padStart(2, "0")}.mp4`,
  total_size: "245 MB",
  download_progress: index < 2 ? "100" : "43",
  upload_progress: index === 0 ? "28" : index === 1 ? "100" : "0",
  status:
    index === 0
      ? "上传失败"
      : index === 1
        ? "已完成"
        : index === 2
          ? "已暂停"
          : index === 3
            ? "等待中"
            : "下载中",
  state:
    index === 0
      ? "upload_failed"
      : index === 1
        ? "finished"
        : index === 2
          ? "paused"
          : "downloading",
  download_speed: "512 KB/s",
  remote_path: "/资源库/示例.mp4",
  created_at: "2026-09-05 08:00:00",
  error:
    index === 0
      ? "云盘连接中断，本地文件已完整保存。可以重置上传后仅重传本地文件。"
      : "",
}));
const streams = new Set();
const taskEvent = () =>
  `data: ${JSON.stringify({ type: "update", tasks, history: [] })}\n\n`;
const broadcastTasks = () =>
  streams.forEach((stream) => stream.write(taskEvent()));
const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");
  const json = (value, status = 200) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ success: true, ...value }));
  };
  if (url.pathname === "/api/bootstrap")
    return json({ version: "test", config: fixtureConfig, account });
  if (url.pathname === "/api/account/status") return json(account);
  if (url.pathname === "/stream") {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(taskEvent());
    streams.add(res);
    req.on("close", () => streams.delete(res));
    return;
  }
  const readBody = (handler) => {
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => handler(JSON.parse(body || "{}")));
  };
  if (url.pathname === "/login" && req.method === "GET") {
    const template = fs
      .readFileSync(path.join(project, "module/templates/login.html"), "utf8")
      .replace(
        /\{\{\s*url_for\('static', filename='([^']+)'\)\s*\}\}/g,
        (_, file) => `/static/${file}`,
      );
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(template);
    return;
  }
  if (url.pathname === "/login" && req.method === "POST")
    return readBody((body) => {
      unlockAttempts++;
      unlockPayload = body.password;
      json({ code: unlockAttempts === 1 ? "0" : "1" });
    });
  if (
    [
      "/static/css/workbench-login.css",
      "/static/login/workbench-login.js",
      "/static/aes/crypto-js-master/crypto-js.js",
    ].includes(url.pathname)
  ) {
    const file = path.join(project, "module", url.pathname);
    res.writeHead(200, {
      "Content-Type": file.endsWith(".css") ? "text/css" : "text/javascript",
    });
    fs.createReadStream(file).pipe(res);
    return;
  }
  if (url.pathname === "/api/config" && req.method === "POST")
    return readBody((body) => {
      configAttempts++;
      if (configAttempts === 1)
        return json(
          { success: false, message: "模拟保存失败，修改尚未写入。" },
          500,
        );
      fixtureConfig = body.config;
      json({ message: "配置已保存", config: fixtureConfig });
    });
  if (url.pathname === "/test_webdav")
    return json({ message: "离线连接测试通过" });
  if (url.pathname === "/task_control")
    return readBody((body) => {
      lastControl = body;
      const task = tasks.find((item) => item.id === body.message_id);
      if (task && body.action === "pause") {
        task.state = "paused";
        task.status = "已暂停";
      }
      if (task && body.action === "resume") {
        task.state = "downloading";
        task.status = "下载中";
      }
      if (body.action.startsWith("delete"))
        tasks.splice(tasks.indexOf(task), 1);
      json({ message: "离线任务操作成功" });
      broadcastTasks();
    });
  if (url.pathname === "/api/profiles")
    return readBody((body) => {
      account.accounts.push({
        id: "new",
        profileName: body.name,
        status: "disconnected",
        hasSession: false,
        isRunning: false,
        phoneNumber: "",
        username: "",
      });
      json({ account });
    });
  if (url.pathname === "/api/profiles/update")
    return readBody((body) => {
      account.accounts.find((item) => item.id === body.profile_id).profileName =
        body.name;
      json({ account });
    });
  if (
    url.pathname === "/api/profiles/stop" ||
    url.pathname === "/api/profiles/start"
  )
    return readBody((body) => {
      const item = account.accounts.find((item) => item.id === body.profile_id);
      item.isRunning = url.pathname.endsWith("/start");
      item.status = item.isRunning ? "connected" : "disconnected";
      json({ account });
    });
  if (url.pathname === "/api/profiles/delete")
    return readBody((body) => {
      account.accounts = account.accounts.filter(
        (item) => item.id !== body.profile_id,
      );
      json({ account });
    });
  if (url.pathname === "/api/account/send_code")
    return readBody((body) => {
      lastLogin = body;
      json({});
    });
  if (url.pathname === "/api/account/verify_code")
    return json({ needs_password: true });
  if (url.pathname === "/api/account/verify_password") {
    const item = account.accounts.find(
      (item) => item.id === lastLogin.profile_id,
    );
    item.hasSession = true;
    item.isRunning = true;
    item.status = "connected";
    return json({ account });
  }
  if (url.pathname === "/api/bot/access")
    return readBody((body) => {
      lastBotAccess = body;
      fixtureConfig.bot_download_access_mode = body.mode;
      fixtureConfig.allowed_user_ids = body.allowedUsers;
      json({ account, config: fixtureConfig });
    });
  if (library.handle(req, res, url, json, readBody)) return;
  if (url.pathname.startsWith("/api/"))
    return json({ message: "Unexpected fixture endpoint" }, 404);
  const relative = url.pathname.startsWith("/static/newui/")
    ? url.pathname.slice("/static/newui/".length)
    : "index.html";
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {
    res.writeHead(404);
    res.end();
    return;
  }
  res.writeHead(200, {
    "Content-Type": file.endsWith(".js")
      ? "text/javascript"
      : file.endsWith(".css")
        ? "text/css"
        : "text/html",
  });
  fs.createReadStream(file).pipe(res);
});

(async () => {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const browser = await chromium.launch({
    channel: process.env.TEST_BROWSER_CHANNEL || "msedge",
    headless: true,
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: "reduce",
    });
    page.setDefaultTimeout(8000);
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("https://**/*", (route) => route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/files`);
    if (process.env.SUMMARY_QUALITY_ONLY === "1") {
      await library.runSummaryQuality(page);
      assert.deepEqual(errors, []);
      return;
    }
    if (process.env.TASK_PROGRESS_ONLY === "1") {
      await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "传输任务" }).click();
      await require("./task_progress.scenarios.cjs").runTaskProgress(page, tasks, broadcastTasks);
      return;
    }
    await require("./shell_layout.scenarios.cjs").runShellLayout(page, project);
    if (process.env.LIBRARY_GEOMETRY_ONLY === "1") return;
    await library.runLayout(page);
    if (process.env.LIBRARY_LAYOUT_ONLY === "1") return;
    await library.run(page, server.address().port);

    // Every other workbench page uses the same shell; test real actions against local fixtures only.
    const nav = page.getByRole("navigation", { name: "主导航" });
    await nav.getByRole("button", { name: "传输任务" }).click();
    await page
      .getByRole("heading", { name: "传输任务", exact: true })
      .waitFor();
    await require("./task_progress.scenarios.cjs").runTaskProgress(page, tasks, broadcastTasks);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    await page.screenshot({
      path: path.join(project, ".cache/workspace-tasks-mobile.png"),
      fullPage: true,
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: path.join(project, ".cache/workspace-tasks-desktop.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "下一页任务" }).click();
    await page.getByRole("button", { name: "上一页任务" }).click();
    await page.getByLabel("搜索任务").fill("工作坊_03");
    await page.getByRole("button", { name: "继续传输", exact: true }).click();
    await page.getByRole("button", { name: "暂停传输", exact: true }).waitFor();
    assert.equal(lastControl.action, "resume");
    await page.getByRole("button", { name: "暂停传输", exact: true }).click();
    await page.getByRole("button", { name: "继续传输", exact: true }).waitFor();
    assert.equal(lastControl.action, "pause");
    await page.getByLabel("搜索任务").fill("需要重传");
    await page.getByRole("button", { name: "重置上传", exact: true }).click();
    const resetDialog = page.getByRole("dialog", { name: "重置上传" });
    assert.equal(
      lastControl.action,
      "pause",
      "opening confirmation must not execute a destructive reset",
    );
    await resetDialog.getByRole("button", { name: "确认重置并重传" }).click();
    await resetDialog.waitFor({ state: "detached" });
    assert.equal(lastControl.action, "reset_upload");
    await page.getByRole("button", { name: "删除任务", exact: true }).click();
    const deletion = page.getByRole("dialog", { name: "删除任务" });
    await deletion
      .getByRole("button", { name: "仅删除任务", exact: true })
      .click();
    await deletion.waitFor({ state: "detached" });
    assert.equal(lastControl.action, "delete");
    await page.getByText("没有符合条件的任务", { exact: true }).waitFor();

    await nav.getByRole("button", { name: "设置", exact: true }).click();
    const settingsNav = page.getByRole("navigation", { name: "设置分区" });
    await settingsNav
      .getByRole("button", { name: "下载规则", exact: true })
      .click();
    const saveCover = page.getByRole("checkbox", {
      name: "下载后自动保存视频封面",
      exact: true,
    });
    assert.ok(
      await saveCover.isChecked(),
      "covers default to enabled independently of AI",
    );
    await saveCover.uncheck();
    await settingsNav
      .getByRole("button", { name: "云盘连接", exact: true })
      .click();
    await page
      .getByLabel("服务器地址", { exact: false })
      .fill("https://changed.invalid/dav");
    await settingsNav
      .getByRole("button", { name: "资源分析", exact: true })
      .click();
    await page.getByLabel("模型名称", { exact: true }).fill("fixture-model");
    await page.getByLabel("分类置信度阈值", { exact: false }).fill("1.5");
    await settingsNav
      .getByRole("button", { name: "云盘连接", exact: true })
      .click();
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await page
      .getByRole("heading", { name: "资源分析", exact: true })
      .waitFor();
    assert.equal(
      configAttempts,
      0,
      "invalid hidden-section fields must be revealed before any API write",
    );
    await page.getByLabel("分类置信度阈值", { exact: false }).fill("0.75");
    await page.screenshot({
      path: path.join(project, ".cache/workspace-settings-desktop.png"),
      fullPage: true,
    });
    await settingsNav
      .getByRole("button", { name: "云盘连接", exact: true })
      .click();
    assert.equal(
      await page.getByLabel("服务器地址", { exact: false }).inputValue(),
      "https://changed.invalid/dav",
    );
    page.once("dialog", (dialog) => dialog.dismiss());
    await nav.getByRole("button", { name: "资源库", exact: true }).click();
    assert.ok(
      page.url().endsWith("/config"),
      "unsaved settings cannot be silently discarded",
    );
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "模拟保存失败" }).waitFor();
    assert.equal(
      await page.getByText("设置已成功保存", { exact: true }).count(),
      0,
    );
    await page.getByRole("button", { name: "保存设置", exact: true }).click();
    await page.getByText("设置已成功保存", { exact: true }).waitFor();
    assert.equal(
      fixtureConfig.upload_drive.webdav_url,
      "https://changed.invalid/dav",
    );
    assert.equal(fixtureConfig.video_classifier.model, "fixture-model");
    assert.equal(fixtureConfig.video_classifier.share_source_text, false, "saving unrelated settings must not opt in to publisher uploads");
    assert.equal(fixtureConfig.video_classifier.use_subtitles, false, "saving unrelated settings must not opt in to subtitle uploads");
    assert.equal(
      fixtureConfig.generate_video_covers,
      false,
      "disabled covers persisted through config save",
    );
    await page.reload();
    await settingsNav
      .getByRole("button", { name: "下载规则", exact: true })
      .click();
    assert.equal(
      await saveCover.isChecked(),
      false,
      "cover setting survives reload",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await settingsNav
      .getByRole("button", { name: "资源分析", exact: true })
      .click();
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    await page.screenshot({
      path: path.join(project, ".cache/workspace-settings-mobile.png"),
      fullPage: true,
    });

    await nav.getByRole("button", { name: "账号", exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.screenshot({
      path: path.join(project, ".cache/workspace-accounts-desktop.png"),
      fullPage: true,
    });
    await page
      .locator("#acct-card-main")
      .getByRole("button", { name: "停止同步" })
      .click();
    await page
      .locator("#acct-card-main")
      .getByRole("button", { name: "启动同步" })
      .waitFor();
    await page
      .locator("#acct-card-main")
      .getByRole("button", { name: "启动同步" })
      .click();
    await page
      .locator("#acct-card-main")
      .getByRole("button", { name: "停止同步" })
      .waitFor();
    await page.getByRole("button", { name: "创建档案", exact: true }).click();
    const create = page.getByRole("dialog", { name: "创建账号档案" });
    await create.getByLabel("档案名称").fill("离线测试档案");
    await create.getByRole("button", { name: "创建档案", exact: true }).click();
    await create.waitFor({ state: "detached" });
    await page.getByRole("heading", { name: "离线测试档案" }).waitFor();
    await page
      .locator("#acct-card-new")
      .getByRole("button", { name: "重命名账号档案" })
      .click();
    const rename = page.getByRole("dialog", { name: "重命名账号档案" });
    await rename.getByLabel("档案名称").fill("已重命名");
    await rename.getByRole("button", { name: "保存名称" }).click();
    await page
      .getByRole("heading", { name: "已重命名", exact: true })
      .waitFor();
    await page
      .locator("#acct-card-spare")
      .getByRole("button", { name: "登录账号" })
      .click();
    const login = page.getByRole("dialog", { name: "登录到：备用账号" });
    await login.getByLabel("手机号", { exact: false }).fill("+860000000001");
    await login.getByRole("button", { name: "发送验证码" }).click();
    await login
      .getByRole("textbox", { name: "验证码", exact: true })
      .fill("12345");
    await login.getByRole("button", { name: "验证并继续" }).click();
    await login.getByLabel("二步验证密码").fill("fixture-only");
    await login.getByRole("button", { name: "完成登录" }).click();
    await login.waitFor({ state: "detached" });
    assert.equal(lastLogin.profile_id, "spare");
    await page.getByRole("button", { name: "管理权限", exact: true }).click();
    const access = page.getByRole("dialog", { name: "Bot 投递权限" });
    await access.getByRole("radio", { name: /指定用户/ }).check();
    await access.getByLabel("允许的用户").fill("123456");
    await access.getByRole("button", { name: "保存权限" }).click();
    await access.getByText("Bot 投递权限已保存。", { exact: true }).waitFor();
    assert.deepEqual(lastBotAccess.allowedUsers, ["123456"]);
    await page.keyboard.press("Escape");
    await access.waitFor({ state: "detached" });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    );
    await page.screenshot({
      path: path.join(project, ".cache/workspace-accounts-mobile.png"),
      fullPage: true,
    });
    await page.goBack();
    await page
      .getByRole("heading", { name: "云盘连接", exact: true })
      .waitFor();
    await page.goForward();
    await page.getByRole("heading", { name: /^账号管理/ }).waitFor();
    if (await page.getByRole("button", { name: "切换为白天模式" }).isVisible())
      await page.getByRole("button", { name: "切换为白天模式" }).click();
    await page.getByRole("button", { name: "切换为夜间模式" }).click();
    await nav.getByRole("button", { name: "资源库", exact: true }).click();
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page
      .getByRole("navigation", { name: "片库切换" })
      .getByRole("button", { name: "其他资源" })
      .click();
    await page.getByTitle("Python 入门.mp4", { exact: true }).click();
    await page.screenshot({
      path: path.join(project, ".cache/workspace-dark.png"),
      fullPage: true,
    });
    await page.reload();
    await page.getByRole("button", { name: "切换为白天模式" }).waitFor();
    for (const viewport of [
      { width: 1280, height: 800 },
      { width: 320, height: 720 },
      { width: 1920, height: 1080 },
    ]) {
      await page.setViewportSize(viewport);
      assert.ok(
        await page.evaluate(
          () =>
            document.documentElement.scrollWidth <= innerWidth &&
            document.documentElement.scrollHeight <= innerHeight,
        ),
        `contained workspace at ${viewport.width}px`,
      );
    }
    await page.goto(
      `http://127.0.0.1:${server.address().port}/login?next=${encodeURIComponent("https://outside.invalid/")}`,
    );
    await page.getByLabel("管理密码", { exact: true }).fill("fixture-password");
    await page.getByRole("button", { name: "显示密码" }).click();
    assert.equal(
      await page.getByLabel("管理密码", { exact: true }).getAttribute("type"),
      "text",
    );
    await page.getByRole("button", { name: "隐藏密码" }).click();
    await page.getByRole("button", { name: "解锁工作台", exact: true }).click();
    await page
      .getByRole("alert")
      .filter({ hasText: "管理密码不正确" })
      .waitFor();
    const decipher = require("node:crypto").createDecipheriv(
      "aes-128-cbc",
      Buffer.from("1234123412ABCDEF"),
      Buffer.from("ABCDEF1234123412"),
    );
    assert.equal(
      Buffer.concat([
        decipher.update(Buffer.from(unlockPayload, "base64")),
        decipher.final(),
      ]).toString(),
      "fixture-password",
      "login keeps the existing encrypted wire protocol",
    );
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: path.join(project, ".cache/workspace-login-mobile.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "解锁工作台", exact: true }).click();
    await page.waitForURL(`http://127.0.0.1:${server.address().port}/files`);
    assert.deepEqual(errors, []);
    console.log(
      "Browser: resource/NSFW classification, readable master-detail layout, reanalysis, preview keyboard access, responsive layouts, task pagination/control/confirmations, settings drafts/error recovery, account login/2FA/permissions, routing and theme persistence passed.",
    );
  } finally {
    await browser.close();
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  server.closeAllConnections();
  server.close();
  process.exitCode = 1;
});
