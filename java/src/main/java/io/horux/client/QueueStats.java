package io.horux.client;

import java.time.Duration;

/**
 * The state of the client-side send queue.
 */
public class QueueStats {
    private final int pending;
    private final long dropped;
    private final int capacity;
    private final Duration retryBackoff;
    private final boolean shedding;

    public QueueStats(int pending, long dropped, int capacity, Duration retryBackoff, boolean shedding) {
        this.pending = pending;
        this.dropped = dropped;
        this.capacity = capacity;
        this.retryBackoff = retryBackoff;
        this.shedding = shedding;
    }

    /** Items waiting to be sent. */
    public int getPending() {
        return pending;
    }

    /** Items this client has discarded in total. */
    public long getDropped() {
        return dropped;
    }

    /** The configured buffer bound. */
    public int getCapacity() {
        return capacity;
    }

    /** Remaining wait before the next send attempt. */
    public Duration getRetryBackoff() {
        return retryBackoff;
    }

    /**
     * True while data is dropped on arrival, which happens when the tenant is
     * over its storage quota.
     */
    public boolean isShedding() {
        return shedding;
    }

    @Override
    public String toString() {
        return "QueueStats{pending=" + pending
                + ", dropped=" + dropped
                + ", capacity=" + capacity
                + ", retryBackoff=" + retryBackoff
                + ", shedding=" + shedding + "}";
    }
}
