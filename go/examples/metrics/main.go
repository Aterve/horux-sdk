package main

import (
	"fmt"
	"log"
	"time"

	client "github.com/Aterve/horux-sdk/go"
)

func main() {
	// Create configuration
	config := client.DefaultConfig()
	// config.IngestorURL = "http://localhost:3434" // Override default (https://ingest.horux.io) for local testing
	config.APIToken = "tenant_550e8400-e29b-41d4-a716-446655440000_secretkey123"
	config.ResourceID = "example-service-123"
	config.FlushInterval = 5 * time.Second
	config.AddInstanceMetadata("version", "1.0.0")
	config.AddInstanceMetadata("environment", "production")

	// Load from environment variables (optional)
	config.FromEnv()

	// Create metrics client
	metricsClient, err := client.NewMetricsClient(config)
	if err != nil {
		log.Fatalf("Failed to create metrics client: %v", err)
	}
	defer metricsClient.Close()

	fmt.Println("Metrics client started, sending metrics...")

	// Example 1: Counter metric
	if err := metricsClient.Counter("http_requests_total", 1, map[string]string{
		"method": "GET",
		"path":   "/api/users",
		"status": "200",
	}); err != nil {
		log.Printf("Failed to record counter: %v", err)
	}

	// Example 2: Gauge metric
	if err := metricsClient.Gauge("memory_usage_bytes", 1024*1024*256, map[string]string{
		"type": "heap",
	}); err != nil {
		log.Printf("Failed to record gauge: %v", err)
	}

	// Example 3: Histogram metric (for request durations)
	buckets := []client.HistogramBucket{
		{UpperBound: 0.1, Count: 50},  // 50 requests < 100ms
		{UpperBound: 0.5, Count: 150}, // 150 requests < 500ms
		{UpperBound: 1.0, Count: 200}, // 200 requests < 1s
		{UpperBound: 5.0, Count: 250}, // 250 requests < 5s
	}
	if err := metricsClient.Histogram(
		"http_request_duration_seconds",
		buckets,
		125.5, // sum of all durations
		250,   // total count
		map[string]string{
			"method": "GET",
		},
	); err != nil {
		log.Printf("Failed to record histogram: %v", err)
	}

	// Example 4: Summary metric (for percentiles)
	quantiles := []client.SummaryQuantile{
		{Quantile: 0.5, Value: 0.2},  // p50 = 200ms
		{Quantile: 0.9, Value: 0.8},  // p90 = 800ms
		{Quantile: 0.99, Value: 2.5}, // p99 = 2.5s
	}
	if err := metricsClient.Summary(
		"http_request_duration_summary",
		quantiles,
		125.5, // sum
		250,   // count
		map[string]string{
			"method": "POST",
		},
	); err != nil {
		log.Printf("Failed to record summary: %v", err)
	}

	// Simulate continuous metrics recording
	fmt.Println("Recording metrics for 15 seconds...")
	for i := 0; i < 15; i++ {
		// Record multiple metrics
		metricsClient.Counter("app_events_total", 1, map[string]string{
			"event_type": "user_login",
		})

		metricsClient.Gauge("active_connections", float64(100+i), nil)

		time.Sleep(1 * time.Second)
	}

	// Flush remaining metrics
	fmt.Println("Flushing remaining metrics...")
	if err := metricsClient.Flush(); err != nil {
		log.Printf("Failed to flush: %v", err)
	}

	fmt.Println("Done! All metrics sent successfully.")
}
