package io.horux.client;

/**
 * Log severity level.
 */
public enum LogLevel {
    /**
     * TRACE - most verbose, detailed diagnostic information.
     */
    TRACE("trace"),

    /**
     * DEBUG - debug-level messages.
     */
    DEBUG("debug"),

    /**
     * INFO - informational messages.
     */
    INFO("info"),

    /**
     * WARN - warning messages.
     */
    WARN("warn"),

    /**
     * ERROR - error conditions.
     */
    ERROR("error"),

    /**
     * FATAL - most severe, critical failures.
     */
    FATAL("fatal");

    private final String value;

    LogLevel(String value) {
        this.value = value;
    }

    public String getValue() {
        return value;
    }

    @Override
    public String toString() {
        return value;
    }
}
