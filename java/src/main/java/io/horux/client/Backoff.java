package io.horux.client;

import java.time.Duration;
import java.util.concurrent.ThreadLocalRandom;

/**
 * Paces retries after a failed or throttled flush.
 *
 * <p>Without it a client that keeps data on failure retries on its normal flush
 * cadence, and because a full buffer also triggered a flush on every recorded
 * item, a throttled client ended up sending a request per datapoint.
 */
class Backoff {
    private final long minMillis;
    private final long maxMillis;
    private int attempt;
    private long notBeforeMillis;

    Backoff(Duration min, Duration max) {
        this.minMillis = Math.max(1L, min.toMillis());
        this.maxMillis = Math.max(this.minMillis, max.toMillis());
    }

    synchronized boolean ready(long nowMillis) {
        return nowMillis >= notBeforeMillis;
    }

    /**
     * Records a failure and schedules the next attempt. A server-supplied hint
     * wins over the exponential schedule, since the server knows exactly when
     * the allowance returns; jitter on top keeps a fleet of clients from
     * retrying in lockstep. {@code max} bounds the exponential branch only.
     *
     * @return the chosen delay
     */
    synchronized Duration fail(Duration hint, long nowMillis) {
        attempt++;

        long delay;
        long hintMillis = hint == null ? 0L : hint.toMillis();
        if (hintMillis > 0) {
            // The hint wins outright and is deliberately not clamped to max:
            // max bounds the client's own exponential guesswork, not an explicit
            // instruction from the other end. Clamping a `Retry-After: 300` from
            // an intermediary (CDN, API gateway, WAF under load) to 60s retries
            // five times inside a window something just asked to stay quiet for.
            delay = hintMillis + ThreadLocalRandom.current().nextLong(hintMillis / 4 + 1);
        } else {
            double exp = Math.min((double) minMillis * Math.pow(2, attempt - 1), (double) maxMillis);
            // Full jitter: anywhere in (0, exp].
            delay = Math.min(ThreadLocalRandom.current().nextLong((long) exp) + 1, maxMillis);
        }

        notBeforeMillis = nowMillis + delay;
        return Duration.ofMillis(delay);
    }

    /**
     * Holds off the next cycle without recording a failure.
     *
     * <p>The server has published that the allowance is spent, so sending
     * again would only earn a rejection - but nothing has actually failed, so
     * the exponential attempt counter must not advance.
     */
    synchronized void pace(Duration delay, long nowMillis) {
        long delayMillis = delay == null || delay.toMillis() <= 0 ? minMillis : delay.toMillis();
        notBeforeMillis = Math.max(notBeforeMillis, nowMillis + delayMillis);
    }

    synchronized void succeed() {
        attempt = 0;
        notBeforeMillis = 0L;
    }

    synchronized Duration remaining(long nowMillis) {
        long left = notBeforeMillis - nowMillis;
        return left > 0 ? Duration.ofMillis(left) : Duration.ZERO;
    }
}
