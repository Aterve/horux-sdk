package io.horux.client;

import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

/**
 * Configuration for the Horux client.
 */
public class HoruxConfig {
    /** Where pushes go when nothing else is configured. */
    public static final String DEFAULT_INGESTOR_URL = "https://ingest.horux.io";

    private final String ingestorUrl;
    private final String gatewayUrl;
    private final String apiToken;
    private final String resourceId;
    private final Duration flushInterval;
    private final int maxBatchSize;
    private final Duration httpTimeout;
    private final Map<String, String> instanceMetadata;
    private final boolean autoAddInstanceLabels;
    private final int maxBufferSize;
    private final DropPolicy dropPolicy;
    private final Duration minRetryBackoff;
    private final Duration maxRetryBackoff;
    private final DropListener dropListener;

    private HoruxConfig(Builder builder) {
        // Required fields
        if (builder.apiToken == null || builder.apiToken.isEmpty()) {
            throw new IllegalArgumentException("apiToken is required");
        }
        if (builder.resourceId == null || builder.resourceId.isEmpty()) {
            throw new IllegalArgumentException("resourceId is required");
        }

        this.ingestorUrl = resolveBaseUrl("ingestorUrl", builder.ingestorUrl, DEFAULT_INGESTOR_URL);
        this.gatewayUrl = resolveBaseUrl("gatewayUrl", builder.gatewayUrl, null);
        this.apiToken = builder.apiToken;
        this.resourceId = builder.resourceId;
        this.flushInterval = builder.flushInterval != null ? builder.flushInterval : Duration.ofSeconds(10);
        this.maxBatchSize = builder.maxBatchSize > 0 ? builder.maxBatchSize : 1000;
        this.httpTimeout = builder.httpTimeout != null ? builder.httpTimeout : Duration.ofSeconds(30);
        this.instanceMetadata = builder.instanceMetadata != null ? builder.instanceMetadata : getDefaultInstanceMetadata();
        this.autoAddInstanceLabels = builder.autoAddInstanceLabels;

        int buffer = builder.maxBufferSize > 0 ? builder.maxBufferSize : this.maxBatchSize * 10;
        this.maxBufferSize = Math.max(buffer, this.maxBatchSize);
        this.dropPolicy = builder.dropPolicy != null ? builder.dropPolicy : DropPolicy.OLDEST;
        this.minRetryBackoff = builder.minRetryBackoff != null ? builder.minRetryBackoff : Duration.ofSeconds(1);
        Duration maxBackoff = builder.maxRetryBackoff != null ? builder.maxRetryBackoff : Duration.ofSeconds(60);
        this.maxRetryBackoff = maxBackoff.compareTo(this.minRetryBackoff) < 0 ? this.minRetryBackoff : maxBackoff;
        this.dropListener = builder.dropListener;
    }

    public static Builder builder() {
        return new Builder();
    }

    /**
     * Validates a base URL at construction instead of on every request.
     *
     * <p>Only {@code null} used to fall back to the default, so {@code ""} was
     * accepted. {@code URI.create("" + path)} then yields a scheme-less URI,
     * {@code httpClient.send} throws {@link IllegalArgumentException}, and
     * {@code Transport} classifies that as a <em>transport</em> failure, which
     * is permanently retryable: the client backed off and retried a request
     * that could never succeed until the queue bound started discarding data.
     * {@code apiToken} and {@code resourceId} were already emptiness-checked.
     *
     * @param fallback the value to use when nothing is configured, or null if
     *                 the field is optional
     */
    private static String resolveBaseUrl(String field, String configured, String fallback) {
        if (configured == null) {
            return fallback;
        }

        String url = configured.trim();
        if (url.isEmpty()) {
            if (fallback == null) {
                return null;
            }
            throw new IllegalArgumentException(
                    field + " must not be empty; omit it to use " + fallback);
        }

        // Paths are appended verbatim, so a trailing slash would produce "//api".
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }

        URI uri;
        try {
            uri = URI.create(url);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException(field + " is not a valid URL: " + configured, e);
        }
        if (uri.getScheme() == null || uri.getHost() == null) {
            throw new IllegalArgumentException(
                    field + " must be absolute, e.g. http://localhost:3434; got: " + configured);
        }

        return url;
    }

    /**
     * Collect default instance metadata (hostname, Kubernetes info if available).
     */
    private static Map<String, String> getDefaultInstanceMetadata() {
        Map<String, String> metadata = new HashMap<>();

        // Add hostname
        try {
            String hostname = InetAddress.getLocalHost().getHostName();
            metadata.put("hostname", hostname);
        } catch (UnknownHostException e) {
            // Ignore if can't get hostname
        }

        // Add Kubernetes metadata from environment variables
        String podName = System.getenv("POD_NAME");
        if (podName != null && !podName.isEmpty()) {
            metadata.put("pod", podName);
        }

        String nodeName = System.getenv("NODE_NAME");
        if (nodeName != null && !nodeName.isEmpty()) {
            metadata.put("node", nodeName);
        }

        String namespace = System.getenv("NAMESPACE");
        if (namespace != null && !namespace.isEmpty()) {
            metadata.put("namespace", namespace);
        }

        String containerId = System.getenv("CONTAINER_ID");
        if (containerId != null && !containerId.isEmpty()) {
            metadata.put("container_id", containerId);
        }

        return metadata;
    }

    public static class Builder {
        private String ingestorUrl;
        private String gatewayUrl;
        private String apiToken;
        private String resourceId;
        private Duration flushInterval;
        private int maxBatchSize;
        private Duration httpTimeout;
        private Map<String, String> instanceMetadata;
        private boolean autoAddInstanceLabels = true;
        private int maxBufferSize;
        private DropPolicy dropPolicy;
        private Duration minRetryBackoff;
        private Duration maxRetryBackoff;
        private DropListener dropListener;

        /**
         * Set the ingestor URL. Must be absolute, e.g. {@code http://localhost:3434}.
         * Default: https://ingest.horux.io
         */
        public Builder ingestorUrl(String ingestorUrl) {
            this.ingestorUrl = ingestorUrl;
            return this;
        }

        /**
         * Set the Gateway URL for cron job check-ins.
         * Can also be set via HORUX_GATEWAY_URL environment variable.
         */
        public Builder gatewayUrl(String gatewayUrl) {
            this.gatewayUrl = gatewayUrl;
            return this;
        }

        /**
         * Set the API token (required).
         * Can also be set via HORUX_API_TOKEN environment variable.
         */
        public Builder apiToken(String apiToken) {
            this.apiToken = apiToken;
            return this;
        }

        /**
         * Set the resource ID (required).
         * Can also be set via HORUX_RESOURCE_ID environment variable.
         */
        public Builder resourceId(String resourceId) {
            this.resourceId = resourceId;
            return this;
        }

        /**
         * Set the flush interval.
         * Default: 10 seconds
         */
        public Builder flushInterval(Duration flushInterval) {
            this.flushInterval = flushInterval;
            return this;
        }

        /**
         * Set the maximum batch size.
         * Default: 1000
         */
        public Builder maxBatchSize(int maxBatchSize) {
            this.maxBatchSize = maxBatchSize;
            return this;
        }

        /**
         * Set the HTTP timeout.
         * Default: 30 seconds
         */
        public Builder httpTimeout(Duration httpTimeout) {
            this.httpTimeout = httpTimeout;
            return this;
        }

        /**
         * Set custom instance metadata.
         * Default: auto-detected hostname and Kubernetes info
         */
        public Builder instanceMetadata(Map<String, String> instanceMetadata) {
            this.instanceMetadata = instanceMetadata;
            return this;
        }

        /**
         * Enable/disable auto-adding instance labels to metrics.
         * Default: true
         */
        public Builder autoAddInstanceLabels(boolean autoAddInstanceLabels) {
            this.autoAddInstanceLabels = autoAddInstanceLabels;
            return this;
        }

        /**
         * Set the most items held while the ingestor is throttling or
         * unreachable. Past it the drop policy decides what goes. The bound is
         * what keeps a rate limit from turning into unbounded memory growth
         * inside the host application.
         * Default: 10x maxBatchSize
         */
        public Builder maxBufferSize(int maxBufferSize) {
            this.maxBufferSize = maxBufferSize;
            return this;
        }

        /**
         * Set which items are discarded once maxBufferSize is reached.
         * Default: DropPolicy.OLDEST
         */
        public Builder dropPolicy(DropPolicy dropPolicy) {
            this.dropPolicy = dropPolicy;
            return this;
        }

        /**
         * Set the shortest wait after a failed or throttled flush.
         * Default: 1 second
         */
        public Builder minRetryBackoff(Duration minRetryBackoff) {
            this.minRetryBackoff = minRetryBackoff;
            return this;
        }

        /**
         * Set the longest wait after a failed or throttled flush. A
         * server-supplied Retry-After takes precedence.
         * Default: 60 seconds
         */
        public Builder maxRetryBackoff(Duration maxRetryBackoff) {
            this.maxRetryBackoff = maxRetryBackoff;
            return this;
        }

        /**
         * Set a listener notified when the client discards telemetry.
         * Notifications are throttled so a sustained outage does not itself
         * become a flood.
         */
        public Builder dropListener(DropListener dropListener) {
            this.dropListener = dropListener;
            return this;
        }

        /**
         * Load configuration from environment variables.
         */
        public Builder fromEnvironment() {
            String envIngestorUrl = System.getenv("HORUX_INGESTOR_URL");
            if (envIngestorUrl != null && !envIngestorUrl.isEmpty()) {
                this.ingestorUrl = envIngestorUrl;
            }

            String envGatewayUrl = System.getenv("HORUX_GATEWAY_URL");
            if (envGatewayUrl != null && !envGatewayUrl.isEmpty()) {
                this.gatewayUrl = envGatewayUrl;
            }

            String envApiToken = System.getenv("HORUX_API_TOKEN");
            if (envApiToken != null && !envApiToken.isEmpty()) {
                this.apiToken = envApiToken;
            }

            String envResourceId = System.getenv("HORUX_RESOURCE_ID");
            if (envResourceId != null && !envResourceId.isEmpty()) {
                this.resourceId = envResourceId;
            }

            return this;
        }

        public HoruxConfig build() {
            return new HoruxConfig(this);
        }
    }

    // Getters
    public String getIngestorUrl() { return ingestorUrl; }
    public String getGatewayUrl() { return gatewayUrl; }
    public String getApiToken() { return apiToken; }
    public String getResourceId() { return resourceId; }
    public Duration getFlushInterval() { return flushInterval; }
    public int getMaxBatchSize() { return maxBatchSize; }
    public Duration getHttpTimeout() { return httpTimeout; }
    public Map<String, String> getInstanceMetadata() { return instanceMetadata; }
    public boolean isAutoAddInstanceLabels() { return autoAddInstanceLabels; }
    public int getMaxBufferSize() { return maxBufferSize; }
    public DropPolicy getDropPolicy() { return dropPolicy; }
    public Duration getMinRetryBackoff() { return minRetryBackoff; }
    public Duration getMaxRetryBackoff() { return maxRetryBackoff; }
    public DropListener getDropListener() { return dropListener; }
}
