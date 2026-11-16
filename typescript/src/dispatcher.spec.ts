import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  Backoff,
  BoundedQueue,
  Dispatcher,
  PushError,
  PushOutcome,
  Reason,
  interpretPush,
  parseRetryAfterHeader,
  readQuotaHeaders,
  MAX_CHUNKS_PER_FLUSH,
  RATE_LIMIT_REMAINING_HEADER,
  RATE_LIMIT_RESET_HEADER,
} from './dispatcher';
import { resolveConfig } from './config';

function testConfig(maxBatchSize: number, maxBufferSize: number, overrides: Record<string, unknown> = {}) {
  return resolveConfig({
    ingestorUrl: 'http://ingestor.invalid',
    apiToken: 'tenant_x_secret',
    resourceId: 'resource-1',
    flushInterval: 3_600_000,
    maxBatchSize,
    maxBufferSize,
    minRetryBackoff: 1000,
    maxRetryBackoff: 60_000,
    ...overrides,
  });
}

function accepted(n: number): PushOutcome {
  return { accepted: n, retry: false, shed: false, retryAfterMs: 0, reason: '' };
}

/** Builds a dispatcher plus handles on its internals, for deterministic drains. */
function harness<T>(config: ReturnType<typeof testConfig>, send: (items: T[]) => Promise<PushOutcome>) {
  const dispatcher = new Dispatcher<T>(config, 'metrics', send);
  const internals = dispatcher as unknown as {
    queue: BoundedQueue<T>;
    drain(final: boolean): Promise<PushError | undefined>;
  };

  return { dispatcher, queue: internals.queue, drain: (final: boolean) => internals.drain(final) };
}

test('queue drops the oldest items when full', () => {
  const q = new BoundedQueue<number>(3, 'oldest');
  for (let i = 1; i <= 5; i++) q.add(i);

  assert.equal(q.length, 3);
  assert.equal(q.dropped, 2);
  assert.deepEqual(q.take(3), [3, 4, 5]);
});

test('queue drops the newest items when configured', () => {
  const q = new BoundedQueue<number>(3, 'newest');
  for (let i = 1; i <= 5; i++) q.add(i);

  assert.deepEqual(q.take(3), [1, 2, 3]);
  assert.equal(q.dropped, 2);
});

test('queue requeues unsent items in order', () => {
  const q = new BoundedQueue<number>(10, 'oldest');
  q.add(3);
  q.requeue([1, 2]);

  assert.deepEqual(q.take(3), [1, 2, 3]);
});

test('queue requeue respects the bound', () => {
  const q = new BoundedQueue<number>(3, 'oldest');
  q.add(8);
  q.add(9);

  assert.equal(q.requeue([1, 2, 3, 4]), 3);
  assert.deepEqual(q.take(3), [4, 8, 9]);
});

test('backoff honours the server hint', () => {
  const b = new Backoff(1000, 300_000);
  const now = Date.now();

  const delay = b.fail(10_000, now);

  assert.ok(delay >= 10_000, `delay ${delay} should be at least the hint`);
  assert.equal(b.ready(now + 9_000), false);
  assert.equal(b.ready(now + delay), true);
});

test('backoff grows exponentially up to the cap', () => {
  const b = new Backoff(1000, 4000);
  const now = Date.now();

  for (let i = 0; i < 10; i++) {
    assert.ok(b.fail(0, now) <= 4000);
  }
});

test('backoff resets on success', () => {
  const b = new Backoff(1000, 60_000);
  b.fail(30_000);
  b.succeed();

  assert.equal(b.ready(), true);
  assert.equal(b.remaining(), 0);
});

test('interpretPush reports a full acceptance', () => {
  const out = interpretPush(200, undefined, { accepted: 100, rejected: 0 }, 100);

  assert.equal(out.accepted, 100);
  assert.equal(out.retry, false);
  assert.equal(out.shed, false);
  assert.equal(out.error, undefined);
});

// The case the old client could not express: the server took part of the batch
// and the rest has to stay queued.
test('interpretPush keeps the tail on a partial acceptance', () => {
  const out = interpretPush(
    200,
    undefined,
    { accepted: 340, rejected: 660, reason: 'rate_limit', retry_after_ms: 12_000 },
    1000
  );

  assert.equal(out.accepted, 340);
  assert.equal(out.retry, true);
  assert.equal(out.retryAfterMs, 12_000);
  assert.equal(out.reason, Reason.RateLimit);
});

test('interpretPush retries a rate limit and reads Retry-After', () => {
  const headers = new Headers({ 'Retry-After': '30' });

  const out = interpretPush(429, headers, { accepted: 0, rejected: 500, reason: 'rate_limit' }, 500);

  assert.equal(out.retry, true);
  assert.equal(out.shed, false);
  assert.equal(out.retryAfterMs, 30_000);
});

// Storage quota does not clear by waiting, so the data is shed rather than
// held: holding it is what grew the buffer until the host process died.
test('interpretPush sheds on a storage quota', () => {
  const out = interpretPush(429, undefined, { reason: 'storage_quota', retry_after_ms: 300_000 }, 500);

  assert.equal(out.shed, true);
  assert.equal(out.retry, false);
  assert.equal(out.retryAfterMs, 300_000);
});

test('interpretPush applies a floor to the shed window', () => {
  const out = interpretPush(429, undefined, { reason: 'storage_quota' }, 10);

  assert.ok(out.retryAfterMs >= 60_000);
});

test('interpretPush drops payloads that can never be accepted', () => {
  for (const status of [400, 413, 422]) {
    const out = interpretPush(status, undefined, { error: 'nope' }, 10);

    assert.equal(out.retry, false, `status ${status}`);
    assert.ok(out.error instanceof PushError);
    assert.equal(out.error?.permanent, true, `status ${status}`);
  }
});

test('interpretPush keeps data for recoverable failures', () => {
  for (const status of [401, 403, 404, 500, 502, 503]) {
    const out = interpretPush(status, undefined, undefined, 10);

    assert.equal(out.retry, true, `status ${status}`);
  }
});

test('interpretPush caps accepted at the batch size', () => {
  const out = interpretPush(200, undefined, { accepted: 9999 }, 10);

  assert.equal(out.accepted, 10);
});

test('parseRetryAfterHeader reads both forms', () => {
  assert.equal(parseRetryAfterHeader('45'), 45_000);
  assert.equal(parseRetryAfterHeader(null), 0);
  assert.equal(parseRetryAfterHeader('-5'), 0);
  assert.equal(parseRetryAfterHeader('garbage'), 0);

  const future = new Date(Date.now() + 90_000).toUTCString();
  const parsed = parseRetryAfterHeader(future);
  assert.ok(parsed > 80_000 && parsed <= 90_000, `parsed ${parsed}`);
});

// A backlog is sent in batches of maxBatchSize, never as one oversized request.
// Sending the whole buffer in a single request is how a stalled client grew a
// payload past the ingestor's 10MB body limit and wedged permanently.
test('drain sends in batches', async () => {
  const sizes: number[] = [];
  const h = harness<number>(testConfig(10, 100), async (items) => {
    sizes.push(items.length);
    return accepted(items.length);
  });

  for (let i = 0; i < 25; i++) h.queue.add(i);

  assert.equal(await h.drain(true), undefined);
  assert.deepEqual(sizes, [10, 10, 5]);
  assert.equal(h.queue.length, 0);

  await h.dispatcher.close();
});

// On a partial acceptance the unaccepted tail goes back to the front of the
// queue and the cycle stops: the client makes exactly the progress its
// allowance permits instead of hammering for the rest.
test('drain keeps the tail and stops on a partial acceptance', async () => {
  const sizes: number[] = [];
  const h = harness<number>(testConfig(10, 100), async (items) => {
    sizes.push(items.length);
    return {
      accepted: 4,
      retry: true,
      shed: false,
      retryAfterMs: 10_000,
      reason: Reason.RateLimit,
      error: new PushError(200, Reason.RateLimit, 'throttled'),
    };
  });

  for (let i = 0; i < 25; i++) h.queue.add(i);

  const error = await h.drain(true);

  assert.ok(error instanceof PushError);
  assert.deepEqual(sizes, [10], 'exactly one attempt');
  assert.equal(h.queue.length, 21, '6 unsent plus the untouched 15');
  assert.deepEqual(h.queue.take(6), [4, 5, 6, 7, 8, 9], 'unsent items keep their place');

  await h.dispatcher.close();
});

// After a rejection the client waits. Without this, a throttled client retried
// on its normal cadence and - because a full buffer also triggered a flush -
// ended up sending one request per recorded datapoint.
test('drain respects the backoff after a rejection', async () => {
  const sizes: number[] = [];
  const h = harness<number>(testConfig(10, 100), async (items) => {
    sizes.push(items.length);
    return {
      accepted: 0,
      retry: true,
      shed: false,
      retryAfterMs: 30_000,
      reason: Reason.RateLimit,
      error: new PushError(429, Reason.RateLimit, 'throttled'),
    };
  });

  for (let i = 0; i < 5; i++) h.queue.add(i);

  await h.drain(false);
  assert.equal(sizes.length, 1);

  for (let i = 0; i < 5; i++) await h.drain(false);
  assert.equal(sizes.length, 1, 'no attempts during the backoff window');
  assert.ok(h.dispatcher.stats().retryBackoffMs >= 30_000);

  await h.dispatcher.close();
});

test('drain drops a permanently rejected batch and continues', async () => {
  const sizes: number[] = [];
  const drops: number[] = [];
  const config = testConfig(10, 100, {
    onDrop: (_stream: string, count: number) => drops.push(count),
  });

  const h = harness<number>(config, async (items) => {
    sizes.push(items.length);
    if (items.length === 10) {
      return {
        accepted: 0,
        retry: false,
        shed: false,
        retryAfterMs: 0,
        reason: Reason.InvalidPayload,
        error: new PushError(400, Reason.InvalidPayload, 'nope', true),
      };
    }
    return accepted(items.length);
  });

  for (let i = 0; i < 15; i++) h.queue.add(i);

  const error = await h.drain(true);

  assert.ok(error instanceof PushError);
  assert.deepEqual(sizes, [10, 5], 'bad batch dropped, next one sent');
  assert.equal(h.queue.length, 0);
  assert.equal(h.queue.dropped, 10);
  assert.deepEqual(drops, [10]);

  await h.dispatcher.close();
});

// Over a storage quota there is nowhere for the data to go, so the client sheds
// it at the door rather than growing a buffer for a condition that needs a plan
// change to clear.
test('a storage quota sheds instead of buffering', async () => {
  const sizes: number[] = [];
  const h = harness<number>(testConfig(10, 100), async (items) => {
    sizes.push(items.length);
    return {
      accepted: 0,
      retry: false,
      shed: true,
      retryAfterMs: 300_000,
      reason: Reason.StorageQuota,
      error: new PushError(429, Reason.StorageQuota, 'over quota'),
    };
  });

  for (let i = 0; i < 30; i++) h.queue.add(i);

  await h.drain(true);

  assert.equal(h.queue.length, 0, 'everything shed');
  assert.equal(h.dispatcher.stats().shedding, true);

  for (let i = 0; i < 50; i++) h.dispatcher.add(i);
  assert.equal(h.queue.length, 0, 'new data dropped at the door while shedding');

  await h.drain(false);
  assert.equal(sizes.length, 1, 'no further attempts during the shed window');

  await h.dispatcher.close();
});

// Recording never touches the network, no matter how full the buffer is.
test('add never sends by itself', async () => {
  const sizes: number[] = [];
  const h = harness<number>(testConfig(1000, 2000), async (items) => {
    sizes.push(items.length);
    return accepted(items.length);
  });

  for (let i = 0; i < 500; i++) h.dispatcher.add(i);

  assert.deepEqual(sizes, [], 'add() must not perform I/O');
  assert.equal(h.queue.length, 500);

  await h.dispatcher.close();
});

test('add bounds the buffer and reports drops', async () => {
  let dropped = 0;
  const config = testConfig(10, 20, {
    onDrop: (_stream: string, count: number, reason: string) => {
      assert.equal(reason, Reason.BufferFull);
      dropped += count;
    },
  });

  const h = harness<number>(config, async (items) => accepted(items.length));

  for (let i = 0; i < 100; i++) h.dispatcher.add(i);

  assert.equal(h.queue.length, 20, 'the bound holds');
  assert.equal(h.queue.dropped, 80);
  assert.ok(dropped > 0, 'onDrop was called');

  await h.dispatcher.close();
});

// The ingestor publishes X-RateLimit-Remaining and X-RateLimit-Reset on every
// push, explicitly so a client can pace itself instead of discovering the limit
// by being rejected. Nothing read them.
test('readQuotaHeaders reports a spent allowance and its reset window', () => {
  const headers = (remaining?: string, reset?: string): Headers => {
    const h = new Headers();
    if (remaining !== undefined) h.set(RATE_LIMIT_REMAINING_HEADER, remaining);
    if (reset !== undefined) h.set(RATE_LIMIT_RESET_HEADER, reset);
    return h;
  };

  assert.deepEqual(readQuotaHeaders(headers('500', '12')), {
    allowanceSpent: false,
    resetAfterMs: 0,
  });
  assert.deepEqual(readQuotaHeaders(headers('0', '12')), {
    allowanceSpent: true,
    resetAfterMs: 12_000,
  });
  assert.deepEqual(readQuotaHeaders(headers('0')), { allowanceSpent: true, resetAfterMs: 0 });
  assert.deepEqual(readQuotaHeaders(headers('-3', '5')), {
    allowanceSpent: true,
    resetAfterMs: 5000,
  });

  // Absent means the quota is not enforced for this tenant; garbage must not
  // be read as a spent allowance and stall the client.
  assert.deepEqual(readQuotaHeaders(headers()), { allowanceSpent: false, resetAfterMs: 0 });
  assert.deepEqual(readQuotaHeaders(headers('lots', 'soon')), {
    allowanceSpent: false,
    resetAfterMs: 0,
  });
  assert.deepEqual(readQuotaHeaders(undefined), { allowanceSpent: false, resetAfterMs: 0 });
});

// Pacing is not a failure: holding off because the server said the allowance is
// spent must not advance the exponential schedule, or a paced client ends up
// backing off as if it were broken.
test('backoff pace does not advance the attempt counter', () => {
  const backoff = new Backoff(1000, 60_000);
  const now = 1_000_000;

  for (let i = 0; i < 5; i++) backoff.pace(10_000, now);

  assert.equal(backoff.ready(now + 9000), false);
  assert.equal(backoff.ready(now + 11_000), true);

  // The next real failure still starts from the bottom of the schedule.
  assert.ok(backoff.fail(0, now) <= 1000);
});

// A client that has just used the last of its allowance stops for the window
// the server published, instead of sending the next batch and being told 429.
test('drain paces itself when the allowance is spent', async () => {
  let calls = 0;
  const h = harness<number>(testConfig(10, 1000), async (items) => {
    calls++;
    return {
      accepted: items.length,
      retry: false,
      shed: false,
      retryAfterMs: 0,
      allowanceSpent: true,
      resetAfterMs: 20_000,
      reason: '',
    };
  });

  for (let i = 0; i < 200; i++) h.queue.add(i);

  await h.drain(false);

  assert.equal(calls, 1, 'the cycle stops once the allowance is gone');
  assert.equal(h.queue.length, 190, 'the remainder is kept');
  assert.ok(h.dispatcher.stats().retryBackoffMs >= 19_000);

  await h.drain(false);
  assert.equal(calls, 1, 'no attempt inside the reset window');

  // A final pass ignores pacing: close() has one chance to deliver.
  await h.drain(true);
  assert.equal(h.queue.length, 0, 'a final pass attempts everything');
});

// A periodic cycle is bounded so a large backlog drains over several passes
// rather than monopolising one.
test('a periodic drain is bounded per cycle', async () => {
  let calls = 0;
  const h = harness<number>(testConfig(10, 1000), async (items) => {
    calls++;
    return accepted(items.length);
  });

  for (let i = 0; i < 1000; i++) h.queue.add(i);

  await h.drain(false);

  assert.equal(calls, MAX_CHUNKS_PER_FLUSH);
  assert.equal(h.queue.length, 1000 - MAX_CHUNKS_PER_FLUSH * 10);

  await h.dispatcher.close();
});

// A final pass must not stop at the cycle cap: with maxBufferSize above
// 16 x maxBatchSize, close() abandoned the remainder with no drop report, no
// error and no log.
test('a final drain keeps going past the cycle cap', async () => {
  let calls = 0;
  const h = harness<number>(testConfig(10, 1000), async (items) => {
    calls++;
    return accepted(items.length);
  });

  for (let i = 0; i < 1000; i++) h.queue.add(i);

  await h.drain(true);

  assert.equal(calls, 100, '1000 items in batches of 10');
  assert.equal(h.queue.length, 0, 'nothing abandoned');

  await h.dispatcher.close();
});

// --- Lifecycle --------------------------------------------------------------

// Nothing asserted that close() delivers what is pending, which is how the
// early return below survived.
test('close delivers pending data', async () => {
  const sent: number[] = [];
  const h = harness<number>(testConfig(10, 1000), async (items) => {
    sent.push(...items);
    return accepted(items.length);
  });

  for (let i = 0; i < 95; i++) h.queue.add(i);

  await h.dispatcher.close();

  assert.equal(sent.length, 95);
  assert.equal(h.queue.length, 0);
  assert.equal(h.queue.dropped, 0);
});

// close() used to see `draining` set, await *that* pass and return having
// flushed nothing of its own - with the interval already cleared, so the
// remainder was never sent by anyone. Roughly a 1-in-3 chance with a 10s
// interval and a fetch in flight.
test('close drains what an in-flight pass left behind', async () => {
  let release!: () => void;
  const firstSendStarted = new Promise<void>((resolve) => {
    release = resolve;
  });
  let openGate!: () => void;
  const gate = new Promise<void>((resolve) => {
    openGate = resolve;
  });

  let calls = 0;
  const sent: number[] = [];

  const h = harness<number>(testConfig(10, 1000), async (items) => {
    calls++;
    if (calls === 1) {
      release();
      await gate;
    }
    sent.push(...items);
    return accepted(items.length);
  });

  // Five items, below the batch size, so this pass sends exactly one batch and
  // then returns: the queue looked drained when it started.
  for (let i = 0; i < 5; i++) h.queue.add(i);

  const inFlight = h.drain(false);
  await firstSendStarted;

  // Fifty more arrive while that pass is stuck inside its one and only send.
  for (let i = 100; i < 150; i++) h.queue.add(i);

  const closing = h.dispatcher.close();
  openGate();
  await Promise.all([inFlight, closing]);

  assert.equal(h.queue.length, 0, 'close() ran its own pass after joining the one in flight');
  assert.equal(sent.length, 55);
});

test('flush rejects rather than resolving without flushing', async () => {
  const h = harness<number>(testConfig(10, 1000), async () => ({
    accepted: 0,
    retry: false,
    shed: false,
    retryAfterMs: 0,
    reason: Reason.InvalidPayload,
    error: new PushError(400, Reason.InvalidPayload, 'nope', true),
  }));

  for (let i = 0; i < 5; i++) h.queue.add(i);

  await assert.rejects(() => h.dispatcher.flush(), /invalid_payload/);

  await h.dispatcher.close();
});

// After close() there is no flusher, so an item enqueued here would sit in the
// queue forever: pending grew while dropped stayed 0.
test('add after close is counted as a drop rather than queued silently', async () => {
  const drops: Array<{ count: number; reason: string }> = [];
  const config = testConfig(10, 1000, {
    onDrop: (_stream: string, count: number, reason: string) => drops.push({ count, reason }),
  });
  const h = harness<number>(config, async (items) => accepted(items.length));

  await h.dispatcher.close();
  h.dispatcher.add(1);

  assert.equal(h.queue.length, 0, 'nothing is left stranded in the queue');
  assert.equal(h.queue.dropped, 1);
  assert.deepEqual(drops, [{ count: 1, reason: Reason.Closed }]);
});

test('close is idempotent', async () => {
  let calls = 0;
  const h = harness<number>(testConfig(10, 1000), async (items) => {
    calls++;
    return accepted(items.length);
  });

  h.queue.add(1);
  await h.dispatcher.close();
  await h.dispatcher.close();

  assert.equal(calls, 1);
});

// The timer is unref'd so telemetry never holds the event loop open, which on
// its own means a short-lived process exits with the whole queue unsent.
test('a beforeExit hook is registered as a safety net for a process that just ends', async () => {
  const before = process.listenerCount('beforeExit');
  const h = harness<number>(testConfig(10, 1000), async (items) => accepted(items.length));

  assert.equal(process.listenerCount('beforeExit'), before + 1);

  await h.dispatcher.close();

  assert.equal(
    process.listenerCount('beforeExit'),
    before,
    'close() must remove its hook, or every client leaks a listener'
  );
});
