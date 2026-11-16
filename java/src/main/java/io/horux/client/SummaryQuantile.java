package io.horux.client;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * A quantile observation in a summary metric.
 */
public class SummaryQuantile {
    @JsonProperty("quantile")
    private final double quantile;

    @JsonProperty("value")
    private final double value;

    public SummaryQuantile(double quantile, double value) {
        this.quantile = quantile;
        this.value = value;
    }

    public double getQuantile() {
        return quantile;
    }

    public double getValue() {
        return value;
    }
}
