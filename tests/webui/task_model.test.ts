import assert from 'node:assert/strict';

import {
  buildDashboardTasks,
  countDashboardStatuses,
  statusFromBackendState,
} from '../../webui/src/taskModel';
import type { SyncTask } from '../../webui/src/types';


function task(id: string, status: SyncTask['status']): SyncTask {
  return {
    id,
    type: 'video',
    sourceId: '-1001',
    sourceName: 'source',
    filename: `${id}.mp4`,
    sizeBytes: 1024,
    createdAt: '2026-07-11T10:00:00.000Z',
    downloadProgress: status === 'completed' ? 100 : 50,
    uploadProgress: status === 'completed' ? 100 : 0,
    status,
    speedKb: 0,
    remotePath: `/telegram/${id}.mp4`,
  };
}


const dashboardTasks = buildDashboardTasks(
  [task('active', 'downloading'), task('failed', 'failed')],
  [task('completed', 'completed')],
);

assert.deepEqual(
  dashboardTasks.map((item) => item.id),
  ['active', 'failed', 'completed'],
  '仪表盘必须同时显示进行中、失败和成功任务',
);
assert.deepEqual(countDashboardStatuses(dashboardTasks), {
  active: 1,
  paused: 0,
  pending: 0,
  completed: 1,
  failed: 1,
});
assert.equal(
  statusFromBackendState({ status: '已完成', upload_progress: '100.0' }),
  'completed',
  '后端已完成状态必须优先于 100% 上传进度',
);

console.log('task model regression: passed');
