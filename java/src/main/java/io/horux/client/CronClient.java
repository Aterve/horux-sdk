package io.horux.client;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Client for cron job check-ins via the Horux Gateway.
 */
public class CronClient {
    private static final Logger logger = Logger.getLogger(CronClient.class.getName());

    private final String gatewayUrl;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final Duration httpTimeout;

    CronClient(String gatewayUrl, Duration httpTimeout) {
        this.gatewayUrl = gatewayUrl;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(httpTimeout)
                .build();
        this.objectMapper = Json.newMapper();
        this.httpTimeout = httpTimeout;
    }

    /**
     * Send a cron job check-in with metadata.
     *
     * @param checkInKey The cron job's secret check-in key
     * @param metadata   Optional metadata (duration_ms, exit_code, host, etc.)
     * @return The check-in response
     */
    public CheckInResponse checkIn(String checkInKey, Map<String, Object> metadata) {
        if (checkInKey == null || checkInKey.isEmpty()) {
            throw new IllegalArgumentException("checkInKey is required");
        }

        try {
            HttpRequest.Builder requestBuilder = HttpRequest.newBuilder()
                    .uri(URI.create(gatewayUrl + "/api/v1/cron-jobs/check-in/" + checkInKey))
                    .header("Content-Type", "application/json")
                    .timeout(httpTimeout);

            if (metadata != null && !metadata.isEmpty()) {
                Map<String, Object> body = new HashMap<>();
                body.put("metadata", metadata);
                String jsonBody = objectMapper.writeValueAsString(body);
                requestBuilder.POST(HttpRequest.BodyPublishers.ofString(jsonBody));
            } else {
                requestBuilder.POST(HttpRequest.BodyPublishers.ofString("{}"));
            }

            HttpResponse<String> response = httpClient.send(requestBuilder.build(), HttpResponse.BodyHandlers.ofString());

            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new RuntimeException("Gateway returned error: " + response.statusCode() + " - " + response.body());
            }

            return objectMapper.readValue(response.body(), CheckInResponse.class);
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new RuntimeException("Failed to send cron job check-in", e);
        }
    }

    /**
     * Send a cron job check-in without metadata.
     *
     * @param checkInKey The cron job's secret check-in key
     * @return The check-in response
     */
    public CheckInResponse checkIn(String checkInKey) {
        return checkIn(checkInKey, null);
    }
}
