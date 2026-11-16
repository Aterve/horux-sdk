package io.horux.client;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.Map;

/**
 * Posts batches to the ingestor and interprets the responses.
 */
final class Transport {
    /**
     * Tells the ingestor this client understands a partial acceptance, i.e. that
     * it will keep the unaccepted tail rather than re-sending the whole batch.
     * Without it the server rejects a batch that does not fit whole, which a
     * client holding a large backlog can never get past.
     */
    static final String PARTIAL_ACCEPT_HEADER = "X-Horux-Partial-Accept";

    private Transport() {
    }

    @SuppressWarnings("unchecked")
    static PushOutcome push(
            HttpClient httpClient,
            ObjectMapper objectMapper,
            HoruxConfig config,
            String path,
            Object payload,
            int batchSize) {

        String jsonBody;
        try {
            jsonBody = objectMapper.writeValueAsString(payload);
        } catch (Exception e) {
            // Unencodable data will never be accepted.
            return PushOutcome.encodeFailure(e);
        }

        HttpResponse<String> response;
        try {
            HttpRequest request = HttpRequest.newBuilder()
                    .uri(URI.create(config.getIngestorUrl() + path))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + config.getApiToken())
                    .header(PARTIAL_ACCEPT_HEADER, "1")
                    .timeout(config.getHttpTimeout())
                    .POST(HttpRequest.BodyPublishers.ofString(jsonBody))
                    .build();

            response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return PushOutcome.transportFailure(e);
        } catch (Exception e) {
            // A connection problem is transient by nature; keep the data.
            return PushOutcome.transportFailure(e);
        }

        Map<String, Object> body = null;
        try {
            if (response.body() != null && !response.body().isEmpty()) {
                body = objectMapper.readValue(response.body(), Map.class);
            }
        } catch (Exception ignored) {
            // Fall back to the status alone.
        }

        String retryAfter = response.headers().firstValue("Retry-After").orElse(null);
        String remaining = response.headers()
                .firstValue(PushOutcome.RATE_LIMIT_REMAINING_HEADER).orElse("");
        String reset = response.headers()
                .firstValue(PushOutcome.RATE_LIMIT_RESET_HEADER).orElse("");

        return PushOutcome.interpret(response.statusCode(), retryAfter, remaining, reset, body, batchSize);
    }
}
