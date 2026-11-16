package client

import (
	"fmt"
	"os"
	"time"
)

// Config represents the client configuration
type Config struct {
	// IngestorURL is the base URL of the data-ingestor service
	IngestorURL string

	// GatewayURL is the base URL of the Gateway service (required for cron job check-ins)
	GatewayURL string

	// APIToken is the authentication token (format: tenant_<tenant-id>_<secret>)
	APIToken string

	// ResourceID is the UUID of the resource sending metrics/logs
	ResourceID string

	// ServiceName is what this process calls itself, and is OPTIONAL.
	//
	// It is stored alongside ResourceID rather than instead of it: ResourceID names a
	// Horux-registered resource and is required, while this is the sender's own
	// identity -- the equivalent of OpenTelemetry's `service.name` resource attribute
	// or Prometheus's `job` label. Set it when one resource receives telemetry from
	// several services and you want them separable; leave it empty and the column is
	// stored NULL.
	//
	// Unlike ResourceID this needs no lookup in the Horux console: it is a name this
	// process already knows about itself, so it can come from the same deployment
	// config as everything else. Also read from HORUX_SERVICE_NAME.
	ServiceName string

	// FlushInterval is how often to flush buffered data
	FlushInterval time.Duration

	// MaxBatchSize is the maximum number of items to send in one request
	MaxBatchSize int

	// MaxBufferSize is the most items held while the ingestor is throttling or
	// unreachable. Past it DropPolicy decides what goes. The bound is what
	// keeps a rate limit from turning into unbounded memory growth inside the
	// host application. Defaults to 10x MaxBatchSize.
	MaxBufferSize int

	// DropPolicy decides which items are discarded once MaxBufferSize is
	// reached. Defaults to DropOldest.
	DropPolicy DropPolicy

	// MinRetryBackoff and MaxRetryBackoff bound the wait after a failed or
	// throttled flush. A server-supplied Retry-After takes precedence.
	// Default to 1s and 60s.
	MinRetryBackoff time.Duration
	MaxRetryBackoff time.Duration

	// OnDrop, when set, is called when the client discards data, with the
	// stream name ("metrics" or "logs"), the number of items and the reason.
	// Notifications are throttled so a sustained outage does not itself
	// become a flood.
	OnDrop func(stream string, count int, reason string)

	// HTTPTimeout is the HTTP request timeout
	HTTPTimeout time.Duration

	// InstanceMetadata contains instance-specific information
	// (e.g., hostname, pod name, container ID)
	InstanceMetadata map[string]interface{}

	// AutoAddInstanceLabels automatically adds instance metadata to metrics as labels
	AutoAddInstanceLabels bool
}

// DefaultConfig returns a configuration with default values
func DefaultConfig() *Config {
	hostname, err := os.Hostname()
	if err != nil {
		hostname = "unknown" // Fallback if hostname cannot be determined
	}

	return &Config{
		IngestorURL:           "https://ingest.horux.io",
		FlushInterval:         10 * time.Second,
		MaxBatchSize:          1000,
		MaxBufferSize:         10000,
		DropPolicy:            DropOldest,
		MinRetryBackoff:       1 * time.Second,
		MaxRetryBackoff:       60 * time.Second,
		HTTPTimeout:           30 * time.Second,
		AutoAddInstanceLabels: true,
		InstanceMetadata: map[string]interface{}{
			"hostname": hostname,
		},
	}
}

// applyDefaults fills in unset pacing options, so a hand-built Config keeps
// working without having to know about every knob.
func (c *Config) applyDefaults() {
	if c.MaxBatchSize <= 0 {
		c.MaxBatchSize = 1000
	}
	if c.MaxBufferSize <= 0 {
		c.MaxBufferSize = c.MaxBatchSize * 10
	}
	if c.MaxBufferSize < c.MaxBatchSize {
		c.MaxBufferSize = c.MaxBatchSize
	}
	if c.DropPolicy == "" {
		c.DropPolicy = DropOldest
	}
	if c.MinRetryBackoff <= 0 {
		c.MinRetryBackoff = 1 * time.Second
	}
	if c.MaxRetryBackoff <= 0 {
		c.MaxRetryBackoff = 60 * time.Second
	}
	if c.MaxRetryBackoff < c.MinRetryBackoff {
		c.MaxRetryBackoff = c.MinRetryBackoff
	}
}

// Validate validates the configuration, filling in defaults for anything left
// unset.
func (c *Config) Validate() error {
	c.applyDefaults()

	if c.IngestorURL == "" {
		return fmt.Errorf("IngestorURL is required")
	}

	if c.APIToken == "" {
		return fmt.Errorf("APIToken is required")
	}

	if c.ResourceID == "" {
		return fmt.Errorf("ResourceID is required")
	}

	if c.FlushInterval <= 0 {
		return fmt.Errorf("FlushInterval must be positive")
	}

	if c.MaxBatchSize <= 0 {
		return fmt.Errorf("MaxBatchSize must be positive")
	}

	if c.DropPolicy != DropOldest && c.DropPolicy != DropNewest {
		return fmt.Errorf("DropPolicy must be %q or %q", DropOldest, DropNewest)
	}

	if c.HTTPTimeout <= 0 {
		return fmt.Errorf("HTTPTimeout must be positive")
	}

	return nil
}

// FromEnv populates configuration from environment variables
func (c *Config) FromEnv() {
	if url := os.Getenv("HORUX_INGESTOR_URL"); url != "" {
		c.IngestorURL = url
	}

	if token := os.Getenv("HORUX_API_TOKEN"); token != "" {
		c.APIToken = token
	}

	if resourceID := os.Getenv("HORUX_RESOURCE_ID"); resourceID != "" {
		c.ResourceID = resourceID
	}

	if serviceName := os.Getenv("HORUX_SERVICE_NAME"); serviceName != "" {
		c.ServiceName = serviceName
	}

	if gatewayURL := os.Getenv("HORUX_GATEWAY_URL"); gatewayURL != "" {
		c.GatewayURL = gatewayURL
	}

	// Add common instance metadata from environment
	if podName := os.Getenv("POD_NAME"); podName != "" {
		c.InstanceMetadata["pod"] = podName
	}

	if nodeName := os.Getenv("NODE_NAME"); nodeName != "" {
		c.InstanceMetadata["node"] = nodeName
	}

	if namespace := os.Getenv("NAMESPACE"); namespace != "" {
		c.InstanceMetadata["namespace"] = namespace
	}

	if containerID := os.Getenv("CONTAINER_ID"); containerID != "" {
		c.InstanceMetadata["container_id"] = containerID
	}
}

// AddInstanceMetadata adds custom instance metadata
func (c *Config) AddInstanceMetadata(key string, value interface{}) {
	if c.InstanceMetadata == nil {
		c.InstanceMetadata = make(map[string]interface{})
	}
	c.InstanceMetadata[key] = value
}
