/* Synthetic SSE workflow transitions; no real Telegram or model requests. */
const assert = require("node:assert/strict");

exports.runTaskProgress = async (page, tasks, broadcast) => {
  const saved = tasks.map((task) => ({ ...task }));
  const viewport = page.viewportSize();
  let succeeded = false;
  const fixture = (id, phase, phase_label) => ({
    chat: "fixture", id, filename: `阶段_${id}.mp4`, total_size: "100 MB",
    download_progress: "100", upload_progress: phase === "analyzing" ? "0" : "100",
    state: phase === "completed" ? "finished" : "running",
    status: phase_label, phase, phase_label, ai_status: "分析个人实拍",
    download_speed: "2 MB/s", upload_speed: "2 MB/s",
    created_at: "2026-09-06 08:00:00", remote_path: "/fixture.mp4",
  });
  try {
    tasks.splice(0, tasks.length,
      fixture("1", "analyzing", "AI分析中"),
      fixture("2", "finalizing", "确认上传"),
      { ...fixture("3", "completed", "已完成"), download_progress: "Finishing...", upload_progress: "Finishing..." },
    );
    broadcast();
    const row = (id) => page.getByRole("article", { name: `阶段_${id}.mp4`, exact: true });
    await row("1").waitFor();
    assert.equal(await row("1").locator(".status-pill").innerText(), "AI分析中");
    assert.equal(await row("1").getByRole("progressbar", { name: "下载", exact: true }).getAttribute("aria-valuenow"), "100");
    assert.equal(await row("1").getByRole("progressbar", { name: "上传", exact: true }).getAttribute("aria-valuenow"), "0");
    assert.equal(await row("1").getByRole("progressbar", { name: "上传", exact: true }).getAttribute("aria-valuetext"), "待上传");
    assert.equal(await row("2").locator(".status-pill").innerText(), "确认上传");
    for (const label of ["下载", "上传"]) {
      assert.equal(await row("3").getByRole("progressbar", { name: label, exact: true }).getAttribute("aria-valuenow"), "100");
    }
    assert.equal(await page.locator(".transfer-speed strong").innerText(), "0 KB/s");
    assert.equal(await row("1").locator(".task-speed").innerText(), "—");
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    tasks[0] = { ...tasks[0], phase: "uploading", phase_label: "上传中", upload_progress: "40", download_speed: "0B/s", upload_speed: "1 KB/s" };
    broadcast();
    await row("1").getByText("上传中", { exact: true }).waitFor();
    assert.equal(await row("1").getByRole("progressbar", { name: "上传", exact: true }).getAttribute("aria-valuenow"), "40");
    assert.equal(await row("1").locator(".task-speed").innerText(), "1 KB/s");
    tasks[0] = { ...tasks[0], state: "finished", phase: "completed", phase_label: "已完成", status: "已完成", upload_progress: "100" };
    broadcast();
    await row("1").getByText("已完成", { exact: true }).waitFor();
    assert.equal(await row("1").locator(".task-speed").innerText(), "—");
    console.log("Task phases: analysis/pending upload, acknowledgement, completion, stale speeds and mobile SSE transitions passed.");
    succeeded = true;
  } finally {
    tasks.splice(0, tasks.length, ...saved);
    broadcast();
    await page.setViewportSize(viewport);
    const visibleSaved = saved.find((task) => !["failed", "upload_failed", "finished", "completed"].includes(task.state)) || saved[0];
    if (succeeded) await page.getByRole("article", { name: visibleSaved.filename, exact: true }).waitFor();
  }
};
