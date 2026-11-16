import { ResolvedHoruxConfig } from './config';
import { DropPolicy } from './types';

/** Rejection reasons reported by the ingestor. */
export const Reason = {
  RateLimit: 'rate_limit',
  StorageQuota: 'storage_quota',
  BufferFull: 'buffer_full',
  BackendUnavailable: 'backend_unavailable',
  Transport: 'transport',
  InvalidPayload: 'invalid_payload',
  Rejected: 'rejected',

  /** Client-side: recorded after close(), when nothing is left to send it. */
  Closed: 'client_closed',
} as const;

/**
 * Tells the ingestor this client understands a partial acceptance, i.e. that it
 * will keep the unaccepted tail rather than re-sending the whole batch. Without
 * it the server rejects a batch that does not fit whole, which a client holding
 * a large backlog can never get past.
 */
export const PARTIAL_ACCEPT_HEADER = 'X-Horux-Partial-Accept';

/** Bounds the work one flush cycle does, so a large backlog drains over several. */
export const MAX_CHUNKS_PER_FLUSH = 16;

/** Floor for how long to shed data after a storage quota rejection. */
const MIN_SHED_MS = 60_000;

/** Throttle for drop notifications, so an outage does not itself become a flood. */
const DROP_REPORT_INTERVAL_MS = 30_000;

/** Describes a rejected push. */
export class PushError extends Error {
  /** HTTP status returned by the ingestor, or 0 for a transport failure. */
  readonly status: number;

  /** The ingestor's machine-readable reason, when it gave one. */
  readonly reason: string;

  /** True when the payload will never be accepted, so the data was dropped. */
  readonly permanent: boolean;

  constructor(status: number, reason: string, message: string, permanent = false) {
    super(status ? `horux: ${status} ${reason}: ${message}` : `horux: ${message}`);
    this.name = 'PushError';
    this.status = status;
    this.reason = reason;
    this.permanent = permanent;
  }
}

/** What the ingestor did with one batch. */
export interface PushOutcome {
  /**
   * How many items were taken. The ingestor always takes a prefix of the
   * batch, so items.slice(accepted) is exactly what is left over.
   */
  accepted: number;

  /** The leftover is worth keeping and sending again. */
  retry: boolean;

  /**
   * The leftover should be dropped and further data discarded until
   * retryAfterMs passes: a storage quota does not clear by waiting a moment,
   * and holding data for it only grows the buffer.
   */
  shed: boolean;

  /** The server's hint for when to come back. */
  retryAfterMs: number;

  /**
   * True when the ingestor published a remaining quota of zero for this
   * tenant, with `resetAfterMs` the wait until the allowance is back.
   *
   * The ingestor sets these on every push explicitly "so a client can pace
   * itself instead of discovering the limit by being rejected"; reading them is
   * what stops that costing a wasted round trip and a 429.
   */
  allowanceSpent?: boolean;

  resetAfterMs?: number;

  reason: string;

  error?: PushError;
}

/** The allowance the ingestor publishes on every push. */
export const RATE_LIMIT_REMAINING_HEADER = 'X-RateLimit-Remaining';
export const RATE_LIMIT_RESET_HEADER = 'X-RateLimit-Reset';

/**
 * Report whether the tenant's allowance is spent and, if so, the wait until it
 * returns. The reset header is a whole number of *seconds until the full
 * allowance is available again*, not an absolute time.
 */
export function readQuotaHeaders(headers: Headers | undefined): {
  allowanceSpent: boolean;
  resetAfterMs: number;
} {
  const raw = headers?.get(RATE_LIMIT_REMAINING_HEADER);
  const remaining = raw === null || raw === undefined ? NaN : Number(raw);

  if (!Number.isFinite(remaining) || remaining > 0) {
    // Absent (quota not enforced for this tenant) or allowance left.
    return { allowanceSpent: false, resetAfterMs: 0 };
  }

  const seconds = Number(headers?.get(RATE_LIMIT_RESET_HEADER) ?? NaN);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return { allowanceSpent: true, resetAfterMs: 0 };
  }

  return { allowanceSpent: true, resetAfterMs: seconds * 1000 };
}

/** The shared response shape of both push endpoints. */
interface PushResponseBody {
  accepted?: number;
  rejected?: number;
  message?: string;
  reason?: string;
  retry_after_ms?: number;
  error?: string;
}

/**
 * Turn an ingestor response into an outcome.
 *
 * The distinction that matters is whether the leftover is worth keeping:
 * anything the server might accept later is retried, a payload it will never
 * accept is dropped rather than retried forever, and data that has nowhere to
 * go at all (storage quota) is shed.
 */
export function interpretPush(
  status: number,
  headers: Headers | undefined,
  body: PushResponseBody | undefined,
  batchSize: number
): PushOutcome {
  const parsed = body ?? {};
  const message = parsed.error || parsed.message || 'push rejected';

  let retryAfterMs = parsed.retry_after_ms ?? 0;
  if (!retryAfterMs) {
    retryAfterMs = parseRetryAfterHeader(headers?.get('Retry-After') ?? null);
  }

  let accepted = parsed.accepted ?? 0;
  if (!Number.isFinite(accepted) || accepted < 0) {
    accepted = 0;
  }
  accepted = Math.min(Math.floor(accepted), batchSize);

  const { allowanceSpent, resetAfterMs } = readQuotaHeaders(headers);

  if (parsed.reason === Reason.StorageQuota) {
    return {
      accepted,
      retry: false,
      shed: true,
      retryAfterMs: Math.max(retryAfterMs, MIN_SHED_MS),
      reason: Reason.StorageQuota,
      error: new PushError(status, Reason.StorageQuota, message),
    };
  }

  if (status === 200) {
    if (accepted >= batchSize) {
      return {
        accepted: batchSize,
        retry: false,
        shed: false,
        retryAfterMs: 0,
        allowanceSpent,
        resetAfterMs,
        reason: '',
      };
    }

    // Partially accepted: keep the tail, come back when told to.
    const reason = parsed.reason || Reason.Rejected;
    return {
      accepted,
      retry: true,
      shed: false,
      retryAfterMs,
      reason,
      error: new PushError(status, reason, message),
    };
  }

  if (status === 400 || status === 413 || status === 422) {
    // The payload itself is unacceptable; retrying it forever would block
    // every later batch behind data that can never be sent.
    const reason = parsed.reason || Reason.InvalidPayload;
    return {
      accepted,
      retry: false,
      shed: false,
      retryAfterMs: 0,
      reason,
      error: new PushError(status, reason, message, true),
    };
  }

  // Rate limits, auth problems and server errors can all clear on their own,
  // so the data is kept.
  const reason = parsed.reason || (status === 429 ? Reason.RateLimit : Reason.BackendUnavailable);
  return {
    accepted,
    retry: true,
    shed: false,
    retryAfterMs,
    reason,
    error: new PushError(status, reason, message),
  };
}

/** Reads both forms of Retry-After, seconds and HTTP date. */
export function parseRetryAfterHeader(value: string | null): number {
  if (!value) {
    return 0;
  }

  const seconds = Number(value);
  if (Number.isFinite(seconds)) {
    return seconds > 0 ? seconds * 1000 : 0;
  }

  const when = Date.parse(value);
  if (!Number.isNaN(when)) {
    const delta = when - Date.now();
    return delta > 0 ? delta : 0;
  }

  return 0;
}

/**
 * A bounded FIFO of items waiting to be sent.
 *
 * The bound is the point: an unbounded buffer turns a rate limit or an outage
 * into unbounded memory growth inside the host application, and the telemetry
 * client is never worth taking the application down for.
 */
export class BoundedQueue<T> {
  private items: T[] = [];
  private droppedCount = 0;
  private readonly maxSize: number;
  private readonly policy: DropPolicy;

  constructor(maxSize: number, policy: DropPolicy) {
    this.maxSize = maxSize;
    this.policy = policy;
  }

  /** Appends an item, dropping to stay within the bound. Returns items dropped. */
  add(item: T): number {
    if (this.items.length < this.maxSize) {
      this.items.push(item);
      return 0;
    }

    if (this.policy === 'newest') {
      this.droppedCount++;
      return 1;
    }

    const overflow = this.items.length - this.maxSize + 1;
    this.items.splice(0, overflow);
    this.items.push(item);
    this.droppedCount += overflow;
    return overflow;
  }

  /** Removes and returns up to n items from the front. */
  take(n: number): T[] {
    return this.items.splice(0, n);
  }

  /** Returns unsent items to the front, preserving order and the bound. */
  requeue(items: T[]): number {
    if (items.length === 0) {
      return 0;
    }

    const combined = items.concat(this.items);

    let dropped = 0;
    if (combined.length > this.maxSize) {
      dropped = combined.length - this.maxSize;
      this.items = this.policy === 'newest'
        ? combined.slice(0, this.maxSize)
        : combined.slice(dropped);
      this.droppedCount += dropped;
    } else {
      this.items = combined;
    }

    return dropped;
  }

  /** Drops everything pending, for data the server will never accept. */
  discard(): number {
    const n = this.items.length;
    this.items = [];
    this.droppedCount += n;
    return n;
  }

  countDropped(n: number): void {
    if (n > 0) {
      this.droppedCount += n;
    }
  }

  get length(): number {
    return this.items.length;
  }

  get dropped(): number {
    return this.droppedCount;
  }
}

/**
 * Paces retries after a failed or throttled flush.
 *
 * Without it a client that keeps data on failure retries on its normal flush
 * cadence, and because a full buffer also triggered a flush on every recorded
 * item, a throttled client ended up sending a request per datapoint.
 */
export class Backoff {
  private attempt = 0;
  private notBefore = 0;
  private readonly minMs: number;
  private readonly maxMs: number;

  constructor(minMs: number, maxMs: number) {
    this.minMs = minMs;
    this.maxMs = maxMs;
  }

  ready(now = Date.now()): boolean {
    return now >= this.notBefore;
  }

  /**
   * Records a failure and schedules the next attempt. A server-supplied hint
   * wins over the exponential schedule, since the server knows exactly when the
   * allowance returns; jitter on top keeps a fleet from retrying in lockstep.
   * maxRetryBackoff bounds the exponential branch only.
   */
  fail(hintMs = 0, now = Date.now()): number {
    this.attempt++;

    let delay: number;
    if (hintMs > 0) {
      // The hint wins outright and is deliberately not clamped to maxMs: that
      // bound is on the client's own exponential guesswork, not on an explicit
      // instruction from the other end. Clamping a `Retry-After: 300` from an
      // intermediary (CDN, API gateway, WAF under load) to 60s retries five
      // times inside a window something just asked to stay quiet for.
      delay = hintMs + Math.random() * (hintMs / 4);
    } else {
      const exp = Math.min(this.minMs * Math.pow(2, this.attempt - 1), this.maxMs);
      // Full jitter: anywhere in (0, exp].
      delay = Math.min(Math.random() * exp + 1, this.maxMs);
    }

    this.notBefore = now + delay;
    return delay;
  }

  /**
   * Hold off the next cycle without recording a failure. The server has
   * published that the allowance is spent, so sending again would only earn a
   * rejection - but nothing has actually failed, so the exponential attempt
   * counter must not advance.
   */
  pace(delayMs: number, now = Date.now()): void {
    const delay = delayMs > 0 ? delayMs : this.minMs;
    this.notBefore = Math.max(this.notBefore, now + delay);
  }

  succeed(): void {
    this.attempt = 0;
    this.notBefore = 0;
  }

  remaining(now = Date.now()): number {
    return Math.max(0, this.notBefore - now);
  }
}

/** The state of the client-side send queue. */
export interface QueueStats {
  /** Items waiting to be sent. */
  pending: number;

  /** Items this client has discarded in total. */
  dropped: number;

  /** The configured buffer bound. */
  capacity: number;

  /** Remaining wait before the next send attempt (ms). */
  retryBackoffMs: number;

  /**
   * True while data is dropped on arrival, which happens when the tenant is
   * over its storage quota.
   */
  shedding: boolean;
}

/** Delivers one batch to the ingestor. */
export type SendFn<T> = (items: T[]) => Promise<PushOutcome>;

/**
 * Owns the pending queue, the flush cycle and the retry state for one stream
 * (metrics or logs).
 *
 * Recording an item only enqueues it; the HTTP request always happens on the
 * flush cycle. Flushing straight from `record` is what turned a throttled
 * client into a request per datapoint, because a buffer stuck at the batch
 * threshold satisfied the "buffer full, flush now" test on every call.
 */
export class Dispatcher<T> {
  private readonly queue: BoundedQueue<T>;
  private readonly backoff: Backoff;
  private timer: NodeJS.Timeout | null = null;
  private shedUntil = 0;
  private lastReport = 0;
  private draining: Promise<void> | null = null;
  private wakeScheduled = false;
  private closed = false;
  private exitHook: (() => void) | null = null;
  private readonly config: ResolvedHoruxConfig;
  private readonly stream: 'metrics' | 'logs';
  private readonly send: SendFn<T>;

  constructor(config: ResolvedHoruxConfig, stream: 'metrics' | 'logs', send: SendFn<T>) {
    this.config = config;
    this.stream = stream;
    this.send = send;
    this.queue = new BoundedQueue<T>(config.maxBufferSize, config.dropPolicy);
    this.backoff = new Backoff(config.minRetryBackoff, config.maxRetryBackoff);
    this.start();
  }

  /** Enqueues an item. Never performs I/O and never awaits the network. */
  add(item: T): void {
    if (this.closed) {
      // There is no flusher left. Without this the item was enqueued anyway and
      // moved only if the batch threshold happened to fire a drain, so a library
      // logging during its own teardown accumulated items that were never sent
      // and never counted: stats().pending grew while dropped stayed 0.
      this.queue.countDropped(1);
      this.report(1, Reason.Closed);
      return;
    }

    if (this.shedding()) {
      // Nowhere to put this data; dropping it now keeps memory flat.
      this.queue.countDropped(1);
      this.report(1, Reason.StorageQuota);
      return;
    }

    const dropped = this.queue.add(item);
    if (dropped > 0) {
      this.report(dropped, Reason.BufferFull);
    }

    if (this.queue.length >= this.config.maxBatchSize) {
      this.scheduleDrain();
    }
  }

  /**
   * Drains synchronously. Throws the last rejection so callers of the public
   * flush() still learn that something could not be sent.
   */
  async flush(): Promise<void> {
    const error = await this.drain(true);
    if (error) {
      throw error;
    }
  }

  async close(): Promise<void> {
    if (this.closed) {
      return;
    }
    this.closed = true;

    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }

    if (this.exitHook) {
      process.off?.('beforeExit', this.exitHook);
      this.exitHook = null;
    }

    await this.drain(true);
  }

  stats(): QueueStats {
    return {
      pending: this.queue.length,
      dropped: this.queue.dropped,
      capacity: this.config.maxBufferSize,
      retryBackoffMs: this.backoff.remaining(),
      shedding: this.shedding(),
    };
  }

  /**
   * Nudges the flush cycle for the next turn of the event loop. Deferring it
   * keeps every part of sending - serialization included - off the stack of
   * whoever recorded the datapoint.
   */
  private scheduleDrain(): void {
    if (this.wakeScheduled) {
      return;
    }
    this.wakeScheduled = true;

    setImmediate(() => {
      this.wakeScheduled = false;
      void this.drain(false).catch(() => undefined);
    });
  }

  private start(): void {
    this.timer = setInterval(() => {
      void this.drain(false).catch(() => undefined);
    }, this.config.flushInterval);

    // Never hold the event loop open for telemetry.
    this.timer.unref?.();

    // But an unref'd timer and nothing else means a short-lived process - a
    // CLI, a lambda, a cron script, a test run - exits with the whole queue
    // unsent, and with the 10s default interval a script that finishes sooner
    // loses 100% of its telemetry unless the author remembered close().
    // beforeExit fires while async work can still be scheduled, so draining
    // from it delivers the backlog without defeating the unref().
    if (typeof process !== 'undefined' && typeof process.once === 'function') {
      this.exitHook = () => {
        void this.drain(true).catch(() => undefined);
      };
      process.once('beforeExit', this.exitHook);
    }
  }

  /**
   * Sends pending items in batches of at most maxBatchSize, stopping at the
   * first batch that is not fully accepted, so a throttled client makes exactly
   * the progress its allowance permits and no more.
   *
   * Concurrent calls share one pass: overlapping drains would send the same
   * backlog twice over.
   */
  private async drain(final: boolean): Promise<PushError | undefined> {
    // Only one pass runs at a time. A periodic pass can simply join the one in
    // flight, but a final pass has to run its own afterwards: close() has
    // already cleared the interval, so whatever the in-flight pass leaves
    // behind would never be sent by anyone. Returning early here is how
    // `await client.close()` in a SIGTERM handler silently discarded up to
    // maxBufferSize items whenever a fetch happened to be outstanding, and how
    // flush() resolved successfully without flushing and without throwing.
    while (this.draining) {
      await this.draining.catch(() => undefined);
      if (!final) {
        return undefined;
      }
    }

    let lastError: PushError | undefined;

    const pass = (async () => {
      if (!final && !this.backoff.ready()) {
        return;
      }

      if (this.shedding()) {
        const dropped = this.queue.discard();
        if (dropped > 0) {
          this.report(dropped, Reason.StorageQuota);
        }
        return;
      }

      // A final pass runs until the queue is empty. The cap exists to bound a
      // periodic cycle so a backlog drains over several; applying it to close()
      // abandons everything past 16 batches with no drop report and no error
      // once maxBufferSize exceeds 16 x maxBatchSize. This terminates: a
      // partial acceptance and a throttle both return, and every path that
      // continues the loop shrinks the queue.
      for (let i = 0; final || i < MAX_CHUNKS_PER_FLUSH; i++) {
        const batch = this.queue.take(this.config.maxBatchSize);
        if (batch.length === 0) {
          return;
        }

        const outcome = await this.send(batch);
        const unsent = batch.slice(outcome.accepted);

        if (outcome.shed) {
          this.queue.countDropped(unsent.length);
          const dropped = unsent.length + this.queue.discard();
          this.beginShed(Date.now() + outcome.retryAfterMs);
          this.report(dropped, outcome.reason);
          lastError = outcome.error;
          return;
        }

        if (outcome.retry) {
          const dropped = this.queue.requeue(unsent);
          if (dropped > 0) {
            this.report(dropped, Reason.BufferFull);
          }
          this.backoff.fail(outcome.retryAfterMs);
          lastError = outcome.error;
          return;
        }

        if (outcome.error) {
          // Permanent: drop this batch and keep going with the next one.
          this.queue.countDropped(unsent.length);
          this.report(unsent.length, outcome.reason);
          lastError = outcome.error;
          continue;
        }

        this.backoff.succeed();

        if (batch.length < this.config.maxBatchSize) {
          // The queue is drained; nothing to gain from another request.
          return;
        }

        if (!final && outcome.allowanceSpent) {
          // The ingestor published that this tenant's allowance is spent.
          // Sending the next batch would only earn a 429 and a round trip,
          // which is exactly what publishing those headers exists to avoid.
          // This is pacing, not a failure, so the exponential attempt counter
          // is left alone.
          this.backoff.pace(outcome.resetAfterMs ?? 0);
          return;
        }
      }
    })();

    this.draining = pass.then(
      () => undefined,
      () => undefined
    );

    try {
      await pass;
    } finally {
      this.draining = null;
    }

    return lastError;
  }

  private shedding(now = Date.now()): boolean {
    return now < this.shedUntil;
  }

  private beginShed(until: number): void {
    if (until > this.shedUntil) {
      this.shedUntil = until;
    }
  }

  /** Surfaces dropped data through the configured hook, throttled. */
  private report(count: number, reason: string): void {
    if (count <= 0) {
      return;
    }

    const now = Date.now();
    if (now - this.lastReport < DROP_REPORT_INTERVAL_MS) {
      return;
    }
    this.lastReport = now;

    if (this.config.onDrop) {
      this.config.onDrop(this.stream, count, reason);
    }
  }
}

/** Posts one batch to the ingestor and interprets the response. */
export async function doPush(
  config: ResolvedHoruxConfig,
  path: string,
  payload: unknown,
  batchSize: number
): Promise<PushOutcome> {
  let body: string;
  try {
    body = JSON.stringify(payload);
  } catch (error) {
    // Unencodable data will never be accepted.
    return {
      accepted: 0,
      retry: false,
      shed: false,
      retryAfterMs: 0,
      reason: Reason.InvalidPayload,
      error: new PushError(0, Reason.InvalidPayload, String(error), true),
    };
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), config.httpTimeout);

  try {
    const response = await fetch(`${config.ingestorUrl}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${config.apiToken}`,
        [PARTIAL_ACCEPT_HEADER]: '1',
      },
      body,
      signal: controller.signal,
    });

    let parsed: PushResponseBody | undefined;
    try {
      parsed = (await response.json()) as PushResponseBody;
    } catch {
      parsed = undefined;
    }

    return interpretPush(response.status, response.headers, parsed, batchSize);
  } catch (error) {
    // A connection problem is transient by nature; keep the data.
    return {
      accepted: 0,
      retry: true,
      shed: false,
      retryAfterMs: 0,
      reason: Reason.Transport,
      error: new PushError(0, Reason.Transport, error instanceof Error ? error.message : String(error)),
    };
  } finally {
    clearTimeout(timeout);
  }
}
