import { hasCategoryOnlySynopsis, ResourceAnalysis, statusLabels } from "./model";

// One projection for the card, hover and details. The server owns validation;
// a historical category decision is not a validated reader synopsis.
export function synopsisView(analysis: ResourceAnalysis) {
  const videoComplete = analysis.resource_kind === "video" && analysis.status === "complete";
  const categoryOnly = hasCategoryOnlySynopsis(analysis);
  const ready = videoComplete && !categoryOnly && analysis.summary_quality?.status === "ready" && (analysis.summary_quality?.version ?? 0) >= 5 && (analysis.summary_quality?.audit?.version ?? 0) >= 2 && analysis.summary_quality?.audit?.status === "passed" && (analysis.narrative?.version ?? 0) >= 2 && !!analysis.narrative?.events.length;
  const legacy = videoComplete && (analysis.summary_quality?.version ?? 0) < 5;
  const needsUpdate = videoComplete && !ready;
  const stateLabel = needsUpdate
    ? categoryOnly ? "分类完成·简介待完善" : legacy ? "分类完成·旧版简介" : "分类完成·剧情待确认"
    : statusLabels[analysis.status];
  const observation = analysis.status !== "complete" && analysis.content_description;
  return {
    videoComplete, ready, needsUpdate, stateLabel,
    heading: videoComplete
      ? ready ? analysis.narrative?.mode === "story" ? "剧情梗概" : "片段内容" : categoryOnly ? "简介待完善" : legacy ? "剧情总结待更新" : "剧情待确认"
      : observation ? "内容观察（分类未确认）" : "内容概览",
    text: needsUpdate
      ? categoryOnly
        ? "分类已识别，但现有简介只有通用活动标签，缺少本资源的具体信息。请更新简介；证据不足时不会编造剧情。"
        : legacy
        ? "已有简介尚未通过事件与剧情核验。请使用更新简介，原有分类依据保留。"
        : "现有证据不足以还原剧情或活动过程。可补充采样后更新简介，原有分类依据保留。"
      : observation || analysis.summary || "尚未分析此资源。",
  };
}
