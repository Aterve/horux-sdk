package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// CronClient handles cron job check-ins via the Gateway
type CronClient struct {
	gatewayURL string
	httpClient *http.Client
}

// NewCronClient creates a new cron job client
func NewCronClient(config *Config) (*CronClient, error) {
	if config.GatewayURL == "" {
		return nil, fmt.Errorf("GatewayURL is required for cron job check-ins")
	}

	return &CronClient{
		gatewayURL: config.GatewayURL,
		httpClient: &http.Client{
			Timeout: config.HTTPTimeout,
		},
	}, nil
}

// CheckIn reports a cron job execution to the Gateway
func (c *CronClient) CheckIn(checkInKey string, metadata map[string]interface{}) (*CheckInResponse, error) {
	url := fmt.Sprintf("%s/api/v1/cron-jobs/check-in/%s", c.gatewayURL, checkInKey)

	var body []byte
	var err error

	if len(metadata) > 0 {
		body, err = json.Marshal(map[string]interface{}{
			"metadata": metadata,
		})
		if err != nil {
			return nil, fmt.Errorf("failed to marshal request: %w", err)
		}
	}

	httpReq, err := http.NewRequestWithContext(context.Background(), "POST", url, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	if len(metadata) > 0 {
		httpReq.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	if resp.StatusCode != http.StatusOK {
		var errorResp map[string]interface{}
		_ = json.NewDecoder(resp.Body).Decode(&errorResp)
		return nil, fmt.Errorf("gateway returned error: %d - %v", resp.StatusCode, errorResp)
	}

	var checkInResp CheckInResponse
	if err := json.NewDecoder(resp.Body).Decode(&checkInResp); err != nil {
		return nil, fmt.Errorf("failed to decode response: %w", err)
	}

	return &checkInResp, nil
}

// CheckInResponse represents the cron job check-in API response
type CheckInResponse struct {
	Success        bool   `json:"success"`
	Message        string `json:"message"`
	NextExpectedAt string `json:"next_expected_at"`
}
