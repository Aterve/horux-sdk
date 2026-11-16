package io.horux.client;

/**
 * A rejected push.
 */
public class PushError extends RuntimeException {
    private static final long serialVersionUID = 1L;

    private final int status;
    private final String reason;
    private final boolean permanent;

    public PushError(int status, String reason, String message) {
        this(status, reason, message, false);
    }

    public PushError(int status, String reason, String message, boolean permanent) {
        super(status != 0 ? "horux: " + status + " " + reason + ": " + message : "horux: " + message);
        this.status = status;
        this.reason = reason;
        this.permanent = permanent;
    }

    /** HTTP status returned by the ingestor, or 0 for a transport failure. */
    public int getStatus() {
        return status;
    }

    /** The ingestor's machine-readable reason, when it gave one. */
    public String getReason() {
        return reason;
    }

    /**
     * True when the payload will never be accepted, so the client dropped it
     * instead of retrying.
     */
    public boolean isPermanent() {
        return permanent;
    }
}
