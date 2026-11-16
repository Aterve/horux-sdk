package io.horux.client;

/**
 * Notified when the client discards telemetry it could not deliver.
 *
 * <p>Notifications are throttled, so a sustained outage does not itself become
 * a flood.
 */
@FunctionalInterface
public interface DropListener {
    /**
     * @param stream "metrics" or "logs"
     * @param count  how many items were dropped
     * @param reason rate_limit, storage_quota, buffer_full, invalid_payload, ...
     */
    void onDrop(String stream, int count, String reason);
}
