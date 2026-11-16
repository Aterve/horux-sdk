package client

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// LogLevel represents the log severity level
type LogLevel string

const (
	LogLevelTrace LogLevel = "trace"
	LogLevelDebug LogLevel = "debug"
	LogLevelInfo  LogLevel = "info"
	LogLevelWarn  LogLevel = "warn"
	LogLevelError LogLevel = "error"
	LogLevelFatal LogLevel = "fatal"
)

// LogsClient handles sending logs to the data-ingestor
type LogsClient struct {
	config     *Config
	httpClient *http.Client
	dispatch   *dispatcher[*LogEntry]
}

// NewLogsClient creates a new logs client
func NewLogsClient(config *Config) (*LogsClient, error) {
	if err := config.Validate(); err != nil {
		return nil, fmt.Errorf("invalid config: %w", err)
	}

	client := &LogsClient{
		config: config,
		httpClient: &http.Client{
			Timeout: config.HTTPTimeout,
		},
	}

	client.dispatch = newDispatcher(config, "logs", client.send)

	return client, nil
}

// Log sends a log entry with the specified level
func (c *LogsClient) Log(level LogLevel, message string, fields map[string]interface{}) error {
	return c.record(&LogEntry{
		Level:   level,
		Message: message,
		Fields:  fields,
	})
}

// Trace logs a trace-level message
func (c *LogsClient) Trace(message string, fields map[string]interface{}) error {
	return c.Log(LogLevelTrace, message, fields)
}

// Debug logs a debug-level message
func (c *LogsClient) Debug(message string, fields map[string]interface{}) error {
	return c.Log(LogLevelDebug, message, fields)
}

// Info logs an info-level message
func (c *LogsClient) Info(message string, fields map[string]interface{}) error {
	return c.Log(LogLevelInfo, message, fields)
}

// Warn logs a warning-level message
func (c *LogsClient) Warn(message string, fields map[string]interface{}) error {
	return c.Log(LogLevelWarn, message, fields)
}

// Error logs an error-level message
func (c *LogsClient) Error(message string, fields map[string]interface{}) error {
	return c.Log(LogLevelError, message, fields)
}

// Fatal logs a fatal-level message
func (c *LogsClient) Fatal(message string, fields map[string]interface{}) error {
	return c.Log(LogLevelFatal, message, fields)
}

// WithTrace adds distributed tracing context to a log entry
func (c *LogsClient) WithTrace(level LogLevel, message string, fields map[string]interface{}, traceID, spanID, parentSpanID, requestID string) error {
	return c.record(&LogEntry{
		Level:        level,
		Message:      message,
		Fields:       fields,
		TraceID:      traceID,
		SpanID:       spanID,
		ParentSpanID: parentSpanID,
		RequestID:    requestID,
	})
}

// record queues a log entry for delivery.
//
// This never performs I/O: sending happens on the dispatcher's goroutine, so a
// throttled ingestor cannot slow down or block the calling application.
func (c *LogsClient) record(entry *LogEntry) error {
	now := time.Now().UTC()
	entry.Timestamp = &now

	c.dispatch.add(entry)

	return nil
}

// Flush sends all buffered logs to the ingestor.
//
// Logs are sent in batches of at most MaxBatchSize. If the ingestor can only
// take part of what is pending, the remainder stays queued for the next cycle
// and the returned error says why.
func (c *LogsClient) Flush() error {
	return c.dispatch.flush()
}

// QueueStats reports the state of the client-side send queue: what is pending,
// what has been dropped, and whether the client is currently backing off.
func (c *LogsClient) QueueStats() QueueStats {
	return c.dispatch.stats()
}

// send delivers one batch to the ingestor.
func (c *LogsClient) send(items []*LogEntry) pushOutcome {
	body, err := json.Marshal(LogsPushRequest{
		ResourceID:  c.config.ResourceID,
		ServiceName: c.config.ServiceName,
		Format:      "json",
		Dimensions:  c.config.InstanceMetadata,
		Logs:        items,
	})
	if err != nil {
		return encodeOutcome(err)
	}

	return doPush(c.httpClient, c.config, "/api/v1/logs/push", body, len(items))
}

// Stats retrieves logs ingestion statistics from the data-ingestor
func (c *LogsClient) Stats() (*LogsStats, error) {
	url := fmt.Sprintf("%s/api/v1/logs/stats", c.config.IngestorURL)

	httpReq, err := http.NewRequestWithContext(context.Background(), "GET", url, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	httpReq.Header.Set("Authorization", fmt.Sprintf("Bearer %s", c.config.APIToken))

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		var errorResp map[string]interface{}
		_ = json.NewDecoder(resp.Body).Decode(&errorResp)
		return nil, fmt.Errorf("ingestor returned error: %d - %v", resp.StatusCode, errorResp)
	}

	var stats LogsStats
	if err := json.NewDecoder(resp.Body).Decode(&stats); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &stats, nil
}

// Close stops the background flusher and flushes remaining logs
func (c *LogsClient) Close() error {
	return c.dispatch.close()
}

// LogsPushRequest represents the API request
type LogsPushRequest struct {
	ResourceID string `json:"resource_id"`
	// Omitted when empty -- see the note on MetricsPushRequest.ServiceName.
	ServiceName string                 `json:"service_name,omitempty"`
	Format      string                 `json:"format"`
	Dimensions  map[string]interface{} `json:"dimensions,omitempty"` // Infrastructure/instance identifiers
	Logs        []*LogEntry            `json:"logs"`
}

// LogsPushResponse represents the API response
type LogsPushResponse struct {
	// Accepted is how many entries the ingestor took, always the first
	// Accepted entries of the batch.
	Accepted int `json:"accepted"`

	// Rejected is how many entries were not taken.
	Rejected int `json:"rejected"`

	Message string `json:"message"`

	// Reason explains a rejection: rate_limit, storage_quota, buffer_full or
	// backend_unavailable.
	Reason string `json:"reason,omitempty"`

	// RetryAfterMs is how long to wait before retrying the rejected entries.
	RetryAfterMs int64 `json:"retry_after_ms,omitempty"`
}

// LogsStats represents the logs ingestion statistics.
//
// Only the buffer block is returned to a tenant token; see MetricsStats for why
// the server-wide writer counters are absent rather than zero.
type LogsStats struct {
	Buffer LogsBufferStats `json:"buffer"`
}

// LogsBufferStats is this ingestor instance's backpressure: the same condition
// a push response reports as buffer_full, and the part a client can act on.
type LogsBufferStats struct {
	QueuedLogs         int     `json:"queued_logs"`
	Capacity           int     `json:"capacity"`
	UtilizationPercent float64 `json:"utilization_percent"`

	// DroppedLogs and DroppedBatches count data the server-side buffer took and
	// then shed because its writer could not keep up - loss that has already
	// happened and that no client-side retry can recover.
	DroppedLogs    int64 `json:"dropped_logs"`
	DroppedBatches int64 `json:"dropped_batches"`
}

// LogEntry represents a single log entry
type LogEntry struct {
	Timestamp    *time.Time             `json:"timestamp,omitempty"`
	Level        LogLevel               `json:"level"`
	Message      string                 `json:"message"`
	Fields       map[string]interface{} `json:"fields,omitempty"`
	Source       string                 `json:"source,omitempty"`
	TraceID      string                 `json:"trace_id,omitempty"`
	SpanID       string                 `json:"span_id,omitempty"`
	ParentSpanID string                 `json:"parent_span_id,omitempty"`
	RequestID    string                 `json:"request_id,omitempty"`
}
