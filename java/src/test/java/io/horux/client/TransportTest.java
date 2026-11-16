package io.horux.client;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.net.http.HttpClient;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The request Transport actually puts on the wire, against a real HTTP server.
 *
 * <p>Nothing verified this before: a grep for the partial-accept header found no
 * hit anywhere in the suite, so the single most important bit of the
 * backpressure contract could have regressed with every test still green - and
 * the client would have silently reverted to all-or-nothing rejection, which a
 * client holding a large backlog can never get past.
 */
class TransportTest {

    private HttpServer server;
    private String baseUrl;
    private final List<Map<String, String>> requestHeaders =
            Collections.synchronizedList(new ArrayList<>());
    private final List<String> requestBodies = Collections.synchronizedList(new ArrayList<>());

    private void start(Consumer<HttpExchange> responder) throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/metrics/push", exchange -> {
            Map<String, String> headers = new HashMap<>();
            exchange.getRequestHeaders().forEach((key, values) ->
                    headers.put(key.toLowerCase(), String.join(",", values)));
            requestHeaders.add(headers);

            try (InputStream in = exchange.getRequestBody()) {
                requestBodies.add(new String(in.readAllBytes(), StandardCharsets.UTF_8));
            }

            responder.accept(exchange);
        });
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private static void respond(HttpExchange exchange, int status, String body) {
        try {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        } catch (IOException e) {
            throw new AssertionError(e);
        }
    }

    @BeforeEach
    void reset() {
        requestHeaders.clear();
        requestBodies.clear();
    }

    @AfterEach
    void stop() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
    }

    private PushOutcome push(int batchSize) {
        HoruxConfig config = HoruxConfig.builder()
                .ingestorUrl(baseUrl)
                .apiToken("tenant_x_secret")
                .resourceId("resource-1")
                .httpTimeout(Duration.ofSeconds(5))
                .build();

        Map<String, Object> payload = new HashMap<>();
        payload.put("resource_id", "resource-1");
        payload.put("metrics", Collections.singletonList(Metric.builder()
                .name("m")
                .type(MetricType.GAUGE.getValue())
                .value(1.0)
                .build()));

        return Transport.push(HttpClient.newHttpClient(), Json.newMapper(), config,
                "/api/v1/metrics/push", payload, batchSize);
    }

    @Test
    void sendsThePartialAcceptHeaderOnEveryPush() throws IOException {
        start(exchange -> respond(exchange, 200, "{\"accepted\":1}"));

        push(1);

        assertEquals(1, requestHeaders.size());
        assertEquals("1", requestHeaders.get(0).get(Transport.PARTIAL_ACCEPT_HEADER.toLowerCase()),
                "without this header the ingestor rejects a batch that does not fit whole");
        assertEquals("Bearer tenant_x_secret", requestHeaders.get(0).get("authorization"));
        assertTrue(requestHeaders.get(0).get("content-type").startsWith("application/json"));
    }

    @Test
    void theBodyOnTheWireCarriesAnIso8601Timestamp() throws IOException {
        start(exchange -> respond(exchange, 200, "{\"accepted\":1}"));

        push(1);

        String body = requestBodies.get(0);
        assertTrue(body.contains("\"resource_id\":\"resource-1\""), body);
        assertTrue(body.matches(".*\"timestamp\":\"\\d{4}-\\d{2}-\\d{2}T.*"),
                "timestamp must reach the server as a quoted RFC 3339 string, not a number: " + body);
    }

    @Test
    void readsAPartialAcceptanceAndKeepsTheTail() throws IOException {
        start(exchange -> respond(exchange, 200,
                "{\"accepted\":40,\"rejected\":60,\"reason\":\"rate_limit\",\"retry_after_ms\":12000}"));

        PushOutcome outcome = push(100);

        assertEquals(40, outcome.getAccepted());
        assertTrue(outcome.isRetryable());
        assertEquals(Duration.ofSeconds(12), outcome.getRetryAfter());
    }

    @Test
    void readsTheRetryAfterHeaderOnAThrottle() throws IOException {
        start(exchange -> {
            exchange.getResponseHeaders().add("Retry-After", "30");
            respond(exchange, 429, "{\"reason\":\"rate_limit\"}");
        });

        PushOutcome outcome = push(10);

        assertTrue(outcome.isRetryable());
        assertEquals(Duration.ofSeconds(30), outcome.getRetryAfter());
    }

    @Test
    void readsThePublishedAllowanceOffTheResponse() throws IOException {
        start(exchange -> {
            exchange.getResponseHeaders().add("X-RateLimit-Limit", "1000");
            exchange.getResponseHeaders().add("X-RateLimit-Remaining", "0");
            exchange.getResponseHeaders().add("X-RateLimit-Reset", "18");
            respond(exchange, 200, "{\"accepted\":10}");
        });

        PushOutcome outcome = push(10);

        assertEquals(10, outcome.getAccepted());
        assertTrue(outcome.isAllowanceSpent(),
                "the ingestor publishes these so a client can pace itself rather than be rejected");
        assertEquals(Duration.ofSeconds(18), outcome.getResetAfter());
    }

    @Test
    void anUnenforcedQuotaLeavesPacingAlone() throws IOException {
        start(exchange -> respond(exchange, 200, "{\"accepted\":10}"));

        PushOutcome outcome = push(10);

        assertFalse(outcome.isAllowanceSpent());
        assertEquals(Duration.ZERO, outcome.getResetAfter());
    }

    @Test
    void treatsAnUnacceptablePayloadAsPermanent() throws IOException {
        start(exchange -> respond(exchange, 400, "{\"error\":\"invalid request body\"}"));

        PushOutcome outcome = push(10);

        assertFalse(outcome.isRetryable());
        assertNotNull(outcome.getError());
        assertTrue(outcome.getError().isPermanent());
        // This is the shape the Instant defect produced on every single push.
        assertEquals(400, outcome.getError().getStatus());
        assertTrue(outcome.getError().getMessage().contains("invalid request body"),
                outcome.getError().getMessage());
    }

    @Test
    void keepsDataWhenTheServerIsUnreachable() {
        HoruxConfig config = HoruxConfig.builder()
                .ingestorUrl("http://127.0.0.1:1")
                .apiToken("tenant_x_secret")
                .resourceId("resource-1")
                .httpTimeout(Duration.ofSeconds(2))
                .build();

        PushOutcome outcome = Transport.push(HttpClient.newHttpClient(), Json.newMapper(), config,
                "/api/v1/metrics/push", Collections.singletonMap("resource_id", "r"), 10);

        assertTrue(outcome.isRetryable(), "a connection problem is transient; the data is kept");
        assertEquals(PushOutcome.REASON_TRANSPORT, outcome.getReason());
    }
}
