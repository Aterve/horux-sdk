package io.horux.client;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

/**
 * A bounded FIFO of items waiting to be sent.
 *
 * <p>The bound is the point: an unbounded buffer turns a rate limit or an outage
 * into unbounded memory growth inside the host application, and the telemetry
 * client is never worth taking the application down for.
 */
class SendQueue<T> {
    private final Deque<T> items = new ArrayDeque<>();
    private final int maxSize;
    private final DropPolicy policy;
    private long dropped;

    SendQueue(int maxSize, DropPolicy policy) {
        this.maxSize = maxSize;
        this.policy = policy;
    }

    /**
     * Appends an item, dropping to stay within the bound.
     *
     * @return how many items were dropped to make room
     */
    synchronized int add(T item) {
        if (items.size() < maxSize) {
            items.addLast(item);
            return 0;
        }

        if (policy == DropPolicy.NEWEST) {
            dropped++;
            return 1;
        }

        int overflow = items.size() - maxSize + 1;
        for (int i = 0; i < overflow; i++) {
            items.pollFirst();
        }
        items.addLast(item);
        dropped += overflow;
        return overflow;
    }

    /** Removes and returns up to n items from the front. */
    synchronized List<T> take(int n) {
        List<T> batch = new ArrayList<>(Math.min(n, items.size()));
        for (int i = 0; i < n; i++) {
            T item = items.pollFirst();
            if (item == null) {
                break;
            }
            batch.add(item);
        }
        return batch;
    }

    /**
     * Returns unsent items to the front, preserving order and the bound.
     *
     * @return how many items had to be dropped
     */
    synchronized int requeue(List<T> unsent) {
        if (unsent.isEmpty()) {
            return 0;
        }

        for (int i = unsent.size() - 1; i >= 0; i--) {
            items.addFirst(unsent.get(i));
        }

        int over = items.size() - maxSize;
        if (over <= 0) {
            return 0;
        }

        for (int i = 0; i < over; i++) {
            if (policy == DropPolicy.NEWEST) {
                items.pollLast();
            } else {
                items.pollFirst();
            }
        }
        dropped += over;
        return over;
    }

    /** Drops everything pending, for data the server will never accept. */
    synchronized int discard() {
        int n = items.size();
        items.clear();
        dropped += n;
        return n;
    }

    synchronized void countDropped(int n) {
        if (n > 0) {
            dropped += n;
        }
    }

    synchronized int size() {
        return items.size();
    }

    synchronized long getDropped() {
        return dropped;
    }
}
