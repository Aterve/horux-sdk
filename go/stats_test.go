package client

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// The ingestor returns the writer block only to the internal service key: its
// counters span every tenant. The SDK type therefore has no Writer field, and
// the drop counters the buffer block now carries are what a client can act on.
func TestStatsReadsTheBufferBlockAndTheDropCounters(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		// A writer block is still sent to an internal caller, and must be
		// ignored rather than cause a failure.
		_, _ = w.Write([]byte(`{
			"buffer": {"queued_metrics": 12, "capacity": 1000, "utilization_percent": 1.2,
			            "dropped_metrics": 7, "dropped_batches": 1},
			"writer": {"metrics_written": 99}
		}`))
	}))
	defer srv.Close()

	cfg := testConfig(10, 100)
	cfg.IngestorURL = srv.URL

	client, err := NewMetricsClient(cfg)
	if err != nil {
		t.Fatalf("NewMetricsClient: %v", err)
	}
	defer func() { _ = client.Close() }()

	stats, err := client.Stats()
	if err != nil {
		t.Fatalf("Stats: %v", err)
	}

	if stats.Buffer.QueuedMetrics != 12 || stats.Buffer.Capacity != 1000 {
		t.Errorf("buffer = %+v, want queued 12 of 1000", stats.Buffer)
	}
	if stats.Buffer.DroppedMetrics != 7 || stats.Buffer.DroppedBatches != 1 {
		t.Errorf("dropped = (%d, %d), want (7, 1)",
			stats.Buffer.DroppedMetrics, stats.Buffer.DroppedBatches)
	}
}

// A tenant token gets no writer block at all, and the drop counters may be
// absent on an older ingestor: neither is an error.
func TestStatsSurvivesAResponseWithOnlyTheBaseBufferFields(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"buffer": {"queued_logs": 3, "capacity": 500}}`))
	}))
	defer srv.Close()

	cfg := testConfig(10, 100)
	cfg.IngestorURL = srv.URL

	client, err := NewLogsClient(cfg)
	if err != nil {
		t.Fatalf("NewLogsClient: %v", err)
	}
	defer func() { _ = client.Close() }()

	stats, err := client.Stats()
	if err != nil {
		t.Fatalf("Stats: %v", err)
	}

	if stats.Buffer.QueuedLogs != 3 {
		t.Errorf("queued = %d, want 3", stats.Buffer.QueuedLogs)
	}
	if stats.Buffer.DroppedLogs != 0 {
		t.Errorf("dropped = %d, want 0 for a field the server omits", stats.Buffer.DroppedLogs)
	}
}
