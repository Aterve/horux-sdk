package io.horux.client;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Logs ingestion statistics, as returned by {@code GET /api/v1/logs/stats}.
 *
 * <p>Only the buffer block is returned to a tenant token; see
 * {@link MetricsStats} for why the server-wide writer counters are absent
 * rather than zero.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class LogsStats {

    @JsonProperty("buffer")
    private BufferStats buffer;

    public BufferStats getBuffer() { return buffer; }

    /**
     * This ingestor instance's backpressure: the same condition a push response
     * reports as {@code buffer_full}, and the part a client can act on.
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BufferStats {
        @JsonProperty("queued_logs")
        private long queuedLogs;

        @JsonProperty("capacity")
        private long capacity;

        @JsonProperty("utilization_percent")
        private double utilizationPercent;

        @JsonProperty("dropped_logs")
        private long droppedLogs;

        @JsonProperty("dropped_batches")
        private long droppedBatches;

        public long getQueuedLogs() { return queuedLogs; }

        public long getCapacity() { return capacity; }

        public double getUtilizationPercent() { return utilizationPercent; }

        /**
         * Logs the server-side buffer took and then shed because its writer
         * could not keep up: loss that has already happened and that no
         * client-side retry can recover.
         */
        public long getDroppedLogs() { return droppedLogs; }

        /** Whole batches shed for the same reason. */
        public long getDroppedBatches() { return droppedBatches; }
    }
}
