package client

import "fmt"

// Client is a unified client for metrics, logs, and cron job check-ins
type Client struct {
	Metrics *MetricsClient
	Logs    *LogsClient
	Cron    *CronClient
	config  *Config
}

// NewClient creates a new unified client
func NewClient(config *Config) (*Client, error) {
	if err := config.Validate(); err != nil {
		return nil, fmt.Errorf("invalid config: %w", err)
	}

	metricsClient, err := NewMetricsClient(config)
	if err != nil {
		return nil, fmt.Errorf("failed to create metrics client: %w", err)
	}

	logsClient, err := NewLogsClient(config)
	if err != nil {
		_ = metricsClient.Close() // Ignore error during cleanup
		return nil, fmt.Errorf("failed to create logs client: %w", err)
	}

	var cronClient *CronClient
	if config.GatewayURL != "" {
		cronClient, err = NewCronClient(config)
		if err != nil {
			_ = metricsClient.Close()
			_ = logsClient.Close()
			return nil, fmt.Errorf("failed to create cron client: %w", err)
		}
	}

	return &Client{
		Metrics: metricsClient,
		Logs:    logsClient,
		Cron:    cronClient,
		config:  config,
	}, nil
}

// Close closes both metrics and logs clients
func (c *Client) Close() error {
	var metricsErr, logsErr error

	if c.Metrics != nil {
		metricsErr = c.Metrics.Close()
	}

	if c.Logs != nil {
		logsErr = c.Logs.Close()
	}

	if metricsErr != nil {
		return fmt.Errorf("failed to close metrics client: %w", metricsErr)
	}

	if logsErr != nil {
		return fmt.Errorf("failed to close logs client: %w", logsErr)
	}

	return nil
}
