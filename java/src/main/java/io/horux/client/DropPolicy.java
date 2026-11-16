package io.horux.client;

/**
 * Which end of a full send buffer is sacrificed when the ingestor cannot keep up.
 */
public enum DropPolicy {
    /**
     * Discard the oldest pending items. The default: during an outage recent
     * telemetry is usually the more useful half.
     */
    OLDEST,

    /**
     * Discard incoming items and preserve the existing backlog.
     */
    NEWEST
}
