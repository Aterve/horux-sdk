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
	config.AddInstanceMetadata("pod", "api-pod-42")
	config.AddInstanceMetadata("version", "1.0.0")

	// Load from environment variables (optional)
	config.FromEnv()

	// Create logs client
	logsClient, err := client.NewLogsClient(config)
	if err != nil {
		log.Fatalf("Failed to create logs client: %v", err)
	}
	defer logsClient.Close()

	fmt.Println("Logs client started, sending logs...")

	// Example 1: Info log
	logsClient.Info("Application started successfully", map[string]interface{}{
		"port":    8080,
		"version": "1.0.0",
	})

	// Example 2: Debug log
	logsClient.Debug("Database connection established", map[string]interface{}{
		"host":      "localhost",
		"database":  "horus",
		"pool_size": 25,
	})

	// Example 3: Warning log
	logsClient.Warn("High memory usage detected", map[string]interface{}{
		"memory_mb":    512,
		"threshold_mb": 400,
		"heap_mb":      256,
	})

	// Example 4: Error log
	logsClient.Error("Failed to process request", map[string]interface{}{
		"error":       "connection timeout",
		"request_id":  "req-12345",
		"retry_count": 3,
	})

	// Example 5: Log with distributed tracing
	traceID := "abc123def456"
	spanID := "span789"
	requestID := "req-98765"

	logsClient.WithTrace(
		client.LogLevelInfo,
		"Request processed successfully",
		map[string]interface{}{
			"method":      "GET",
			"path":        "/api/users",
			"status_code": 200,
			"duration_ms": 45,
		},
		traceID,
		spanID,
		"", // no parent span
		requestID,
	)

	// Simulate application logs
	fmt.Println("Simulating application logs for 15 seconds...")
	for i := 0; i < 15; i++ {
		// Simulate various log events
		switch i % 5 {
		case 0:
			logsClient.Info("Processing batch job", map[string]interface{}{
				"batch_id": fmt.Sprintf("batch-%d", i),
				"items":    100 + i,
			})
		case 1:
			logsClient.Debug("Cache hit", map[string]interface{}{
				"key":   fmt.Sprintf("user:%d", i),
				"ttl_s": 3600,
			})
		case 2:
			logsClient.Warn("Slow query detected", map[string]interface{}{
				"query":       "SELECT * FROM users WHERE...",
				"duration_ms": 1500,
			})
		case 3:
			logsClient.Error("API rate limit exceeded", map[string]interface{}{
				"endpoint":     "/api/search",
				"client_ip":    "192.168.1.100",
				"limit":        1000,
				"current_rate": 1250,
			})
		case 4:
			logsClient.Trace("Function entry", map[string]interface{}{
				"function": "processRequest",
				"args":     []string{"arg1", "arg2"},
			})
		}

		time.Sleep(1 * time.Second)
	}

	// Flush remaining logs
	fmt.Println("Flushing remaining logs...")
	if err := logsClient.Flush(); err != nil {
		log.Printf("Failed to flush: %v", err)
	}

	fmt.Println("Done! All logs sent successfully.")
}
