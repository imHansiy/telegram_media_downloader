import type { CompletedFile } from './types';


export function buildWebdavResourceUrl(
  file: Pick<CompletedFile, 'remotePath'>,
  download = false,
): string {
  const query = new URLSearchParams({ path: file.remotePath });
  if (download) query.set('download', '1');
  return `/api/webdav/preview?${query.toString()}`;
}


export function isPdfFile(file: Pick<CompletedFile, 'name'>): boolean {
  return file.name.toLowerCase().endsWith('.pdf');
}
