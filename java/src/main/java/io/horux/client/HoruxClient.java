package io.horux.client;

/**
 * Unified Horux client for sending metrics and logs.
 * <p>
 * This client wraps {@link MetricsClient}, {@link LogsClient}, and optionally
 * {@link CronClient} and provides a single entry point for all Horux operations.
 * </p>
 *
 * <h2>Example Usage:</h2>
 * <pre>{@code
 * HoruxConfig config = HoruxConfig.builder()
 *     .apiToken("your-api-token")
 *     .resourceId("your-resource-id")
 *     .gatewayUrl("http://localhost:3000")
 *     .fromEnvironment()
 *     .build();
 *
 * try (HoruxClient client = new HoruxClient(config)) {
 *     // Send metrics
 *     client.metrics.counter("http.requests.total", 1, Map.of("method", "GET", "status", "200"));
 *     client.metrics.gauge("memory.used", 1024.5, Map.of("type", "heap"));
 *
 *     // Send logs
 *     client.logs.info("User logged in", Map.of("userId", "123", "ip", "192.168.1.1"));
 *     client.logs.error("Database connection failed", Map.of("error", "timeout"));
 *
 *     // Cron job check-in (requires gatewayUrl)
 *     if (client.getCron() != null) {
 *         client.getCron().checkIn("your-check-in-key");
 *     }
 * }
 * }</pre>
 */
public class HoruxClient implements AutoCloseable {
    /**
     * Metrics client for recording metrics.
     */
    public final MetricsClient metrics;

    /**
     * Logs client for sending logs.
     */
    public final LogsClient logs;

    private final CronClient cron;

    /**
     * Create a new Horux client with the given configuration.
     *
     * @param config Configuration for the client
     */
    public HoruxClient(HoruxConfig config) {
        this.metrics = new MetricsClient(config);
        this.logs = new LogsClient(config);

        if (config.getGatewayUrl() != null && !config.getGatewayUrl().isEmpty()) {
            this.cron = new CronClient(config.getGatewayUrl(), config.getHttpTimeout());
        } else {
            this.cron = null;
        }
    }

    /**
     * Get the cron job check-in client.
     * Returns null if gatewayUrl was not configured.
     */
    public CronClient getCron() {
        return cron;
    }

    /**
     * Close both clients and flush any remaining data.
     */
    @Override
    public void close() {
        metrics.close();
        logs.close();
    }
}
