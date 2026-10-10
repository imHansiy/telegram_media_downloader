/** Bound both network and response-body waits; never substitute empty config. */
export async function fetchBootstrap<T>(transport: typeof fetch = fetch, timeoutMs = 15000): Promise<T> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error('账户和配置加载超时。数据尚未读取，已暂停编辑；请重试，或在 Chrome 中打开本地项目。'));
      controller.abort();
    }, timeoutMs);
  });
  try {
    return await Promise.race([
      (async () => {
        const response = await transport('/api/bootstrap', { signal: controller.signal, cache: 'no-store' });
        if (!response.ok) throw new Error('账户和配置加载失败，请重新登录后重试。');
        const payload = await response.json();
        if (!payload || !payload.config || typeof payload.config !== 'object' || Array.isArray(payload.config) || !payload.account || typeof payload.account !== 'object') {
          throw new Error('服务器返回的账户或配置不完整，已暂停编辑。');
        }
        return payload as T;
      })(),
      deadline,
    ]);
  } finally {
    clearTimeout(timer!);
  }
}
