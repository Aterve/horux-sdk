package io.horux.client;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.Collections;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Pins the JSON this client puts on the wire.
 *
 * <p>The ingestor decodes {@code timestamp} into Go's {@code *time.Time}, which
 * accepts an RFC 3339 <em>string</em> and nothing else. Jackson's default
 * {@code WRITE_DATES_AS_TIMESTAMPS} made every push a number, so the server
 * answered {@code 400 invalid request body} and the dispatcher - correctly
 * treating 400 as permanent - dropped the batch. Every test still passed,
 * because no test looked at the bytes. These do.
 */
class WireFormatTest {

    private static final ObjectMapper mapper = Json.newMapper();

    private static JsonNode serialise(Object value) {
        try {
            return mapper.readTree(mapper.writeValueAsString(value));
        } catch (Exception e) {
            throw new AssertionError("serialisation failed", e);
        }
    }

    // --- The regression itself ---------------------------------------------

    @Test
    void metricTimestampIsAnIso8601String() {
        Metric metric = Metric.builder()
                .name("http_requests_total")
                .type(MetricType.COUNTER.getValue())
                .value(1.0)
                .timestamp(Instant.parse("2026-10-13T09:00:00.123456789Z"))
                .build();

        JsonNode json = serialise(metric);

        assertTrue(json.get("timestamp").isTextual(),
                "timestamp must be a JSON string; Go's time.Time rejects a number with "
                        + "\"input is not a JSON string\". Got: " + json.get("timestamp"));
        assertFalse(json.get("timestamp").isNumber(), "timestamp must not be a numeric epoch");
        assertEquals("2026-10-13T09:00:00.123456789Z", json.get("timestamp").asText());
    }

    @Test
    void logEntryTimestampIsAnIso8601String() {
        LogEntry entry = LogEntry.builder()
                .level(LogLevel.INFO.getValue())
                .message("started")
                .timestamp(Instant.parse("2026-10-13T09:00:00.123456789Z"))
                .build();

        JsonNode json = serialise(entry);

        assertTrue(json.get("timestamp").isTextual(),
                "timestamp must be a JSON string; got: " + json.get("timestamp"));
        assertEquals("2026-10-13T09:00:00.123456789Z", json.get("timestamp").asText());
    }

    @Test
    void serialisedTimestampParsesBackAsRfc3339() {
        // Go's time.Time unmarshals with time.RFC3339; anything it accepts,
        // DateTimeFormatter.ISO_OFFSET_DATE_TIME accepts too. If this parses,
        // the server's decoder will not reject it.
        Metric metric = Metric.builder()
                .name("m")
                .type(MetricType.GAUGE.getValue())
                .value(0.0)
                .build();

        String timestamp = serialise(metric).get("timestamp").asText();

        Instant parsed = Instant.from(DateTimeFormatter.ISO_OFFSET_DATE_TIME.parse(timestamp));
        assertTrue(timestamp.endsWith("Z") || timestamp.contains("+") || timestamp.contains("-"),
                "timestamp must carry a zone offset: " + timestamp);
        assertEquals(metric.getTimestamp(), parsed);
    }

    @Test
    void whicheverClientSerialisesIsTheSameWireFormat() {
        // Both clients built their own mapper, which is how one of them could be
        // wrong while the other was right. They share one factory now.
        HoruxConfig config = HoruxConfig.builder()
                .ingestorUrl("http://ingestor.invalid")
                .apiToken("tenant_x_secret")
                .resourceId("resource-1")
                .flushInterval(java.time.Duration.ofHours(1))
                .build();

        try (MetricsClient metrics = new MetricsClient(config); LogsClient logs = new LogsClient(config)) {
            Instant at = Instant.parse("2026-10-13T09:00:00Z");
            assertEquals("\"2026-10-13T09:00:00Z\"", metrics.mapperForTests().writeValueAsString(at));
            assertEquals("\"2026-10-13T09:00:00Z\"", logs.mapperForTests().writeValueAsString(at));
        } catch (Exception e) {
            throw new AssertionError(e);
        }
    }

    // --- The rest of the push payload --------------------------------------

    @Test
    void metricsPushPayloadUsesSnakeCaseResourceId() {
        JsonNode json = serialise(Map.of(
                "resource_id", "resource-1",
                "metrics", List.of(Metric.builder()
                        .name("m")
                        .type(MetricType.GAUGE.getValue())
                        .value(2.0)
                        .build())));

        assertEquals("resource-1", json.get("resource_id").asText());
        assertEquals(1, json.get("metrics").size());
        assertEquals("gauge", json.get("metrics").get(0).get("type").asText());
        assertEquals(2.0, json.get("metrics").get(0).get("value").asDouble());
    }

    @Test
    void nullFieldsAreOmittedRatherThanSentAsNull() {
        // The ingestor validates per type: a `value` of null on a histogram, or
        // empty `buckets` on a counter, is a 400 the client would then drop.
        JsonNode json = serialise(Metric.builder()
                .name("m")
                .type(MetricType.COUNTER.getValue())
                .value(1.0)
                .build());

        assertFalse(json.has("buckets"), "buckets must be omitted for a counter");
        assertFalse(json.has("quantiles"), "quantiles must be omitted for a counter");
        assertFalse(json.has("labels"), "an unset label map must be omitted, not null");
    }

    // --- Server-side stats -------------------------------------------------

    // The ingestor returns the `writer` block only to the internal service key,
    // because its counters span every tenant. getWriter() therefore returned
    // null for every SDK caller, and the fields it mapped could never hold a
    // true value - so the block is gone rather than present and zero.
    @Test
    void metricsStatsReadsTheBufferBlockAndTheDropCounters() {
        String json = "{\"buffer\":{\"queued_metrics\":12,\"capacity\":1000,"
                + "\"utilization_percent\":1.2,\"dropped_metrics\":7,\"dropped_batches\":1}}";

        MetricsStats stats;
        try {
            stats = mapper.readValue(json, MetricsStats.class);
        } catch (Exception e) {
            throw new AssertionError(e);
        }

        assertEquals(12L, stats.getBuffer().getQueuedMetrics());
        assertEquals(1000L, stats.getBuffer().getCapacity());
        assertEquals(7L, stats.getBuffer().getDroppedMetrics());
        assertEquals(1L, stats.getBuffer().getDroppedBatches());
    }

    @Test
    void statsIgnoresAWriterBlockRatherThanFailing() {
        // An internal caller, or an older ingestor, still sends `writer`. It is
        // not part of this type, and an unknown field must not throw.
        String json = "{\"buffer\":{\"queued_logs\":3,\"capacity\":500},"
                + "\"writer\":{\"logs_written\":99,\"batches_written\":9}}";

        LogsStats stats;
        try {
            stats = mapper.readValue(json, LogsStats.class);
        } catch (Exception e) {
            throw new AssertionError(e);
        }

        assertEquals(3L, stats.getBuffer().getQueuedLogs());
        assertEquals(0L, stats.getBuffer().getDroppedLogs(), "a field the server omits reads as 0");
    }

    @Test
    void logFieldsKeepTheirSnakeCaseWireNames() {
        JsonNode json = serialise(LogEntry.builder()
                .level(LogLevel.ERROR.getValue())
                .message("boom")
                .traceId("t")
                .spanId("s")
                .parentSpanId("p")
                .requestId("r")
                .source("svc")
                .fields(Collections.singletonMap("attempt", 3))
                .build());

        assertEquals("error", json.get("level").asText());
        assertEquals("t", json.get("trace_id").asText());
        assertEquals("s", json.get("span_id").asText());
        assertEquals("p", json.get("parent_span_id").asText());
        assertEquals("r", json.get("request_id").asText());
        assertEquals(3, json.get("fields").get("attempt").asInt());
    }
}
