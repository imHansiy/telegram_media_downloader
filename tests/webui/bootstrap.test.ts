import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fetchBootstrap } from '../../webui/src/bootstrap';

test('hung response body times out rather than leaving default settings editable', async () => {
  let signal: AbortSignal | undefined;
  const transport = (async (_url: unknown, options: RequestInit) => {
    signal = options.signal as AbortSignal;
    return { ok: true, json: () => new Promise(() => {}) };
  }) as typeof fetch;
  await assert.rejects(fetchBootstrap(transport, 15), /加载超时/);
  assert.equal(signal?.aborted, true);
});

test('valid response preserves saved configuration and account list', async () => {
  const payload = { config: { fixture: true }, account: { accounts: [{ id: 'one' }, { id: 'two' }] } };
  const transport = (async () => ({ ok: true, json: async () => payload })) as typeof fetch;
  assert.deepEqual(await fetchBootstrap(transport, 100), payload);
});

test('missing config is a load error, not an empty editable configuration', async () => {
  const transport = (async () => ({ ok: true, json: async () => ({ account: {} }) })) as typeof fetch;
  await assert.rejects(fetchBootstrap(transport, 100), /不完整/);
});
