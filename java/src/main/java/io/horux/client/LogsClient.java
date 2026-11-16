package io.horux.client;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.*;

/**
 * Client for sending logs to Horux.
 */
public class LogsClient implements AutoCloseable {
    private final HoruxConfig config;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final Dispatcher<LogEntry> dispatcher;

    public LogsClient(HoruxConfig config) {
        this.config = config;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(config.getHttpTimeout())
                .build();
        this.objectMapper = Json.newMapper();
        this.dispatcher = new Dispatcher<>(config, "logs", this::send);
    }

    /**
     * Log a trace-level message.
     */
    public void trace(String message, Map<String, Object> fields) {
        log(LogLevel.TRACE, message, fields);
    }

    /**
     * Log a debug-level message.
     */
    public void debug(String message, Map<String, Object> fields) {
        log(LogLevel.DEBUG, message, fields);
    }

    /**
     * Log an info-level message.
     */
    public void info(String message, Map<String, Object> fields) {
        log(LogLevel.INFO, message, fields);
    }

    /**
     * Log a warning-level message.
     */
    public void warn(String message, Map<String, Object> fields) {
        log(LogLevel.WARN, message, fields);
    }

    /**
     * Log an error-level message.
     */
    public void error(String message, Map<String, Object> fields) {
        log(LogLevel.ERROR, message, fields);
    }

    /**
     * Log a fatal-level message.
     */
    public void fatal(String message, Map<String, Object> fields) {
        log(LogLevel.FATAL, message, fields);
    }

    /**
     * Log with distributed tracing context.
     */
    public void withTrace(LogLevel level, String message, Map<String, Object> fields,
                         String traceId, String spanId, String requestId) {
        LogEntry entry = LogEntry.builder()
                .level(level.getValue())
                .message(message)
                .fields(fields)
                .traceId(traceId)
                .spanId(spanId)
                .requestId(requestId)
                .build();
        record(entry);
    }

    /**
     * Log a message with the specified level.
     */
    private void log(LogLevel level, String message, Map<String, Object> fields) {
        LogEntry entry = LogEntry.builder()
                .level(level.getValue())
                .message(message)
                .fields(fields)
                .build();
        record(entry);
    }

    /**
     * Get logs ingestor buffer and writer stats.
     */
    public LogsStats getStats() {
        try {
            HttpRequest httpRequest = HttpRequest.newBuilder()
                    .uri(URI.create(config.getIngestorUrl() + "/api/v1/logs/stats"))
                    .header("Authorization", "Bearer " + config.getApiToken())
                    .timeout(config.getHttpTimeout())
                    .GET()
                    .build();

            HttpResponse<String> response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new RuntimeException("Ingestor returned error: " + response.statusCode() + " - " + response.body());
            }

            return objectMapper.readValue(response.body(), LogsStats.class);
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("Failed to get logs stats", e);
        }
    }

    /**
     * Queue a log entry for delivery.
     *
     * <p>This never performs I/O: sending happens on the dispatcher's thread, so
     * a throttled ingestor cannot slow down or block the calling code.
     */
    private void record(LogEntry entry) {
        dispatcher.add(entry);
    }

    /**
     * Flush all buffered logs to the ingestor.
     *
     * <p>Logs are sent in batches of at most maxBatchSize. If the ingestor can
     * only take part of what is pending, the remainder stays queued for the
     * next cycle.
     */
    public void flush() {
        dispatcher.flush();
    }

    /**
     * The state of the client-side send queue: what is pending, what has been
     * dropped, and whether the client is currently backing off.
     */
    public QueueStats getQueueStats() {
        return dispatcher.stats();
    }

    /**
     * Deliver one batch to the ingestor.
     */
    private PushOutcome send(List<LogEntry> logs) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("resource_id", config.getResourceId());
        payload.put("format", "json");
        // Infrastructure/instance identifiers
        payload.put("dimensions", config.getInstanceMetadata());
        payload.put("logs", logs);

        return Transport.push(httpClient, objectMapper, config,
                "/api/v1/logs/push", payload, logs.size());
    }

    /** Package-private for tests: the mapper that defines this client's wire format. */
    ObjectMapper mapperForTests() {
        return objectMapper;
    }

    /**
     * Close the client and flush remaining logs.
     */
    @Override
    public void close() {
        dispatcher.close();
    }
}
