package io.horux.client;

/**
 * Type of metric being recorded.
 */
public enum MetricType {
    /**
     * Counter metric - always increasing value.
     * Use for: request counts, error counts, events.
     */
    COUNTER("counter"),

    /**
     * Gauge metric - can increase or decrease.
     * Use for: memory usage, active connections, queue depth.
     */
    GAUGE("gauge"),

    /**
     * Histogram metric - distribution tracking with buckets.
     * Use for: request durations, response sizes.
     */
    HISTOGRAM("histogram"),

    /**
     * Summary metric - quantile/percentile tracking.
     * Use for: p50, p90, p99 latencies.
     */
    SUMMARY("summary");

    private final String value;

    MetricType(String value) {
        this.value = value;
    }

    public String getValue() {
        return value;
    }

    @Override
    public String toString() {
        return value;
    }
}
