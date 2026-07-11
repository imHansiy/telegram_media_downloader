import assert from 'node:assert/strict';

import { buildWebdavResourceUrl, isPdfFile } from '../../webui/src/webdavPreview';


const file = {
  remotePath: '/Telegram Backup/频道/视频 #1.mp4',
  name: '视频 #1.mp4',
};

assert.equal(
  buildWebdavResourceUrl(file),
  '/api/webdav/preview?path=%2FTelegram+Backup%2F%E9%A2%91%E9%81%93%2F%E8%A7%86%E9%A2%91+%231.mp4',
);
assert.equal(
  buildWebdavResourceUrl(file, true),
  '/api/webdav/preview?path=%2FTelegram+Backup%2F%E9%A2%91%E9%81%93%2F%E8%A7%86%E9%A2%91+%231.mp4&download=1',
);
assert.equal(isPdfFile({ name: 'manual.PDF' }), true);
assert.equal(isPdfFile({ name: 'manual.docx' }), false);

console.log('webdav preview model: passed');
