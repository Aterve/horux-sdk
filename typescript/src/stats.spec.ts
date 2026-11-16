import { test } from 'node:test';
import assert from 'node:assert/strict';

import { MetricsClient } from './metrics-client';
import { LogsClient } from './logs-client';

/**
 * The server-side stats response, as this client now reads it.
 *
 * The ingestor returns the `writer` block only to the internal service key: its
 * counters - total written, batches written, write errors - span *every* tenant
 * and used to be handed to any tenant bearer token. This client dereferenced
 * `data.writer.metrics_written` unconditionally, so against the current server
 * `stats()` threw a TypeError on a missing property rather than returning
 * anything.
 */

interface Reply {
  status: number;
  body: unknown;
}

/** Swaps in a fetch that replies with the given stats response. */
async function withStatsResponse<R>(reply: Reply, run: () => Promise<R>): Promise<R> {
  const original = globalThis.fetch;

  globalThis.fetch = (async () => ({
    status: reply.status,
    ok: reply.status >= 200 && reply.status < 300,
    json: async () => reply.body,
  })) as unknown as typeof globalThis.fetch;

  try {
    return await run();
  } finally {
    globalThis.fetch = original;
  }
}

function clientConfig() {
  return {
    ingestorUrl: 'http://ingestor.invalid',
    apiToken: 'tenant_x_secret',
    resourceId: 'resource-1',
    flushInterval: 3_600_000,
  };
}


// The ingestor now returns the `writer` block only to the internal service key,
// because those counters spanned every tenant. This client read
// `data.writer.metrics_written` unconditionally, so against the current server
// stats() threw TypeError on a missing property rather than returning anything.
test('stats reads the buffer block and does not require a writer block', async () => {
  await withStatsResponse(
    {
      status: 200,
      body: {
        buffer: {
          queued_metrics: 12,
          capacity: 1000,
          utilization_percent: 1.2,
          dropped_metrics: 7,
          dropped_batches: 1,
        },
      },
    },
    async () => {
      const client = new MetricsClient(clientConfig());

      const stats = await client.stats();

      assert.deepEqual(stats, {
        buffer: {
          queuedMetrics: 12,
          capacity: 1000,
          utilizationPercent: 1.2,
          droppedMetrics: 7,
          droppedBatches: 1,
        },
      });

      await client.close();
    }
  );
});

test('logs stats reads the buffer block and does not require a writer block', async () => {
  await withStatsResponse(
    {
      status: 200,
      body: {
        buffer: { queued_logs: 3, capacity: 500, utilization_percent: 0.6, dropped_logs: 2 },
      },
    },
    async () => {
      const client = new LogsClient(clientConfig());

      const stats = await client.stats();

      assert.equal(stats.buffer.queuedLogs, 3);
      assert.equal(stats.buffer.droppedLogs, 2);
      assert.equal(stats.buffer.droppedBatches, 0, 'a field the server omits reads as 0, not NaN');

      await client.close();
    }
  );
});

// An older ingestor, or one that omits the block entirely, must not throw.
test('stats survives a response with no buffer block at all', async () => {
  await withStatsResponse({ status: 200, body: {} }, async () => {
    const client = new MetricsClient(clientConfig());

    const stats = await client.stats();
    assert.equal(stats.buffer.queuedMetrics, 0);

    await client.close();
  });
});
