import type { SyncTask } from './types';


export interface DashboardStatusCounts {
  active: number;
  paused: number;
  pending: number;
  completed: number;
  failed: number;
}

export interface BackendTaskState {
  state?: string;
  status?: string;
  upload_progress?: string | number;
}


export function statusFromBackendState(item: BackendTaskState): SyncTask['status'] {
  // 本机完整、仅云盘失败：与普通下载失败区分，便于只重传。
  if (item.state === 'upload_failed' || item.status === '上传失败') return 'upload_failed';
  if (item.state === 'failed' || item.status === '失败') return 'failed';
  // 终态优先于数值进度；完成记录的上传进度也是 100%，不能再次判成上传中。
  if (item.state === 'finished' || item.status === '已完成') return 'completed';
  if (item.state === 'paused' || item.status === '已暂停') return 'paused';
  if (item.status === '正在完成...') return 'syncing';
  // AI 分类阶段（后端 status 形如 "AI 抽帧中"）是下载后的处理步骤，按传输中显示。
  if (item.status && item.status.startsWith('AI ')) return 'syncing';
  if (item.status === '上传中' || Number(item.upload_progress) > 0) return 'uploading';
  if (item.status === '等待中') return 'pending';
  return 'downloading';
}


export function buildDashboardTasks(
  liveTasks: SyncTask[],
  terminalTasks: SyncTask[],
): SyncTask[] {
  const merged = new Map<string, SyncTask>();

  // 已结束记录先进入任务视图；若同一任务仍出现在实时流中，以实时状态为准，
  // 避免任务完成切换期间同时显示“传输中”和“已完成”两行。
  terminalTasks.forEach((task) => merged.set(task.id, task));
  liveTasks.forEach((task) => merged.set(task.id, task));

  return Array.from(merged.values()).sort((left, right) => {
    const leftTerminal =
      left.status === 'completed' || left.status === 'failed' || left.status === 'upload_failed';
    const rightTerminal =
      right.status === 'completed' || right.status === 'failed' || right.status === 'upload_failed';
    if (leftTerminal !== rightTerminal) return leftTerminal ? 1 : -1;
    if (left.status !== right.status) {
      // 失败类优先展示，上传失败略低于下载失败
      if (left.status === 'failed') return -1;
      if (right.status === 'failed') return 1;
      if (left.status === 'upload_failed') return -1;
      if (right.status === 'upload_failed') return 1;
    }
    return Date.parse(right.createdAt) - Date.parse(left.createdAt);
  });
}


export function countDashboardStatuses(tasks: SyncTask[]): DashboardStatusCounts {
  return tasks.reduce<DashboardStatusCounts>((counts, task) => {
    if (task.status === 'completed') counts.completed += 1;
    else if (task.status === 'failed' || task.status === 'upload_failed') counts.failed += 1;
    else if (task.status === 'paused') counts.paused += 1;
    else if (task.status === 'pending') counts.pending += 1;
    else counts.active += 1;
    return counts;
  }, {
    active: 0,
    paused: 0,
    pending: 0,
    completed: 0,
    failed: 0,
  });
}
