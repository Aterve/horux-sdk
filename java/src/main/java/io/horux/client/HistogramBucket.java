package io.horux.client;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * A single bucket in a histogram metric.
 */
public class HistogramBucket {
    @JsonProperty("upper_bound")
    private final double upperBound;

    @JsonProperty("count")
    private final long count;

    public HistogramBucket(double upperBound, long count) {
        this.upperBound = upperBound;
        this.count = count;
    }

    public double getUpperBound() {
        return upperBound;
    }

    public long getCount() {
        return count;
    }
}
