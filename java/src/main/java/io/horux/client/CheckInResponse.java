package io.horux.client;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Response from a cron job check-in.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class CheckInResponse {

    @JsonProperty("success")
    private boolean success;

    @JsonProperty("message")
    private String message;

    @JsonProperty("next_expected_at")
    private String nextExpectedAt;

    public boolean isSuccess() { return success; }
    public String getMessage() { return message; }
    public String getNextExpectedAt() { return nextExpectedAt; }
}
