package io.horux.client;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.Instant;
import java.util.Map;

/**
 * A single log entry.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public class LogEntry {
    @JsonProperty("level")
    private final String level;

    @JsonProperty("message")
    private final String message;

    @JsonProperty("fields")
    private final Map<String, Object> fields;

    @JsonProperty("timestamp")
    private final Instant timestamp;

    @JsonProperty("trace_id")
    private final String traceId;

    @JsonProperty("span_id")
    private final String spanId;

    @JsonProperty("parent_span_id")
    private final String parentSpanId;

    @JsonProperty("request_id")
    private final String requestId;

    @JsonProperty("source")
    private final String source;

    private LogEntry(Builder builder) {
        this.level = builder.level;
        this.message = builder.message;
        this.fields = builder.fields;
        this.timestamp = builder.timestamp != null ? builder.timestamp : Instant.now();
        this.traceId = builder.traceId;
        this.spanId = builder.spanId;
        this.parentSpanId = builder.parentSpanId;
        this.requestId = builder.requestId;
        this.source = builder.source;
    }

    public static Builder builder() {
        return new Builder();
    }

    public static class Builder {
        private String level;
        private String message;
        private Map<String, Object> fields;
        private Instant timestamp;
        private String traceId;
        private String spanId;
        private String parentSpanId;
        private String requestId;
        private String source;

        public Builder level(String level) {
            this.level = level;
            return this;
        }

        public Builder message(String message) {
            this.message = message;
            return this;
        }

        public Builder fields(Map<String, Object> fields) {
            this.fields = fields;
            return this;
        }

        public Builder timestamp(Instant timestamp) {
            this.timestamp = timestamp;
            return this;
        }

        public Builder traceId(String traceId) {
            this.traceId = traceId;
            return this;
        }

        public Builder spanId(String spanId) {
            this.spanId = spanId;
            return this;
        }

        public Builder parentSpanId(String parentSpanId) {
            this.parentSpanId = parentSpanId;
            return this;
        }

        public Builder requestId(String requestId) {
            this.requestId = requestId;
            return this;
        }

        public Builder source(String source) {
            this.source = source;
            return this;
        }

        public LogEntry build() {
            return new LogEntry(this);
        }
    }

    // Getters
    public String getLevel() { return level; }
    public String getMessage() { return message; }
    public Map<String, Object> getFields() { return fields; }
    public Instant getTimestamp() { return timestamp; }
    public String getTraceId() { return traceId; }
    public String getSpanId() { return spanId; }
    public String getParentSpanId() { return parentSpanId; }
    public String getRequestId() { return requestId; }
    public String getSource() { return source; }
}
