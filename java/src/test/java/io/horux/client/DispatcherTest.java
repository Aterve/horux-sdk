package io.horux.client;

import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class DispatcherTest {

    private static HoruxConfig config(int maxBatchSize, int maxBufferSize, DropListener listener) {
        return HoruxConfig.builder()
                .ingestorUrl("http://ingestor.invalid")
                .apiToken("tenant_x_secret")
                .resourceId("resource-1")
                .flushInterval(Duration.ofHours(1))
                .maxBatchSize(maxBatchSize)
                .maxBufferSize(maxBufferSize)
                .minRetryBackoff(Duration.ofSeconds(1))
                .maxRetryBackoff(Duration.ofSeconds(60))
                .dropListener(listener)
                .build();
    }

    private static HoruxConfig config(int maxBatchSize, int maxBufferSize) {
        return config(maxBatchSize, maxBufferSize, null);
    }

    private static HoruxConfig.Builder valid() {
        return HoruxConfig.builder()
                .apiToken("tenant_x_secret")
                .resourceId("resource-1");
    }

    private static PushOutcome throttled(int accepted, Duration retryAfter) {
        return new PushOutcome(accepted, true, false, retryAfter, PushOutcome.REASON_RATE_LIMIT,
                new PushError(429, PushOutcome.REASON_RATE_LIMIT, "throttled"));
    }

    // --- SendQueue ---------------------------------------------------------

    @Test
    void queueDropsOldestWhenFull() {
        SendQueue<Integer> queue = new SendQueue<>(3, DropPolicy.OLDEST);
        for (int i = 1; i <= 5; i++) {
            queue.add(i);
        }

        assertEquals(3, queue.size());
        assertEquals(2, queue.getDropped());
        assertEquals(List.of(3, 4, 5), queue.take(3));
    }

    @Test
    void queueDropsNewestWhenConfigured() {
        SendQueue<Integer> queue = new SendQueue<>(3, DropPolicy.NEWEST);
        for (int i = 1; i <= 5; i++) {
            queue.add(i);
        }

        assertEquals(List.of(1, 2, 3), queue.take(3));
        assertEquals(2, queue.getDropped());
    }

    @Test
    void queueTakeReturnsAtMostRequested() {
        SendQueue<Integer> queue = new SendQueue<>(10, DropPolicy.OLDEST);
        for (int i = 1; i <= 5; i++) {
            queue.add(i);
        }

        assertEquals(List.of(1, 2), queue.take(2));
        assertEquals(3, queue.size());
    }

    @Test
    void queueRequeuePreservesOrder() {
        SendQueue<Integer> queue = new SendQueue<>(10, DropPolicy.OLDEST);
        queue.add(3);

        queue.requeue(new ArrayList<>(List.of(1, 2)));

        assertEquals(List.of(1, 2, 3), queue.take(3));
    }

    @Test
    void queueRequeueRespectsTheBound() {
        SendQueue<Integer> queue = new SendQueue<>(3, DropPolicy.OLDEST);
        queue.add(8);
        queue.add(9);

        assertEquals(3, queue.requeue(new ArrayList<>(List.of(1, 2, 3, 4))));
        assertEquals(List.of(4, 8, 9), queue.take(3));
    }

    // --- Backoff -----------------------------------------------------------

    @Test
    void backoffHonoursServerHint() {
        Backoff backoff = new Backoff(Duration.ofSeconds(1), Duration.ofMinutes(5));
        long now = 1_000_000L;

        Duration delay = backoff.fail(Duration.ofSeconds(10), now);

        assertTrue(delay.toMillis() >= 10_000L, "delay was " + delay);
        assertFalse(backoff.ready(now + 9_000L));
        assertTrue(backoff.ready(now + delay.toMillis()));
    }

    @Test
    void backoffGrowsExponentiallyUpToTheCap() {
        Backoff backoff = new Backoff(Duration.ofSeconds(1), Duration.ofSeconds(4));
        long now = 1_000_000L;

        for (int i = 0; i < 10; i++) {
            assertTrue(backoff.fail(Duration.ZERO, now).toMillis() <= 4_000L);
        }
    }

    @Test
    void backoffDoesNotClampTheServerHintToTheLocalMax() {
        // max bounds the client's own exponential guesswork, not an explicit
        // instruction from the other end. Clamping a `Retry-After: 300` from an
        // intermediary (CDN, API gateway, WAF under load) down to 60s retries
        // five times inside a window something just asked to stay quiet for.
        Backoff backoff = new Backoff(Duration.ofSeconds(1), Duration.ofSeconds(60));
        long now = 1_000_000L;

        Duration delay = backoff.fail(Duration.ofSeconds(300), now);

        assertTrue(delay.toMillis() >= 300_000L, "delay was " + delay);
        assertFalse(backoff.ready(now + 299_000L));
    }

    // Pacing is not a failure: holding off because the server said the allowance
    // is spent must not advance the exponential schedule, or a paced client ends
    // up backing off as if it were broken.
    @Test
    void backoffPaceDoesNotAdvanceTheAttemptCounter() {
        Backoff backoff = new Backoff(Duration.ofSeconds(1), Duration.ofSeconds(60));
        long now = 1_000_000L;

        for (int i = 0; i < 5; i++) {
            backoff.pace(Duration.ofSeconds(10), now);
        }

        assertFalse(backoff.ready(now + 9_000L));
        assertTrue(backoff.ready(now + 11_000L));
        assertTrue(backoff.fail(Duration.ZERO, now).toMillis() <= 1_000L,
                "the first failure after pacing must start from the bottom of the schedule");
    }

    // The ingestor publishes X-RateLimit-Remaining and X-RateLimit-Reset on
    // every push, explicitly so a client can pace itself instead of discovering
    // the limit by being rejected. Nothing read them.
    @Test
    void readsThePublishedAllowance() {
        assertFalse(PushOutcome.readQuotaHeaders("500", "12").spent);
        assertEquals(Duration.ZERO, PushOutcome.readQuotaHeaders("500", "12").resetAfter);

        assertTrue(PushOutcome.readQuotaHeaders("0", "12").spent);
        assertEquals(Duration.ofSeconds(12), PushOutcome.readQuotaHeaders("0", "12").resetAfter);

        assertTrue(PushOutcome.readQuotaHeaders("0", "").spent);
        assertEquals(Duration.ZERO, PushOutcome.readQuotaHeaders("0", "").resetAfter);

        assertTrue(PushOutcome.readQuotaHeaders("-3", "5").spent);

        // Absent means the quota is not enforced for this tenant; garbage must
        // not be read as a spent allowance and stall the client.
        assertFalse(PushOutcome.readQuotaHeaders("", "").spent);
        assertFalse(PushOutcome.readQuotaHeaders("lots", "soon").spent);
    }

    @Test
    void backoffResetsOnSuccess() {
        Backoff backoff = new Backoff(Duration.ofSeconds(1), Duration.ofSeconds(60));
        long now = 1_000_000L;

        backoff.fail(Duration.ofSeconds(30), now);
        backoff.succeed();

        assertTrue(backoff.ready(now));
        assertEquals(Duration.ZERO, backoff.remaining(now));
    }

    // --- PushOutcome -------------------------------------------------------

    @Test
    void interpretsFullAcceptance() {
        Map<String, Object> body = new HashMap<>();
        body.put("accepted", 100);
        body.put("rejected", 0);

        PushOutcome outcome = PushOutcome.interpret(200, null, body, 100);

        assertEquals(100, outcome.getAccepted());
        assertFalse(outcome.isRetryable());
        assertFalse(outcome.isShed());
        assertNull(outcome.getError());
    }

    @Test
    void interpretsPartialAcceptanceAndKeepsTail() {
        // The case the old client could not express: the server took part of the
        // batch and the rest has to stay queued.
        Map<String, Object> body = new HashMap<>();
        body.put("accepted", 340);
        body.put("rejected", 660);
        body.put("reason", "rate_limit");
        body.put("retry_after_ms", 12000);

        PushOutcome outcome = PushOutcome.interpret(200, null, body, 1000);

        assertEquals(340, outcome.getAccepted());
        assertTrue(outcome.isRetryable());
        assertEquals(Duration.ofSeconds(12), outcome.getRetryAfter());
        assertEquals(PushOutcome.REASON_RATE_LIMIT, outcome.getReason());
    }

    @Test
    void interpretsRateLimitWithRetryAfterHeader() {
        Map<String, Object> body = new HashMap<>();
        body.put("reason", "rate_limit");

        PushOutcome outcome = PushOutcome.interpret(429, "30", body, 500);

        assertTrue(outcome.isRetryable());
        assertFalse(outcome.isShed());
        assertEquals(Duration.ofSeconds(30), outcome.getRetryAfter());
    }

    @Test
    void interpretsStorageQuotaAsShed() {
        // Storage quota does not clear by waiting, so the data is shed rather
        // than held: holding it is what grew the buffer until the process died.
        Map<String, Object> body = new HashMap<>();
        body.put("reason", "storage_quota");
        body.put("retry_after_ms", 300000);

        PushOutcome outcome = PushOutcome.interpret(429, null, body, 500);

        assertTrue(outcome.isShed());
        assertFalse(outcome.isRetryable());
        assertEquals(Duration.ofMinutes(5), outcome.getRetryAfter());
    }

    @Test
    void storageQuotaHasAShedFloor() {
        Map<String, Object> body = new HashMap<>();
        body.put("reason", "storage_quota");

        PushOutcome outcome = PushOutcome.interpret(429, null, body, 10);

        assertTrue(outcome.getRetryAfter().compareTo(PushOutcome.MIN_SHED) >= 0);
    }

    @Test
    void dropsPayloadsThatCanNeverBeAccepted() {
        for (int status : new int[]{400, 413, 422}) {
            Map<String, Object> body = new HashMap<>();
            body.put("error", "nope");

            PushOutcome outcome = PushOutcome.interpret(status, null, body, 10);

            assertFalse(outcome.isRetryable(), "status " + status);
            assertNotNull(outcome.getError());
            assertTrue(outcome.getError().isPermanent(), "status " + status);
        }
    }

    @Test
    void keepsDataForRecoverableFailures() {
        for (int status : new int[]{401, 403, 404, 500, 502, 503}) {
            PushOutcome outcome = PushOutcome.interpret(status, null, null, 10);

            assertTrue(outcome.isRetryable(), "status " + status);
        }
    }

    @Test
    void capsAcceptedAtBatchSize() {
        Map<String, Object> body = new HashMap<>();
        body.put("accepted", 9999);

        assertEquals(10, PushOutcome.interpret(200, null, body, 10).getAccepted());
    }

    @Test
    void parsesBothRetryAfterForms() {
        assertEquals(Duration.ofSeconds(45), PushOutcome.parseRetryAfter("45"));
        assertEquals(Duration.ZERO, PushOutcome.parseRetryAfter(null));
        assertEquals(Duration.ZERO, PushOutcome.parseRetryAfter(""));
        assertEquals(Duration.ZERO, PushOutcome.parseRetryAfter("-5"));
        assertEquals(Duration.ZERO, PushOutcome.parseRetryAfter("garbage"));

        String future = ZonedDateTime.now(java.time.ZoneOffset.UTC).plusSeconds(90)
                .format(DateTimeFormatter.RFC_1123_DATE_TIME);
        Duration parsed = PushOutcome.parseRetryAfter(future);
        assertTrue(parsed.getSeconds() > 80 && parsed.getSeconds() <= 90, "parsed " + parsed);
    }

    // --- Dispatcher --------------------------------------------------------

    // A backlog is sent in batches of maxBatchSize, never as one oversized
    // request. Sending the whole buffer in a single request is how a stalled
    // client grew a payload past the ingestor's 10MB limit and wedged.
    @Test
    void drainSendsInBatches() {
        List<Integer> sizes = Collections.synchronizedList(new ArrayList<>());

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 100), "metrics", batch -> {
            sizes.add(batch.size());
            return PushOutcome.accepted(batch.size());
        })) {
            for (int i = 0; i < 25; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(true);

            assertEquals(List.of(10, 10, 5), sizes);
            assertEquals(0, dispatcher.queue().size());
        }
    }

    // On a partial acceptance the unaccepted tail goes back to the front of the
    // queue and the cycle stops: the client makes exactly the progress its
    // allowance permits, instead of hammering for the rest.
    @Test
    void drainKeepsTailAndStopsOnPartialAcceptance() {
        List<Integer> sizes = Collections.synchronizedList(new ArrayList<>());

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 100), "metrics", batch -> {
            sizes.add(batch.size());
            return throttled(4, Duration.ofSeconds(10));
        })) {
            for (int i = 0; i < 25; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(true);

            assertEquals(List.of(10), sizes, "exactly one attempt");
            assertEquals(21, dispatcher.queue().size(), "6 unsent plus the untouched 15");
            assertEquals(List.of(4, 5, 6, 7, 8, 9), dispatcher.queue().take(6));
        }
    }

    // After a rejection the client waits. Without this, a throttled client
    // retried on its normal cadence and - because a full buffer also triggered a
    // flush - ended up sending one request per recorded datapoint.
    @Test
    void drainRespectsBackoffAfterRejection() {
        AtomicInteger calls = new AtomicInteger();

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 100), "metrics", batch -> {
            calls.incrementAndGet();
            return throttled(0, Duration.ofSeconds(30));
        })) {
            for (int i = 0; i < 5; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(false);
            assertEquals(1, calls.get());

            for (int i = 0; i < 5; i++) {
                dispatcher.drain(false);
            }
            assertEquals(1, calls.get(), "no attempts during the backoff window");
            assertTrue(dispatcher.stats().getRetryBackoff().toMillis() >= 30_000L);
        }
    }

    @Test
    void drainDropsPermanentlyRejectedBatch() {
        AtomicInteger calls = new AtomicInteger();
        List<Integer> drops = Collections.synchronizedList(new ArrayList<>());
        HoruxConfig cfg = config(10, 100, (stream, count, reason) -> drops.add(count));

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(cfg, "metrics", batch -> {
            calls.incrementAndGet();
            if (batch.size() == 10) {
                return new PushOutcome(0, false, false, Duration.ZERO, PushOutcome.REASON_INVALID_PAYLOAD,
                        new PushError(400, PushOutcome.REASON_INVALID_PAYLOAD, "nope", true));
            }
            return PushOutcome.accepted(batch.size());
        })) {
            for (int i = 0; i < 15; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(true);

            assertEquals(2, calls.get(), "bad batch dropped, next one sent");
            assertEquals(0, dispatcher.queue().size());
            assertEquals(10, dispatcher.queue().getDropped());
            assertEquals(List.of(10), drops);
        }
    }

    // Over a storage quota there is nowhere for the data to go, so the client
    // sheds it at the door rather than growing a buffer for a condition that
    // needs a plan change to clear.
    @Test
    void storageQuotaShedsInsteadOfBuffering() {
        AtomicInteger calls = new AtomicInteger();

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 100), "metrics", batch -> {
            calls.incrementAndGet();
            return new PushOutcome(0, false, true, Duration.ofMinutes(5), PushOutcome.REASON_STORAGE_QUOTA,
                    new PushError(429, PushOutcome.REASON_STORAGE_QUOTA, "over quota"));
        })) {
            for (int i = 0; i < 30; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(true);

            assertEquals(0, dispatcher.queue().size(), "everything shed");
            assertTrue(dispatcher.stats().isShedding());

            for (int i = 0; i < 50; i++) {
                dispatcher.add(i);
            }
            assertEquals(0, dispatcher.queue().size(), "new data dropped at the door");

            dispatcher.drain(false);
            assertEquals(1, calls.get(), "no further attempts during the shed window");
        }
    }

    // Recording never touches the network, no matter how full the buffer is.
    @Test
    void addNeverSends() {
        AtomicInteger calls = new AtomicInteger();

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(1000, 2000), "metrics", batch -> {
            calls.incrementAndGet();
            return PushOutcome.accepted(batch.size());
        })) {
            for (int i = 0; i < 500; i++) {
                dispatcher.add(i);
            }

            assertEquals(0, calls.get(), "add() must not perform I/O");
            assertEquals(500, dispatcher.queue().size());
        }
    }

    // The bound is what keeps a throttled ingestor from turning into unbounded
    // memory growth inside the host application.
    @Test
    void addBoundsTheBufferAndReports() {
        List<Integer> drops = Collections.synchronizedList(new ArrayList<>());
        HoruxConfig cfg = config(20, 20, (stream, count, reason) -> {
            assertEquals(PushOutcome.REASON_BUFFER_FULL, reason);
            drops.add(count);
        });

        // Nothing is ever accepted, so every item beyond the bound is dropped.
        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(cfg, "metrics",
                batch -> throttled(0, Duration.ofSeconds(30)))) {
            for (int i = 0; i < 100; i++) {
                dispatcher.add(i);
            }

            assertEquals(20, dispatcher.queue().size(), "the bound holds");
            assertEquals(80, dispatcher.queue().getDropped());
            assertFalse(drops.isEmpty(), "the drop listener was called");
        }
    }

    // A client that has just used the last of its allowance stops for the window
    // the server published, instead of sending the next batch and being told 429.
    @Test
    void drainPacesItselfWhenTheAllowanceIsSpent() {
        AtomicInteger calls = new AtomicInteger();
        PushOutcome spent = new PushOutcome(10, false, false, Duration.ZERO, "", null,
                new PushOutcome.Allowance(true, Duration.ofSeconds(20)));

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 1000), "metrics", batch -> {
            calls.incrementAndGet();
            return spent;
        })) {
            for (int i = 0; i < 200; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(false);

            assertEquals(1, calls.get(), "the cycle stops once the allowance is gone");
            assertEquals(190, dispatcher.queue().size(), "the remainder is kept");
            assertTrue(dispatcher.stats().getRetryBackoff().toMillis() >= 19_000L);

            dispatcher.drain(false);
            assertEquals(1, calls.get(), "no attempt inside the reset window");

            // A final pass ignores pacing: close() has one chance to deliver.
            dispatcher.drain(true);
            assertEquals(0, dispatcher.queue().size(), "a final pass attempts everything");
        }
    }

    // A periodic cycle is bounded so a large backlog drains over several passes
    // rather than monopolising the flusher thread in one.
    @Test
    void periodicDrainIsBoundedPerCycle() {
        AtomicInteger calls = new AtomicInteger();

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 1000), "metrics", batch -> {
            calls.incrementAndGet();
            return PushOutcome.accepted(batch.size());
        })) {
            for (int i = 0; i < 1000; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(false);

            assertEquals(Dispatcher.MAX_CHUNKS_PER_FLUSH, calls.get());
            assertEquals(1000 - Dispatcher.MAX_CHUNKS_PER_FLUSH * 10, dispatcher.queue().size());
        }
    }

    // A final pass must not stop at the cycle cap. With maxBufferSize above
    // 16 x maxBatchSize, close() used to abandon the remainder with no drop
    // report, no error and no log - the data simply vanished.
    @Test
    void finalDrainKeepsGoingPastTheCycleCap() {
        AtomicInteger calls = new AtomicInteger();

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 1000), "metrics", batch -> {
            calls.incrementAndGet();
            return PushOutcome.accepted(batch.size());
        })) {
            for (int i = 0; i < 1000; i++) {
                dispatcher.queue().add(i);
            }

            dispatcher.drain(true);

            assertEquals(100, calls.get(), "1000 items in batches of 10");
            assertEquals(0, dispatcher.queue().size(), "nothing abandoned");
        }
    }

    // --- Lifecycle ---------------------------------------------------------

    // Nothing in any of the four SDK suites asserted that close() actually
    // delivers what is pending, which is how several lifecycle defects shipped.
    @Test
    void closeDeliversPendingData() {
        List<Integer> sent = Collections.synchronizedList(new ArrayList<>());

        // A one-hour flush interval guarantees the periodic pass never runs, so
        // anything that arrives came from close().
        Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 1000), "metrics", batch -> {
            sent.addAll(batch);
            return PushOutcome.accepted(batch.size());
        });

        for (int i = 0; i < 95; i++) {
            dispatcher.add(i);
        }
        dispatcher.close();

        assertEquals(95, sent.size());
        assertEquals(0, dispatcher.queue().size());
        assertEquals(0, dispatcher.queue().getDropped());
    }

    // The grace period used to be a hard-coded 5s against a 30s default
    // httpTimeout, so a request in flight at second 6 was interrupted, reported
    // as a transport failure, requeued, and then sent again by the final pass -
    // a double write of data the server had already committed. Asserting the
    // derivation beats a test that has to sleep past five seconds to see it.
    @Test
    void closeGraceTracksTheHttpTimeout() {
        assertEquals(31_000L, Dispatcher.closeGraceMillis(
                valid().httpTimeout(Duration.ofSeconds(30)).build()));
        assertEquals(121_000L, Dispatcher.closeGraceMillis(
                valid().httpTimeout(Duration.ofSeconds(120)).build()));
        assertTrue(Dispatcher.closeGraceMillis(valid().build()) > 5_000L,
                "the default 30s httpTimeout must not be cut short at 5s");
    }

    @Test
    void closeDoesNotResendAnInFlightBatch() {
        HoruxConfig cfg = HoruxConfig.builder()
                .ingestorUrl("http://ingestor.invalid")
                .apiToken("tenant_x_secret")
                .resourceId("resource-1")
                .flushInterval(Duration.ofMillis(10))
                .httpTimeout(Duration.ofMillis(400))
                .maxBatchSize(10)
                .maxBufferSize(100)
                .build();

        AtomicInteger attempts = new AtomicInteger();
        List<Integer> delivered = Collections.synchronizedList(new ArrayList<>());

        Dispatcher<Integer> dispatcher = new Dispatcher<>(cfg, "metrics", batch -> {
            attempts.incrementAndGet();
            try {
                // Still in flight when the old fixed grace period would have
                // expired relative to this config's httpTimeout.
                Thread.sleep(250L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return PushOutcome.transportFailure(e);
            }
            delivered.addAll(batch);
            return PushOutcome.accepted(batch.size());
        });

        for (int i = 0; i < 10; i++) {
            dispatcher.add(i);
        }

        dispatcher.close();

        assertEquals(1, attempts.get(), "the in-flight batch was not interrupted and resent");
        assertEquals(10, delivered.size());
    }

    @Test
    void flusherSurvivesAnErrorFromASendCycle() {
        // scheduleAtFixedRate cancels its task for good if the task throws, and
        // the guard caught only RuntimeException: an Error killed all future
        // flushing silently, with stats() still reporting a growing backlog.
        AtomicInteger calls = new AtomicInteger();

        try (Dispatcher<Integer> dispatcher = new Dispatcher<>(config(10, 100), "metrics", batch -> {
            if (calls.incrementAndGet() == 1) {
                throw new StackOverflowError("simulated");
            }
            return PushOutcome.accepted(batch.size());
        })) {
            dispatcher.queue().add(1);
            dispatcher.drainQuietly(false);
            assertEquals(1, calls.get(), "the Error did not escape the flush cycle");

            dispatcher.queue().add(2);
            dispatcher.drainQuietly(false);
            assertEquals(2, calls.get(), "flushing continued after an Error");
            assertEquals(0, dispatcher.queue().size());
        }
    }
}
