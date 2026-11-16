package io.horux.client;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.*;

/**
 * Client for sending metrics to Horux.
 */
public class MetricsClient implements AutoCloseable {
    private final HoruxConfig config;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final Dispatcher<Metric> dispatcher;

    public MetricsClient(HoruxConfig config) {
        this.config = config;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(config.getHttpTimeout())
                .build();
        this.objectMapper = Json.newMapper();
        this.dispatcher = new Dispatcher<>(config, "metrics", this::send);
    }

    /**
     * Record a counter metric (always increasing).
     */
    public void counter(String name, double value, Map<String, String> labels) {
        Map<String, String> dimensions = getDimensions();
        Metric metric = Metric.builder()
                .name(name)
                .type(MetricType.COUNTER.getValue())
                .value(value)
                .dimensions(dimensions)
                .labels(labels)
                .build();
        record(metric);
    }

    /**
     * Record a gauge metric (can increase or decrease).
     */
    public void gauge(String name, double value, Map<String, String> labels) {
        Map<String, String> dimensions = getDimensions();
        Metric metric = Metric.builder()
                .name(name)
                .type(MetricType.GAUGE.getValue())
                .value(value)
                .dimensions(dimensions)
                .labels(labels)
                .build();
        record(metric);
    }

    /**
     * Record a histogram metric (distribution tracking).
     */
    public void histogram(String name, List<HistogramBucket> buckets, double sum, long count, Map<String, String> labels) {
        Map<String, String> dimensions = getDimensions();
        Metric metric = Metric.builder()
                .name(name)
                .type(MetricType.HISTOGRAM.getValue())
                .buckets(buckets)
                .sum(sum)
                .count(count)
                .dimensions(dimensions)
                .labels(labels)
                .build();
        record(metric);
    }

    /**
     * Record a summary metric (quantile tracking).
     */
    public void summary(String name, List<SummaryQuantile> quantiles, double sum, long count, Map<String, String> labels) {
        Map<String, String> dimensions = getDimensions();
        Metric metric = Metric.builder()
                .name(name)
                .type(MetricType.SUMMARY.getValue())
                .quantiles(quantiles)
                .sum(sum)
                .count(count)
                .dimensions(dimensions)
                .labels(labels)
                .build();
        record(metric);
    }

    /**
     * Get metrics ingestor buffer and writer stats.
     */
    public MetricsStats getStats() {
        try {
            HttpRequest httpRequest = HttpRequest.newBuilder()
                    .uri(URI.create(config.getIngestorUrl() + "/api/v1/metrics/stats"))
                    .header("Authorization", "Bearer " + config.getApiToken())
                    .timeout(config.getHttpTimeout())
                    .GET()
                    .build();

            HttpResponse<String> response = httpClient.send(httpRequest, HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new RuntimeException("Ingestor returned error: " + response.statusCode() + " - " + response.body());
            }

            return objectMapper.readValue(response.body(), MetricsStats.class);
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("Failed to get metrics stats", e);
        }
    }

    /**
     * Queue a metric for delivery.
     *
     * <p>This never performs I/O: sending happens on the dispatcher's thread, so
     * a throttled ingestor cannot slow down or block the calling code.
     */
    private void record(Metric metric) {
        dispatcher.add(metric);
    }

    /**
     * Get dimensions (instance metadata) if auto-add is enabled.
     */
    private Map<String, String> getDimensions() {
        if (config.isAutoAddInstanceLabels() && config.getInstanceMetadata() != null) {
            return new HashMap<>(config.getInstanceMetadata());
        }
        return null;
    }

    /**
     * Flush all buffered metrics to the ingestor.
     *
     * <p>Metrics are sent in batches of at most maxBatchSize. If the ingestor
     * can only take part of what is pending, the remainder stays queued for the
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
    private PushOutcome send(List<Metric> metrics) {
        Map<String, Object> payload = new HashMap<>();
        payload.put("resource_id", config.getResourceId());
        payload.put("metrics", metrics);

        return Transport.push(httpClient, objectMapper, config,
                "/api/v1/metrics/push", payload, metrics.size());
    }

    /** Package-private for tests: the mapper that defines this client's wire format. */
    ObjectMapper mapperForTests() {
        return objectMapper;
    }

    /**
     * Close the client and flush remaining metrics.
     */
    @Override
    public void close() {
        dispatcher.close();
    }
}
