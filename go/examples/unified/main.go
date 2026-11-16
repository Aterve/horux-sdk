package main

import (
	"fmt"
	"log"
	"math/rand"
	"time"

	client "github.com/Aterve/horux-sdk/go"
)

func main() {
	// Create configuration
	config := client.DefaultConfig()
	// config.IngestorURL = "http://localhost:3434" // Override default (https://ingest.horux.io) for local testing
	config.APIToken = "tenant_550e8400-e29b-41d4-a716-446655440000_secretkey123"
	config.ResourceID = "example-web-api"
	config.FlushInterval = 10 * time.Second
	config.AddInstanceMetadata("pod", "web-api-pod-1")
	config.AddInstanceMetadata("version", "2.1.0")
	config.AddInstanceMetadata("environment", "production")

	// Load from environment variables
	config.FromEnv()

	// Create unified client (includes both metrics and logs)
	horusClient, err := client.NewClient(config)
	if err != nil {
		log.Fatalf("Failed to create Horus client: %v", err)
	}
	defer horusClient.Close()

	fmt.Println("Horus client started successfully!")
	fmt.Println("Simulating web API with metrics and logs...")

	// Simulate a web API handling requests
	for i := 0; i < 30; i++ {
		// Simulate request
		method := []string{"GET", "POST", "PUT", "DELETE"}[rand.Intn(4)]
		path := []string{"/api/users", "/api/orders", "/api/products"}[rand.Intn(3)]
		status := []int{200, 201, 400, 404, 500}[rand.Intn(5)]
		duration := rand.Float64() * 2.0 // 0-2 seconds

		// Log the request
		logLevel := client.LogLevelInfo
		if status >= 500 {
			logLevel = client.LogLevelError
		} else if status >= 400 {
			logLevel = client.LogLevelWarn
		}

		horusClient.Logs.Log(logLevel, "HTTP request processed", map[string]interface{}{
			"method":      method,
			"path":        path,
			"status_code": status,
			"duration_ms": duration * 1000,
			"user_agent":  "Mozilla/5.0",
		})

		// Record metrics
		horusClient.Metrics.Counter("http_requests_total", 1, map[string]string{
			"method": method,
			"path":   path,
			"status": fmt.Sprintf("%d", status),
		})

		horusClient.Metrics.Gauge("http_request_duration_seconds", duration, map[string]string{
			"method": method,
			"path":   path,
		})

		// Simulate database queries
		if i%3 == 0 {
			queryDuration := rand.Float64() * 0.5 // 0-500ms

			horusClient.Logs.Debug("Database query executed", map[string]interface{}{
				"query":       "SELECT * FROM users WHERE id = ?",
				"duration_ms": queryDuration * 1000,
				"rows":        rand.Intn(100),
			})

			horusClient.Metrics.Counter("db_queries_total", 1, map[string]string{
				"operation": "SELECT",
				"table":     "users",
			})
		}

		// Simulate cache operations
		if i%5 == 0 {
			cacheHit := rand.Intn(2) == 1

			horusClient.Logs.Trace("Cache lookup", map[string]interface{}{
				"key": fmt.Sprintf("user:%d", rand.Intn(1000)),
				"hit": cacheHit,
			})

			horusClient.Metrics.Counter("cache_operations_total", 1, map[string]string{
				"operation": "get",
				"result":    map[bool]string{true: "hit", false: "miss"}[cacheHit],
			})
		}

		// Simulate memory usage
		horusClient.Metrics.Gauge("memory_usage_bytes", float64(256*1024*1024+rand.Intn(100*1024*1024)), nil)

		// Simulate active connections
		horusClient.Metrics.Gauge("active_connections", float64(50+rand.Intn(50)), nil)

		fmt.Printf("Request %d: %s %s -> %d (%.2fs)\n", i+1, method, path, status, duration)

		time.Sleep(1 * time.Second)
	}

	// Final flush
	fmt.Println("\nFlushing remaining data...")
	horusClient.Metrics.Flush()
	horusClient.Logs.Flush()

	fmt.Println("Done! All metrics and logs sent successfully.")
}
