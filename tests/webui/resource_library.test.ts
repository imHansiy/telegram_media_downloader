import assert from "node:assert/strict";
import { synopsisView } from "../../webui/src/library/synopsis";
import {
  coverUrl,
  filterDirectory,
  parentPath,
  previewUrl,
  ResourceEntry,
} from "../../webui/src/library/model";

const path = "来源 #1/nsfw/三维与游戏动画/演示 #1.mp4";
const url = new URL(previewUrl("/Telegram Backup/", path), "http://localhost");
assert.equal(url.searchParams.get("path"), "/Telegram Backup/" + path);
assert.equal(
  new URL(
    coverUrl("/Telegram Backup/", path),
    "http://localhost",
  ).searchParams.get("path"),
  "/Telegram Backup/" + path + ".cover.jpg",
);
assert.equal(
  new URL(
    coverUrl("", "folder/clip & 100%.mkv"),
    "http://localhost",
  ).searchParams.get("path"),
  "/folder/clip & 100%.mkv.cover.jpg",
);
assert.equal(parentPath(path), "来源 #1/nsfw/三维与游戏动画");
assert.equal(parentPath("root.mp4"), "");
assert.equal(
  new URL(previewUrl("", path, true), "http://localhost").searchParams.get(
    "download",
  ),
  "1",
);

function entry(
  name: string,
  kind: string,
  status: string,
  summary = "课程知识点",
): ResourceEntry {
  return {
    name,
    path: name,
    size: name.length,
    modified: "",
    content_type: "",
    analysis: { resource_kind: kind, status, summary, tags: ["Python"] },
  } as ResourceEntry;
}
const entries = [
  entry("lesson.mp4", "video", "complete"),
  entry("notes.pdf", "document", "complete"),
  entry("old.mp4", "video", "legacy"),
];
assert.deepEqual(
  filterDirectory(entries, "Python", "video", "complete", "name").map(
    (item) => item.name,
  ),
  ["lesson.mp4"],
);
assert.deepEqual(
  filterDirectory(entries, "", "", "review", "name").map((item) => item.name),
  ["old.mp4"],
);
assert.equal(filterDirectory(entries, "不存在", "", "", "name").length, 0);
assert.equal(
  entries[0].name,
  "lesson.mp4",
  "filtering must not sort the original list in place",
);
const pending = entry("picture.jpg", "image", "needs_review", "分类待确认");
pending.analysis.content_description = "可见蓝色几何图形。";
assert.deepEqual(
  filterDirectory([pending], "几何图形", "image", "review", "name"),
  [pending],
);
assert.equal(pending.analysis.status, "needs_review");
const video = entry("quality.mp4", "video", "complete", "一段独立内容概览。");
assert.equal(synopsisView(video.analysis).needsUpdate, true);
assert.ok(synopsisView(video.analysis).stateLabel.includes("旧版简介"));
assert.notEqual(synopsisView(video.analysis).text, video.analysis.summary);
video.analysis.summary_quality = { version: 1, status: "ready", issues: [], evidence_catalog: [] };
assert.equal(synopsisView(video.analysis).needsUpdate, true, "v1 scene summary is not a narrative");
video.analysis.summary_quality.version = 2;
assert.equal(synopsisView(video.analysis).needsUpdate, true, "v2 references alone do not prove factual grounding");
video.analysis.summary_quality.version = 3;
video.analysis.summary_quality.audit = { version: 2, status: "passed", issues: [] };
video.analysis.narrative = { version: 1, mode: "story", issues: [], events: [
  { id: "event_1", phase: "setup", action: "旅客发现车票遗失。", start: 0, end: 10, evidence_refs: ["frame_1"] },
] };
assert.equal(synopsisView(video.analysis).ready, false, "v3 self-audited micro-actions are still legacy");
video.analysis.summary_quality.version = 4;
video.analysis.narrative.version = 2;
assert.equal(synopsisView(video.analysis).ready, false, "v4 free-form activity cores are legacy");
video.analysis.summary_quality.version = 5;
video.analysis.narrative.version = 1;
assert.equal(synopsisView(video.analysis).ready, false, "new quality requires the event-level contract");
video.analysis.narrative.version = 2;
assert.equal(synopsisView(video.analysis).text, video.analysis.summary);
assert.equal(synopsisView(video.analysis).needsUpdate, false);
assert.equal(synopsisView(video.analysis).heading, "剧情梗概");
video.analysis.narrative.mode = "activity";
assert.equal(synopsisView(video.analysis).heading, "片段内容");
const genericVideo = structuredClone(video);
genericVideo.analysis.category = "adult";
genericVideo.analysis.summary = "参与者进行成人亲密互动。";
assert.equal(synopsisView(genericVideo.analysis).ready, false, "a saved v5 label is not a useful synopsis");
assert.equal(synopsisView(genericVideo.analysis).heading, "简介待完善");
assert.ok(synopsisView(genericVideo.analysis).text.includes("通用活动标签"));
assert.notEqual(synopsisView(genericVideo.analysis).text, genericVideo.analysis.summary);
assert.deepEqual(filterDirectory([genericVideo], "", "", "review", "name"), [genericVideo]);
assert.deepEqual(filterDirectory([genericVideo], "", "", "complete", "name"), []);
assert.equal(genericVideo.analysis.summary_quality!.status, "ready", "presentation must not mutate saved analysis");
video.analysis.summary_quality.status = "review";
assert.ok(synopsisView(video.analysis).stateLabel.includes("剧情待确认"));
assert.deepEqual(filterDirectory([video], "", "", "review", "name"), [video]);
assert.deepEqual(filterDirectory([video], "", "", "complete", "name"), []);
assert.equal(synopsisView(pending.analysis).text, pending.analysis.content_description);
assert.equal(synopsisView(entries[1].analysis).needsUpdate, false);
console.log("resource library model: passed");
