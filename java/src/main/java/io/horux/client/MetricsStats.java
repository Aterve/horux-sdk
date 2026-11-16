package io.horux.client;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Metrics ingestion statistics, as returned by {@code GET /api/v1/metrics/stats}.
 *
 * <p>Only the buffer block is returned to a tenant token. The writer counters
 * this endpoint used to include - total metrics written, batches written, write
 * errors - were server-wide, spanning <em>every</em> tenant, so they are now
 * returned only to the internal service key. They are deliberately absent from
 * this type rather than present and always zero: a counter that reads as fact
 * and is not is worse than no counter at all, and zero is indistinguishable
 * from a genuinely idle ingestor.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class MetricsStats {

    @JsonProperty("buffer")
    private BufferStats buffer;

    public BufferStats getBuffer() { return buffer; }

    /**
     * This ingestor instance's backpressure: the same condition a push response
     * reports as {@code buffer_full}, and the part a client can act on.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BufferStats {
        @JsonProperty("queued_metrics")
        private long queuedMetrics;

        @JsonProperty("capacity")
        private long capacity;

        @JsonProperty("utilization_percent")
        private double utilizationPercent;

        @JsonProperty("dropped_metrics")
        private long droppedMetrics;

        @JsonProperty("dropped_batches")
        private long droppedBatches;

        public long getQueuedMetrics() { return queuedMetrics; }

        public long getCapacity() { return capacity; }

        public double getUtilizationPercent() { return utilizationPercent; }

        /**
         * Metrics the server-side buffer took and then shed because its writer
         * could not keep up: loss that has already happened and that no
         * client-side retry can recover, so it is the figure worth alerting on.
         */
        public long getDroppedMetrics() { return droppedMetrics; }

        /** Whole batches shed for the same reason. */
        public long getDroppedBatches() { return droppedBatches; }
    }
}
