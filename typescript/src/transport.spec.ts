import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PARTIAL_ACCEPT_HEADER, Reason, doPush } from './dispatcher';
import { resolveConfig } from './config';

/**
 * The request doPush actually puts on the wire.
 *
 * Nothing verified this before: a grep for the partial-accept header found no
 * hit anywhere in this suite, so the single most important line of the
 * backpressure contract could have regressed with every test still green - and
 * the client would have silently reverted to all-or-nothing rejection, which a
 * client holding a large backlog can never get past. The same blind spot hid a
 * total-loss timestamp-serialisation defect in the Java SDK.
 */

interface Captured {
  url: string;
  headers: Record<string, string>;
  body: string;
}

function config() {
  return resolveConfig({
    ingestorUrl: 'http://ingestor.invalid',
    apiToken: 'tenant_x_secret',
    resourceId: 'resource-1',
    httpTimeout: 5000,
  });
}

/** Swaps in a fetch that records the request and replies with the given response. */
async function withFetch<R>(
  reply: { status: number; body: unknown; headers?: Record<string, string> },
  run: (captured: Captured[]) => Promise<R>
): Promise<R> {
  const captured: Captured[] = [];
  const original = globalThis.fetch;

  globalThis.fetch = (async (url: string, init: RequestInit) => {
    captured.push({
      url: String(url),
      headers: (init.headers ?? {}) as Record<string, string>,
      body: String(init.body),
    });

    return {
      status: reply.status,
      ok: reply.status >= 200 && reply.status < 300,
      json: async () => reply.body,
      headers: {
        get: (name: string) => reply.headers?.[name] ?? null,
      },
    };
  }) as unknown as typeof globalThis.fetch;

  try {
    return await run(captured);
  } finally {
    globalThis.fetch = original;
  }
}

test('every push carries the partial-accept header', async () => {
  await withFetch({ status: 200, body: { accepted: 1 } }, async (captured) => {
    await doPush(config(), '/api/v1/metrics/push', { resource_id: 'resource-1' }, 1);

    assert.equal(captured.length, 1);
    assert.equal(captured[0].url, 'http://ingestor.invalid/api/v1/metrics/push');
    assert.equal(
      captured[0].headers[PARTIAL_ACCEPT_HEADER],
      '1',
      'without this header the ingestor rejects a batch that does not fit whole'
    );
    assert.equal(captured[0].headers.Authorization, 'Bearer tenant_x_secret');
    assert.equal(captured[0].headers['Content-Type'], 'application/json');
  });
});

test('a Date reaches the server as an ISO-8601 string, never a number', async () => {
  await withFetch({ status: 200, body: { accepted: 1 } }, async (captured) => {
    const payload = {
      resource_id: 'resource-1',
      metrics: [{ name: 'm', type: 'gauge', value: 1, timestamp: new Date('2026-10-13T09:00:00.123Z') }],
    };

    await doPush(config(), '/api/v1/metrics/push', payload, 1);

    const body = JSON.parse(captured[0].body) as {
      metrics: Array<{ timestamp: unknown }>;
    };
    const timestamp = body.metrics[0].timestamp;

    assert.equal(
      typeof timestamp,
      'string',
      "Go's time.Time rejects a number with \"input is not a JSON string\", which the ingestor " +
        'returns as 400 and this client then drops as permanent'
    );
    assert.equal(timestamp, '2026-10-13T09:00:00.123Z');
  });
});

test('a partial acceptance keeps the tail and reads retry_after_ms', async () => {
  await withFetch(
    {
      status: 200,
      body: { accepted: 40, rejected: 60, reason: 'rate_limit', retry_after_ms: 12_000 },
    },
    async () => {
      const outcome = await doPush(config(), '/api/v1/metrics/push', {}, 100);

      assert.equal(outcome.accepted, 40);
      assert.equal(outcome.retry, true);
      assert.equal(outcome.retryAfterMs, 12_000);
      assert.equal(outcome.reason, Reason.RateLimit);
    }
  );
});

test('a throttle without a body falls back to the Retry-After header', async () => {
  await withFetch(
    { status: 429, body: {}, headers: { 'Retry-After': '30' } },
    async () => {
      const outcome = await doPush(config(), '/api/v1/metrics/push', {}, 10);

      assert.equal(outcome.retry, true);
      assert.equal(outcome.retryAfterMs, 30_000);
    }
  );
});

test('the published allowance is read off the response', async () => {
  await withFetch(
    {
      status: 200,
      body: { accepted: 10 },
      headers: { 'X-RateLimit-Limit': '1000', 'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '18' },
    },
    async () => {
      const outcome = await doPush(config(), '/api/v1/metrics/push', {}, 10);

      assert.equal(outcome.accepted, 10);
      assert.equal(
        outcome.allowanceSpent,
        true,
        'the ingestor publishes these so a client can pace itself rather than be rejected'
      );
      assert.equal(outcome.resetAfterMs, 18_000);
    }
  );
});

test('an unenforced quota leaves pacing alone', async () => {
  await withFetch({ status: 200, body: { accepted: 10 } }, async () => {
    const outcome = await doPush(config(), '/api/v1/metrics/push', {}, 10);

    assert.equal(outcome.allowanceSpent, false);
    assert.equal(outcome.resetAfterMs, 0);
  });
});

test('an unacceptable payload is permanent, not retried forever', async () => {
  await withFetch({ status: 400, body: { error: 'invalid request body' } }, async () => {
    const outcome = await doPush(config(), '/api/v1/metrics/push', {}, 10);

    assert.equal(outcome.retry, false);
    assert.equal(outcome.error?.permanent, true);
    assert.equal(outcome.error?.status, 400);
  });
});

test('a transport failure keeps the data', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () => {
    throw new Error('ECONNREFUSED');
  }) as unknown as typeof globalThis.fetch;

  try {
    const outcome = await doPush(config(), '/api/v1/metrics/push', {}, 10);

    assert.equal(outcome.retry, true, 'a connection problem is transient; the data is kept');
    assert.equal(outcome.reason, Reason.Transport);
  } finally {
    globalThis.fetch = original;
  }
});

test('unserialisable data is dropped rather than retried forever', async () => {
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;

  const outcome = await doPush(config(), '/api/v1/metrics/push', cyclic, 1);

  assert.equal(outcome.retry, false);
  assert.equal(outcome.reason, Reason.InvalidPayload);
  assert.equal(outcome.error?.permanent, true);
});
