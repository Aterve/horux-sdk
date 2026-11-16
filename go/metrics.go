package client

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// MetricsClient handles sending metrics to the data-ingestor
type MetricsClient struct {
	config     *Config
	httpClient *http.Client
	dispatch   *dispatcher[*Metric]
}

// NewMetricsClient creates a new metrics client
func NewMetricsClient(config *Config) (*MetricsClient, error) {
	if err := config.Validate(); err != nil {
		return nil, fmt.Errorf("invalid config: %w", err)
	}

	client := &MetricsClient{
		config: config,
		httpClient: &http.Client{
			Timeout: config.HTTPTimeout,
		},
	}

	client.dispatch = newDispatcher(config, "metrics", client.send)

	return client, nil
}

// Counter creates and records a counter metric
func (c *MetricsClient) Counter(name string, value float64, labels map[string]string) error {
	dimensions, userLabels := c.getDimensionsAndLabels(labels)
	return c.record(&Metric{
		Name:       name,
		Type:       "counter",
		Value:      &value,
		Dimensions: dimensions,
		Labels:     userLabels,
	})
}

// Gauge creates and records a gauge metric
func (c *MetricsClient) Gauge(name string, value float64, labels map[string]string) error {
	dimensions, userLabels := c.getDimensionsAndLabels(labels)
	return c.record(&Metric{
		Name:       name,
		Type:       "gauge",
		Value:      &value,
		Dimensions: dimensions,
		Labels:     userLabels,
	})
}

// Histogram creates and records a histogram metric
func (c *MetricsClient) Histogram(name string, buckets []HistogramBucket, sum float64, count int64, labels map[string]string) error {
	dimensions, userLabels := c.getDimensionsAndLabels(labels)
	return c.record(&Metric{
		Name:       name,
		Type:       "histogram",
		Buckets:    buckets,
		Sum:        &sum,
		Count:      &count,
		Dimensions: dimensions,
		Labels:     userLabels,
	})
}

// Summary creates and records a summary metric
func (c *MetricsClient) Summary(name string, quantiles []SummaryQuantile, sum float64, count int64, labels map[string]string) error {
	dimensions, userLabels := c.getDimensionsAndLabels(labels)
	return c.record(&Metric{
		Name:       name,
		Type:       "summary",
		Quantiles:  quantiles,
		Sum:        &sum,
		Count:      &count,
		Dimensions: dimensions,
		Labels:     userLabels,
	})
}

// record queues a metric for delivery.
//
// This never performs I/O: sending happens on the dispatcher's goroutine, so a
// throttled ingestor cannot slow down or block the calling application.
func (c *MetricsClient) record(metric *Metric) error {
	now := time.Now().UTC()
	metric.Timestamp = &now

	c.dispatch.add(metric)

	return nil
}

// getDimensionsAndLabels separates dimensions (instance metadata) from labels (user-provided)
func (c *MetricsClient) getDimensionsAndLabels(userLabels map[string]string) (map[string]string, map[string]string) {
	var dimensions map[string]string

	// Add instance metadata as dimensions if enabled
	if c.config.AutoAddInstanceLabels && len(c.config.InstanceMetadata) > 0 {
		dimensions = make(map[string]string)
		for key, value := range c.config.InstanceMetadata {
			if strValue, ok := value.(string); ok {
				dimensions[key] = strValue
			} else {
				dimensions[key] = fmt.Sprintf("%v", value)
			}
		}
	}

	return dimensions, userLabels
}

// Flush sends all buffered metrics to the ingestor.
//
// Metrics are sent in batches of at most MaxBatchSize. If the ingestor can only
// take part of what is pending, the remainder stays queued for the next cycle
// and the returned error says why.
func (c *MetricsClient) Flush() error {
	return c.dispatch.flush()
}

// QueueStats reports the state of the client-side send queue: what is pending,
// what has been dropped, and whether the client is currently backing off.
func (c *MetricsClient) QueueStats() QueueStats {
	return c.dispatch.stats()
}

// send delivers one batch to the ingestor.
func (c *MetricsClient) send(items []*Metric) pushOutcome {
	body, err := json.Marshal(MetricsPushRequest{
		ResourceID:  c.config.ResourceID,
		ServiceName: c.config.ServiceName,
		Metrics:     items,
	})
	if err != nil {
		return encodeOutcome(err)
	}

	return doPush(c.httpClient, c.config, "/api/v1/metrics/push", body, len(items))
}

// Stats retrieves metrics ingestion statistics from the data-ingestor
func (c *MetricsClient) Stats() (*MetricsStats, error) {
	url := fmt.Sprintf("%s/api/v1/metrics/stats", c.config.IngestorURL)

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

	var stats MetricsStats
	if err := json.NewDecoder(resp.Body).Decode(&stats); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &stats, nil
}

// Close stops the background flusher and flushes remaining metrics
func (c *MetricsClient) Close() error {
	return c.dispatch.close()
}

// MetricsPushRequest represents the API request
type MetricsPushRequest struct {
	ResourceID string `json:"resource_id"`
	// Omitted when empty, so a client that sets no ServiceName sends the same body it
	// always did and the column is stored NULL.
	ServiceName string    `json:"service_name,omitempty"`
	Metrics     []*Metric `json:"metrics"`
}

// MetricsPushResponse represents the API response
type MetricsPushResponse struct {
	// Accepted is how many metrics the ingestor took, always the first
	// Accepted metrics of the batch.
	Accepted int `json:"accepted"`

	// Rejected is how many metrics were not taken.
	Rejected int `json:"rejected"`

	Message string `json:"message"`

	// Reason explains a rejection: rate_limit, storage_quota, buffer_full or
	// backend_unavailable.
	Reason string `json:"reason,omitempty"`

	// RetryAfterMs is how long to wait before retrying the rejected metrics.
	RetryAfterMs int64 `json:"retry_after_ms,omitempty"`
}

// Metric represents a single metric
type Metric struct {
	Name       string            `json:"name"`
	Type       string            `json:"type"`
	Value      *float64          `json:"value,omitempty"`
	Dimensions map[string]string `json:"dimensions,omitempty"` // Infrastructure/instance identifiers
	Labels     map[string]string `json:"labels,omitempty"`     // Metric-specific labels
	Timestamp  *time.Time        `json:"timestamp,omitempty"`
	Buckets    []HistogramBucket `json:"buckets,omitempty"`
	Sum        *float64          `json:"sum,omitempty"`
	Count      *int64            `json:"count,omitempty"`
	Quantiles  []SummaryQuantile `json:"quantiles,omitempty"`
}

// HistogramBucket represents a histogram bucket
type HistogramBucket struct {
	UpperBound float64 `json:"upper_bound"`
	Count      int64   `json:"count"`
}

// SummaryQuantile represents a summary quantile
type SummaryQuantile struct {
	Quantile float64 `json:"quantile"`
	Value    float64 `json:"value"`
}

// MetricsStats represents the metrics ingestion statistics.
//
// Only the buffer block is returned to a tenant token. The writer counters the
// endpoint used to include - total metrics written, batches written, write
// errors - were server-wide, spanning every tenant, so they are now returned
// only to the internal service key. They are deliberately absent from this type
// rather than present and always zero: a counter that reads as fact and is not
// is worse than no counter at all, and it would be indistinguishable from a
// genuinely idle ingestor.
type MetricsStats struct {
	Buffer MetricsBufferStats `json:"buffer"`
}

// MetricsBufferStats is this ingestor instance's backpressure: the same
// condition a push response reports as buffer_full, and the part a client can
// actually act on.
type MetricsBufferStats struct {
	QueuedMetrics      int     `json:"queued_metrics"`
	Capacity           int     `json:"capacity"`
	UtilizationPercent float64 `json:"utilization_percent"`

	// DroppedMetrics and DroppedBatches count data the server-side buffer took
	// and then shed because its writer could not keep up. This is loss that has
	// already happened and that no client-side retry can recover, so it is the
	// figure worth alerting on.
	DroppedMetrics int64 `json:"dropped_metrics"`
	DroppedBatches int64 `json:"dropped_batches"`
}
