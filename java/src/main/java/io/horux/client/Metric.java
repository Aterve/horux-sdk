package io.horux.client;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * A single metric data point.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class Metric {
    @JsonProperty("name")
    private final String name;

    @JsonProperty("type")
    private final String type;

    @JsonProperty("value")
    private final Double value;

    @JsonProperty("dimensions")
    private final Map<String, String> dimensions;

    @JsonProperty("labels")
    private final Map<String, String> labels;

    @JsonProperty("timestamp")
    private final Instant timestamp;

    @JsonProperty("buckets")
    private final List<HistogramBucket> buckets;

    @JsonProperty("sum")
    private final Double sum;

    @JsonProperty("count")
    private final Long count;

    @JsonProperty("quantiles")
    private final List<SummaryQuantile> quantiles;

    private Metric(Builder builder) {
        this.name = builder.name;
        this.type = builder.type;
        this.value = builder.value;
        this.dimensions = builder.dimensions;
        this.labels = builder.labels;
        this.timestamp = builder.timestamp != null ? builder.timestamp : Instant.now();
        this.buckets = builder.buckets;
        this.sum = builder.sum;
        this.count = builder.count;
        this.quantiles = builder.quantiles;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private String name;
        private String type;
        private Double value;
        private Map<String, String> dimensions;
        private Map<String, String> labels;
        private Instant timestamp;
        private List<HistogramBucket> buckets;
        private Double sum;
        private Long count;
        private List<SummaryQuantile> quantiles;

        public Builder name(String name) {
            this.name = name;
            return this;
        }

        public Builder type(String type) {
            this.type = type;
            return this;
        }

        public Builder value(Double value) {
            this.value = value;
            return this;
        }

        public Builder dimensions(Map<String, String> dimensions) {
            this.dimensions = dimensions;
            return this;
        }

        public Builder labels(Map<String, String> labels) {
            this.labels = labels;
            return this;
        }

        public Builder timestamp(Instant timestamp) {
            this.timestamp = timestamp;
            return this;
        }

        public Builder buckets(List<HistogramBucket> buckets) {
            this.buckets = buckets;
            return this;
        }

        public Builder sum(Double sum) {
            this.sum = sum;
            return this;
        }

        public Builder count(Long count) {
            this.count = count;
            return this;
        }

        public Builder quantiles(List<SummaryQuantile> quantiles) {
            this.quantiles = quantiles;
            return this;
        }

        public Metric build() {
            return new Metric(this);
        }
    }

    // Getters
    public String getName() { return name; }
    public String getType() { return type; }
    public Double getValue() { return value; }
    public Map<String, String> getDimensions() { return dimensions; }
    public Map<String, String> getLabels() { return labels; }
    public Instant getTimestamp() { return timestamp; }
    public List<HistogramBucket> getBuckets() { return buckets; }
    public Double getSum() { return sum; }
    public Long getCount() { return count; }
    public List<SummaryQuantile> getQuantiles() { return quantiles; }
}
