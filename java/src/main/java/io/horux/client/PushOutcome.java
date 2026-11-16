package io.horux.client;

import java.time.Duration;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.Map;

/**
 * What the ingestor did with one batch.
 *
 * <p>The distinction that matters is whether the leftover is worth keeping:
 * anything the server might accept later is retried, a payload it will never
 * accept is dropped rather than retried forever, and data that has nowhere to
 * go at all (storage quota) is shed.
 */
public class PushOutcome {
    /** Rejection reasons reported by the ingestor. */
    public static final String REASON_RATE_LIMIT = "rate_limit";
    public static final String REASON_STORAGE_QUOTA = "storage_quota";
    public static final String REASON_BUFFER_FULL = "buffer_full";
    public static final String REASON_BACKEND_UNAVAILABLE = "backend_unavailable";
    public static final String REASON_TRANSPORT = "transport";
    public static final String REASON_INVALID_PAYLOAD = "invalid_payload";
    public static final String REASON_REJECTED = "rejected";

    /** Floor for how long to shed data after a storage quota rejection. */
    static final Duration MIN_SHED = Duration.ofMinutes(1);

    /** The allowance the ingestor publishes on every push. */
    static final String RATE_LIMIT_REMAINING_HEADER = "X-RateLimit-Remaining";
    static final String RATE_LIMIT_RESET_HEADER = "X-RateLimit-Reset";

    /**
     * The tenant's ingestion allowance, as read off the response headers.
     *
     * <p>The ingestor publishes these on every push explicitly "so a client
     * can pace itself instead of discovering the limit by being rejected";
     * reading them is what stops that costing a wasted round trip and a 429.
     */
    static final class Allowance {
        /** No headers, or headers that did not parse: assume nothing. */
        static final Allowance UNKNOWN = new Allowance(false, Duration.ZERO);

        final boolean spent;
        final Duration resetAfter;

        Allowance(boolean spent, Duration resetAfter) {
            this.spent = spent;
            this.resetAfter = resetAfter == null ? Duration.ZERO : resetAfter;
        }
    }

    /**
     * Read the published allowance.
     *
     * <p>{@code X-RateLimit-Reset} is a whole number of <em>seconds until the
     * full allowance is available again</em>, not an absolute time. An absent
     * header means the quota is not enforced for this tenant, and a malformed
     * one must not be read as a spent allowance - that would stall a client on
     * garbage.
     */
    static Allowance readQuotaHeaders(String remainingHeader, String resetHeader) {
        long remaining;
        try {
            remaining = Long.parseLong(remainingHeader.trim());
        } catch (RuntimeException ignored) {
            return Allowance.UNKNOWN;
        }

        if (remaining > 0) {
            return Allowance.UNKNOWN;
        }

        try {
            long seconds = Long.parseLong(resetHeader.trim());
            return new Allowance(true, seconds > 0 ? Duration.ofSeconds(seconds) : Duration.ZERO);
        } catch (RuntimeException ignored) {
            return new Allowance(true, Duration.ZERO);
        }
    }

    private final int accepted;
    private final boolean retry;
    private final boolean shed;
    private final Duration retryAfter;
    private final String reason;
    private final PushError error;
    private final Allowance allowance;

    PushOutcome(int accepted, boolean retry, boolean shed, Duration retryAfter, String reason, PushError error) {
        this(accepted, retry, shed, retryAfter, reason, error, Allowance.UNKNOWN);
    }

    PushOutcome(int accepted, boolean retry, boolean shed, Duration retryAfter, String reason,
                PushError error, Allowance allowance) {
        this.accepted = accepted;
        this.retry = retry;
        this.shed = shed;
        this.retryAfter = retryAfter == null ? Duration.ZERO : retryAfter;
        this.reason = reason == null ? "" : reason;
        this.error = error;
        this.allowance = allowance == null ? Allowance.UNKNOWN : allowance;
    }

    /** True when the ingestor published a remaining quota of zero for this tenant. */
    public boolean isAllowanceSpent() {
        return allowance.spent;
    }

    /** How long until the published allowance is available again. */
    public Duration getResetAfter() {
        return allowance.resetAfter;
    }

    /** A clean, complete acceptance. */
    static PushOutcome accepted(int accepted) {
        return new PushOutcome(accepted, false, false, Duration.ZERO, "", null);
    }

    /** A connection problem is transient by nature, so the data is kept. */
    static PushOutcome transportFailure(Throwable cause) {
        String message = cause.getMessage() != null ? cause.getMessage() : cause.toString();
        return new PushOutcome(0, true, false, Duration.ZERO, REASON_TRANSPORT,
                new PushError(0, REASON_TRANSPORT, message));
    }

    /** Data that could not even be serialized will never be accepted. */
    static PushOutcome encodeFailure(Throwable cause) {
        String message = cause.getMessage() != null ? cause.getMessage() : cause.toString();
        return new PushOutcome(0, false, false, Duration.ZERO, REASON_INVALID_PAYLOAD,
                new PushError(0, REASON_INVALID_PAYLOAD, message, true));
    }

    /**
     * Turn an ingestor response into an outcome.
     *
     * @param status      the HTTP status
     * @param retryAfterHeader the Retry-After header, or null
     * @param body        the parsed JSON body, or null if it could not be read
     * @param batchSize   how many items were sent
     */
    static PushOutcome interpret(int status, String retryAfterHeader, Map<String, Object> body, int batchSize) {
        return interpret(status, retryAfterHeader, "", "", body, batchSize);
    }

    /** As above, with the tenant's published allowance. */
    static PushOutcome interpret(int status, String retryAfterHeader, String remainingHeader,
                                 String resetHeader, Map<String, Object> body, int batchSize) {
        String message = firstNonEmpty(stringValue(body, "error"), stringValue(body, "message"), "push rejected");
        String reason = stringValue(body, "reason");

        Duration retryAfter = Duration.ofMillis(longValue(body, "retry_after_ms"));
        if (retryAfter.isZero()) {
            retryAfter = parseRetryAfter(retryAfterHeader);
        }

        int accepted = (int) Math.max(0, Math.min(longValue(body, "accepted"), batchSize));

        Allowance allowance = readQuotaHeaders(
                remainingHeader == null ? "" : remainingHeader,
                resetHeader == null ? "" : resetHeader);

        if (REASON_STORAGE_QUOTA.equals(reason)) {
            // Waiting does not clear a storage quota, so the data is shed:
            // holding it only grows the buffer until the host process dies.
            Duration shedFor = retryAfter.compareTo(MIN_SHED) < 0 ? MIN_SHED : retryAfter;
            return new PushOutcome(accepted, false, true, shedFor, REASON_STORAGE_QUOTA,
                    new PushError(status, REASON_STORAGE_QUOTA, message));
        }

        if (status == 200) {
            if (accepted >= batchSize) {
                return new PushOutcome(batchSize, false, false, Duration.ZERO, "", null, allowance);
            }

            // Partially accepted: keep the tail, come back when told to.
            String partialReason = reason.isEmpty() ? REASON_REJECTED : reason;
            return new PushOutcome(accepted, true, false, retryAfter, partialReason,
                    new PushError(status, partialReason, message));
        }

        if (status == 400 || status == 413 || status == 422) {
            // The payload itself is unacceptable; retrying it forever would
            // block every later batch behind data that can never be sent.
            String payloadReason = reason.isEmpty() ? REASON_INVALID_PAYLOAD : reason;
            return new PushOutcome(accepted, false, false, Duration.ZERO, payloadReason,
                    new PushError(status, payloadReason, message, true));
        }

        // Rate limits, auth problems and server errors can all clear on their
        // own, so the data is kept.
        String keepReason = reason;
        if (keepReason.isEmpty()) {
            keepReason = status == 429 ? REASON_RATE_LIMIT : REASON_BACKEND_UNAVAILABLE;
        }
        return new PushOutcome(accepted, true, false, retryAfter, keepReason,
                new PushError(status, keepReason, message));
    }

    /** Reads both forms of Retry-After, seconds and HTTP date. */
    static Duration parseRetryAfter(String value) {
        if (value == null || value.isEmpty()) {
            return Duration.ZERO;
        }

        try {
            long seconds = Long.parseLong(value.trim());
            return seconds > 0 ? Duration.ofSeconds(seconds) : Duration.ZERO;
        } catch (NumberFormatException ignored) {
            // Fall through to the HTTP-date form.
        }

        try {
            ZonedDateTime when = ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME);
            Duration delta = Duration.between(ZonedDateTime.now(when.getZone()), when);
            return delta.isNegative() ? Duration.ZERO : delta;
        } catch (DateTimeParseException ignored) {
            return Duration.ZERO;
        }
    }

    /**
     * How many items were taken. The ingestor always takes a prefix of the
     * batch, so everything after this index is exactly what is left over.
     */
    public int getAccepted() {
        return accepted;
    }

    /** The leftover is worth keeping and sending again. */
    public boolean isRetryable() {
        return retry;
    }

    /** The leftover should be dropped and further data discarded for a while. */
    public boolean isShed() {
        return shed;
    }

    /** The server's hint for when to come back. */
    public Duration getRetryAfter() {
        return retryAfter;
    }

    public String getReason() {
        return reason;
    }

    public PushError getError() {
        return error;
    }

    private static String stringValue(Map<String, Object> body, String key) {
        if (body == null) {
            return "";
        }
        Object value = body.get(key);
        return value instanceof String ? (String) value : "";
    }

    private static long longValue(Map<String, Object> body, String key) {
        if (body == null) {
            return 0L;
        }
        Object value = body.get(key);
        return value instanceof Number ? ((Number) value).longValue() : 0L;
    }

    private static String firstNonEmpty(String... values) {
        for (String value : values) {
            if (value != null && !value.isEmpty()) {
                return value;
            }
        }
        return "";
    }
}
