package io.horux.client;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.locks.ReentrantLock;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Owns the pending queue, the flush cycle and the retry state for one stream
 * (metrics or logs).
 *
 * <p>Recording an item only enqueues it; the HTTP request always happens on the
 * dispatcher's own thread. Flushing from the caller's thread is what turned a
 * throttled client into a request per datapoint, because a buffer stuck at the
 * batch threshold satisfied the "buffer full, flush now" test on every call -
 * and worse, the send ran while holding the buffer lock.
 */
class Dispatcher<T> implements AutoCloseable {
    private static final Logger logger = Logger.getLogger(Dispatcher.class.getName());

    /** Bounds the work one flush cycle does, so a large backlog drains over several. */
    static final int MAX_CHUNKS_PER_FLUSH = 16;

    /** Throttle for drop notifications, so an outage is not itself a flood. */
    private static final long DROP_REPORT_INTERVAL_MILLIS = 30_000L;

    /** Headroom on top of httpTimeout when waiting for an in-flight request on close. */
    private static final long CLOSE_GRACE_MARGIN_MILLIS = 1_000L;

    /** Sentinel for an unbounded wait on the drain lock. */
    private static final long WAIT_FOREVER = -1L;

    /** Delivers one batch to the ingestor. */
    @FunctionalInterface
    interface BatchSender<T> {
        PushOutcome send(List<T> batch);
    }

    private final HoruxConfig config;
    private final String stream;
    private final BatchSender<T> sender;
    private final SendQueue<T> queue;
    private final Backoff backoff;
    private final ScheduledExecutorService scheduler;
    private final ReentrantLock drainLock = new ReentrantLock();
    private final AtomicBoolean wakeScheduled = new AtomicBoolean(false);

    private volatile long shedUntilMillis;
    private volatile long lastReportMillis;

    Dispatcher(HoruxConfig config, String stream, BatchSender<T> sender) {
        this.config = config;
        this.stream = stream;
        this.sender = sender;
        this.queue = new SendQueue<>(config.getMaxBufferSize(), config.getDropPolicy());
        this.backoff = new Backoff(config.getMinRetryBackoff(), config.getMaxRetryBackoff());

        ThreadFactory threads = runnable -> {
            Thread thread = new Thread(runnable, "horux-" + stream + "-flusher");
            // Telemetry must never hold a JVM open.
            thread.setDaemon(true);
            return thread;
        };
        this.scheduler = Executors.newSingleThreadScheduledExecutor(threads);

        long intervalMillis = config.getFlushInterval().toMillis();
        this.scheduler.scheduleAtFixedRate(() -> drainQuietly(false),
                intervalMillis, intervalMillis, TimeUnit.MILLISECONDS);
    }

    /** Enqueues an item. Never performs I/O and never blocks on the network. */
    void add(T item) {
        if (isShedding()) {
            // Nowhere to put this data; dropping it now keeps memory flat.
            queue.countDropped(1);
            report(1, PushOutcome.REASON_STORAGE_QUOTA);
            return;
        }

        int dropped = queue.add(item);
        if (dropped > 0) {
            report(dropped, PushOutcome.REASON_BUFFER_FULL);
        }

        if (queue.size() >= config.getMaxBatchSize() && wakeScheduled.compareAndSet(false, true)) {
            // Hand the work to the flusher rather than sending from here.
            try {
                scheduler.execute(() -> {
                    wakeScheduled.set(false);
                    drainQuietly(false);
                });
            } catch (RuntimeException e) {
                wakeScheduled.set(false);
            }
        }
    }

    /** Drains synchronously, ignoring the retry backoff. */
    void flush() {
        drain(true);
    }

    QueueStats stats() {
        long now = System.currentTimeMillis();
        return new QueueStats(queue.size(), queue.getDropped(), config.getMaxBufferSize(),
                backoff.remaining(now), isShedding());
    }

    @Override
    public void close() {
        scheduler.shutdown();

        // Give an in-flight request its full httpTimeout to land. Interrupting
        // it after a fixed 5s, against a 30s default timeout, made Transport
        // report a transport failure, which requeued a batch the server had
        // very likely already committed - and the final pass below then sent it
        // a second time. Duplicated counters are a wrong number, not a dup row.
        long graceMillis = closeGraceMillis(config);
        try {
            if (!scheduler.awaitTermination(graceMillis, TimeUnit.MILLISECONDS)) {
                scheduler.shutdownNow();
            }
        } catch (InterruptedException e) {
            scheduler.shutdownNow();
            Thread.currentThread().interrupt();
        }

        try {
            drainWithin(true, graceMillis);
        } catch (Throwable t) {
            logger.log(Level.WARNING, "horux: unexpected error while flushing " + stream + " on close", t);
        }
    }

    /** Package-private for tests. */
    void drainQuietly(boolean finalPass) {
        try {
            drain(finalPass);
        } catch (Throwable t) {
            // scheduleAtFixedRate cancels its task permanently if the task
            // throws, so catching only RuntimeException meant an Error - a
            // NoClassDefFoundError from a missing Jackson transitive, a
            // StackOverflowError - killed the flusher with no log and no stat
            // change, and stats() went on reporting a `pending` that nothing
            // would ever drain again.
            logger.log(Level.WARNING, "horux: unexpected error while flushing " + stream, t);
        }
    }

    /**
     * Sends pending items in batches of at most maxBatchSize, stopping at the
     * first batch that is not fully accepted, so a throttled client makes
     * exactly the progress its allowance permits and no more.
     */
    void drain(boolean finalPass) {
        drainWithin(finalPass, WAIT_FOREVER);
    }

    /**
     * @param lockWaitMillis how long a final pass waits for a concurrent pass
     *                       to finish, or {@link #WAIT_FOREVER}. close() bounds
     *                       it: a wait limited only by 16 batches x httpTimeout
     *                       is minutes long, and minutes inside a SIGTERM
     *                       handler is a SIGKILL.
     */
    private void drainWithin(boolean finalPass, long lockWaitMillis) {
        if (!acquireDrainLock(finalPass, lockWaitMillis)) {
            return;
        }

        try {
            long now = System.currentTimeMillis();

            if (!finalPass && !backoff.ready(now)) {
                return;
            }

            if (isShedding()) {
                int dropped = queue.discard();
                if (dropped > 0) {
                    report(dropped, PushOutcome.REASON_STORAGE_QUOTA);
                }
                return;
            }

            // A final pass runs until the queue is empty. MAX_CHUNKS_PER_FLUSH
            // exists to bound a periodic cycle so a backlog drains over several;
            // applying it to close() abandons the remainder with no drop report
            // and no error whenever maxBufferSize exceeds 16 x maxBatchSize.
            // Every path that continues the loop shrinks the queue, so this
            // terminates: a partial acceptance and a throttle both return.
            for (int i = 0; finalPass || i < MAX_CHUNKS_PER_FLUSH; i++) {
                List<T> batch = queue.take(config.getMaxBatchSize());
                if (batch.isEmpty()) {
                    return;
                }

                PushOutcome outcome = sender.send(batch);
                List<T> unsent = batch.subList(outcome.getAccepted(), batch.size());

                if (outcome.isShed()) {
                    queue.countDropped(unsent.size());
                    int dropped = unsent.size() + queue.discard();
                    beginShed(System.currentTimeMillis() + outcome.getRetryAfter().toMillis());
                    report(dropped, outcome.getReason());
                    return;
                }

                if (outcome.isRetryable()) {
                    int dropped = queue.requeue(unsent);
                    if (dropped > 0) {
                        report(dropped, PushOutcome.REASON_BUFFER_FULL);
                    }
                    Duration delay = backoff.fail(outcome.getRetryAfter(), System.currentTimeMillis());
                    logger.log(Level.FINE, "horux: {0} push throttled ({1}), {2} items kept, retrying in {3}",
                            new Object[]{stream, outcome.getReason(), unsent.size(), delay});
                    return;
                }

                if (outcome.getError() != null) {
                    // Permanent: drop this batch and keep going with the next.
                    queue.countDropped(unsent.size());
                    report(unsent.size(), outcome.getReason());
                    continue;
                }

                backoff.succeed();

                if (batch.size() < config.getMaxBatchSize()) {
                    // The queue is drained; nothing to gain from another request.
                    return;
                }

                if (!finalPass && outcome.isAllowanceSpent()) {
                    // The ingestor published that this tenant's allowance is
                    // spent. Sending the next batch would only earn a 429 and
                    // a round trip, which is exactly what publishing those
                    // headers exists to avoid. This is pacing, not a failure,
                    // so the exponential attempt counter is left alone.
                    backoff.pace(outcome.getResetAfter(), System.currentTimeMillis());
                    return;
                }
            }
        } finally {
            drainLock.unlock();
        }
    }

    /**
     * How long close() waits for an in-flight request, derived from the
     * configured timeout rather than fixed. Package-private for tests.
     */
    static long closeGraceMillis(HoruxConfig config) {
        return config.getHttpTimeout().toMillis() + CLOSE_GRACE_MARGIN_MILLIS;
    }

    /**
     * Takes the drain lock, so only one pass is in flight at a time: overlapping
     * passes would send the same backlog twice over.
     *
     * @return false if the caller must not drain
     */
    private boolean acquireDrainLock(boolean finalPass, long lockWaitMillis) {
        if (!finalPass) {
            return drainLock.tryLock();
        }

        if (lockWaitMillis == WAIT_FOREVER) {
            drainLock.lock();
            return true;
        }

        try {
            if (drainLock.tryLock(lockWaitMillis, TimeUnit.MILLISECONDS)) {
                return true;
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        }

        logger.log(Level.WARNING,
                "horux: gave up waiting for an in-flight {0} flush; {1} items left unsent",
                new Object[]{stream, queue.size()});
        return false;
    }

    /** True while data is dropped on arrival, over a storage quota. */
    boolean isShedding() {
        return System.currentTimeMillis() < shedUntilMillis;
    }

    private void beginShed(long untilMillis) {
        if (untilMillis > shedUntilMillis) {
            shedUntilMillis = untilMillis;
        }
    }

    /** Surfaces dropped data, throttled so an outage is not itself a flood. */
    private void report(int count, String reason) {
        if (count <= 0) {
            return;
        }

        long now = System.currentTimeMillis();
        synchronized (this) {
            if (now - lastReportMillis < DROP_REPORT_INTERVAL_MILLIS) {
                return;
            }
            lastReportMillis = now;
        }

        logger.log(Level.WARNING, "horux: dropped {0} {1} ({2}); total dropped {3}",
                new Object[]{count, stream, reason, queue.getDropped()});

        DropListener listener = config.getDropListener();
        if (listener != null) {
            try {
                listener.onDrop(stream, count, reason);
            } catch (RuntimeException e) {
                logger.log(Level.WARNING, "horux: drop listener threw", e);
            }
        }
    }

    /** Package-private for tests. */
    SendQueue<T> queue() {
        return queue;
    }
}
