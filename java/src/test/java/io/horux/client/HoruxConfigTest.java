package io.horux.client;

import org.junit.jupiter.api.Test;

import java.time.Duration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Configuration resolution had no tests at all, and two of the defects it hid
 * were in the defaults rather than the code that uses them.
 */
class HoruxConfigTest {

    private static HoruxConfig.Builder valid() {
        return HoruxConfig.builder()
                .apiToken("tenant_x_secret")
                .resourceId("resource-1");
    }

    @Test
    void omittingTheIngestorUrlUsesTheDefault() {
        assertEquals(HoruxConfig.DEFAULT_INGESTOR_URL, valid().build().getIngestorUrl());
    }

    @Test
    void anEmptyIngestorUrlIsRejectedAtConstruction() {
        // It used to be accepted, because only null fell back to the default.
        // URI.create("" + path) is scheme-less, httpClient.send throws
        // IllegalArgumentException, and Transport reads that as a *transport*
        // failure - permanently retryable. So the client backed off and retried
        // a request that could never succeed, until the queue bound began
        // discarding telemetry. Failing here costs one line in a stack trace.
        IllegalArgumentException e =
                assertThrows(IllegalArgumentException.class, () -> valid().ingestorUrl("").build());
        assertTrue(e.getMessage().contains("ingestorUrl"), e.getMessage());

        assertThrows(IllegalArgumentException.class, () -> valid().ingestorUrl("   ").build());
    }

    @Test
    void aSchemeLessIngestorUrlIsRejectedAtConstruction() {
        assertThrows(IllegalArgumentException.class, () -> valid().ingestorUrl("localhost:3434").build());
        assertThrows(IllegalArgumentException.class, () -> valid().ingestorUrl("/api/v1").build());
    }

    @Test
    void aTrailingSlashIsStrippedSoPathsDoNotDouble() {
        assertEquals("http://localhost:3434",
                valid().ingestorUrl("http://localhost:3434/").build().getIngestorUrl());
    }

    @Test
    void anOmittedGatewayUrlStaysNullBecauseCronIsOptional() {
        assertNull(valid().build().getGatewayUrl());
        assertNull(valid().gatewayUrl("").build().getGatewayUrl());
        assertThrows(IllegalArgumentException.class, () -> valid().gatewayUrl("nope").build());
    }

    @Test
    void credentialsAreRequired() {
        assertThrows(IllegalArgumentException.class,
                () -> HoruxConfig.builder().resourceId("resource-1").build());
        assertThrows(IllegalArgumentException.class,
                () -> HoruxConfig.builder().apiToken("tenant_x_secret").build());
    }

    @Test
    void theBufferDefaultsToTenTimesTheBatchAndNeverBelowIt() {
        assertEquals(1000, valid().build().getMaxBatchSize());
        assertEquals(10_000, valid().build().getMaxBufferSize());

        assertEquals(500, valid().maxBatchSize(50).maxBufferSize(500).build().getMaxBufferSize());
        assertEquals(50, valid().maxBatchSize(50).maxBufferSize(5).build().getMaxBufferSize(),
                "a buffer smaller than one batch could never send anything");
    }

    @Test
    void maxBackoffIsLiftedToMinRatherThanInverted() {
        HoruxConfig config = valid()
                .minRetryBackoff(Duration.ofSeconds(5))
                .maxRetryBackoff(Duration.ofSeconds(1))
                .build();

        assertEquals(Duration.ofSeconds(5), config.getMaxRetryBackoff());
    }
}
