package client

import (
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// newTestDispatcher builds a dispatcher without its background loop, so a test
// can drive drain() directly and observe exactly what was sent.
func newTestDispatcher[T any](cfg *Config, send sendFunc[T]) *dispatcher[T] {
	cfg.applyDefaults()

	return &dispatcher[T]{
		cfg:   cfg,
		name:  "test",
		queue: newQueue[T](cfg.MaxBufferSize, cfg.DropPolicy),
		back:  newBackoff(cfg.MinRetryBackoff, cfg.MaxRetryBackoff),
		send:  send,
		wake:  make(chan struct{}, 1),
		done:  make(chan struct{}),
	}
}

type recorder struct {
	mu    sync.Mutex
	sizes []int
}

func (r *recorder) record(n int) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.sizes = append(r.sizes, n)
}

func (r *recorder) calls() []int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]int(nil), r.sizes...)
}

func testConfig(batchSize, bufferSize int) *Config {
	return &Config{
		IngestorURL:     "http://ingestor.invalid",
		APIToken:        "tenant_x_secret",
		ResourceID:      "resource-1",
		FlushInterval:   time.Hour,
		MaxBatchSize:    batchSize,
		MaxBufferSize:   bufferSize,
		DropPolicy:      DropOldest,
		MinRetryBackoff: time.Second,
		MaxRetryBackoff: time.Minute,
		HTTPTimeout:     time.Second,
	}
}

// A backlog is sent in batches of MaxBatchSize, never as one oversized request.
// Sending the whole buffer in a single request is how a stalled client grew a
// payload past the ingestor's 10MB body limit and wedged permanently.
func TestDrainSendsInBatches(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 100), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 25; i++ {
		d.queue.add(i)
	}

	if err := d.drain(true); err != nil {
		t.Fatalf("drain: %v", err)
	}

	if got := rec.calls(); len(got) != 3 || got[0] != 10 || got[1] != 10 || got[2] != 5 {
		t.Fatalf("batch sizes = %v, want [10 10 5]", got)
	}
	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want 0", pending)
	}
}

// On a partial acceptance the unaccepted tail goes back to the front of the
// queue and the cycle stops: the client makes exactly the progress its
// allowance permits, instead of hammering for the rest.
func TestDrainKeepsTailAndStopsOnPartialAcceptance(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 100), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{
			accepted:   4,
			retry:      true,
			retryAfter: 10 * time.Second,
			reason:     reasonRateLimit,
			err:        &PushError{Status: 200, Reason: reasonRateLimit},
		}
	})

	for i := 0; i < 25; i++ {
		d.queue.add(i)
	}

	if err := d.drain(true); err == nil {
		t.Fatal("drain returned nil, want the rejection error")
	}

	if got := rec.calls(); len(got) != 1 {
		t.Fatalf("calls = %v, want exactly one attempt", got)
	}
	if pending := d.queue.len(); pending != 21 {
		t.Errorf("pending = %d, want 21 (6 unsent plus the untouched 15)", pending)
	}

	// The 6 unsent items kept their place at the front of the queue.
	batch := d.queue.take(6)
	for i, item := range batch {
		if item != i+4 {
			t.Fatalf("queue front = %v, want items 4..9 in order", batch)
			break
		}
	}
}

// After a rejection the client waits. Without this, a throttled client retried
// on its normal cadence and - because a full buffer also triggered a flush -
// ended up sending one request per recorded datapoint.
func TestDrainRespectsBackoffAfterRejection(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 100), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{
			retry:      true,
			retryAfter: 30 * time.Second,
			reason:     reasonRateLimit,
			err:        &PushError{Status: http.StatusTooManyRequests, Reason: reasonRateLimit},
		}
	})

	for i := 0; i < 5; i++ {
		d.queue.add(i)
	}

	_ = d.drain(false)
	if got := rec.calls(); len(got) != 1 {
		t.Fatalf("calls after first drain = %v, want 1", got)
	}

	// A normal cycle during the backoff window sends nothing.
	for i := 0; i < 5; i++ {
		_ = d.drain(false)
	}
	if got := rec.calls(); len(got) != 1 {
		t.Fatalf("calls during backoff = %v, want still 1", got)
	}

	if backoff := d.stats().RetryBackoff; backoff < 30*time.Second {
		t.Errorf("RetryBackoff = %v, want at least the 30s hint", backoff)
	}
}

// A payload the ingestor will never accept is dropped, not retried forever
// behind every later batch.
func TestDrainDropsPermanentlyRejectedBatch(t *testing.T) {
	rec := &recorder{}
	var drops []int

	cfg := testConfig(10, 100)
	cfg.OnDrop = func(stream string, count int, reason string) {
		drops = append(drops, count)
	}

	d := newTestDispatcher[int](cfg, func(items []int) pushOutcome {
		rec.record(len(items))
		if len(items) == 10 {
			return pushOutcome{
				reason: "invalid_payload",
				err:    &PushError{Status: http.StatusBadRequest, Reason: "invalid_payload", Permanent: true},
			}
		}
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 15; i++ {
		d.queue.add(i)
	}

	if err := d.drain(true); err == nil {
		t.Fatal("drain returned nil, want the permanent error")
	}

	if got := rec.calls(); len(got) != 2 {
		t.Fatalf("calls = %v, want the bad batch dropped and the next one sent", got)
	}
	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want 0", pending)
	}
	if dropped := d.queue.droppedTotal(); dropped != 10 {
		t.Errorf("dropped = %d, want 10", dropped)
	}
	if len(drops) != 1 || drops[0] != 10 {
		t.Errorf("OnDrop calls = %v, want one call for 10 items", drops)
	}
}

// Over a storage quota there is nowhere for the data to go, so the client sheds
// it at the door rather than growing a buffer for a condition that needs a plan
// change to clear.
func TestStorageQuotaShedsInsteadOfBuffering(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 100), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{
			shed:       true,
			retryAfter: 5 * time.Minute,
			reason:     reasonStorageQuota,
			err:        &PushError{Status: http.StatusTooManyRequests, Reason: reasonStorageQuota},
		}
	})

	for i := 0; i < 30; i++ {
		d.queue.add(i)
	}

	_ = d.drain(true)

	if pending := d.queue.len(); pending != 0 {
		t.Fatalf("pending = %d, want everything shed", pending)
	}
	if !d.stats().Shedding {
		t.Fatal("Shedding = false, want true")
	}

	// New data is dropped on arrival while shedding, so memory stays flat.
	for i := 0; i < 50; i++ {
		d.add(i)
	}
	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want 0 while shedding", pending)
	}

	_ = d.drain(false)
	if got := rec.calls(); len(got) != 1 {
		t.Errorf("calls = %v, want no further attempts during the shed window", got)
	}
}

// Recording never touches the network, no matter how full the buffer is.
func TestAddNeverSends(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 100), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 50; i++ {
		d.add(i)
	}

	if got := rec.calls(); len(got) != 0 {
		t.Fatalf("calls = %v, want none: add() must not perform I/O", got)
	}
	if pending := d.queue.len(); pending != 50 {
		t.Errorf("pending = %d, want 50", pending)
	}
}

func TestAddBoundsTheBufferAndReportsDrops(t *testing.T) {
	var dropped int

	cfg := testConfig(10, 20)
	cfg.OnDrop = func(stream string, count int, reason string) {
		if reason != reasonBufferFull {
			t.Errorf("reason = %q, want %q", reason, reasonBufferFull)
		}
		dropped += count
	}

	d := newTestDispatcher[int](cfg, func(items []int) pushOutcome {
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 100; i++ {
		d.add(i)
	}

	if pending := d.queue.len(); pending != 20 {
		t.Fatalf("pending = %d, want the 20-item bound", pending)
	}
	if total := d.queue.droppedTotal(); total != 80 {
		t.Errorf("dropped total = %d, want 80", total)
	}
	if dropped == 0 {
		t.Error("OnDrop was never called")
	}
}

// A client that has just used the last of its allowance stops for the window
// the server published, instead of sending the next batch and being told 429.
func TestDrainPacesItselfWhenTheAllowanceIsSpent(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 1000), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{
			accepted:       len(items),
			allowanceSpent: true,
			resetAfter:     20 * time.Second,
		}
	})

	for i := 0; i < 200; i++ {
		d.queue.add(i)
	}

	_ = d.drain(false)

	if got := len(rec.calls()); got != 1 {
		t.Fatalf("calls = %d, want 1 - the cycle stops once the allowance is gone", got)
	}
	if pending := d.queue.len(); pending != 190 {
		t.Errorf("pending = %d, want the remainder kept", pending)
	}
	if remaining := d.stats().RetryBackoff; remaining < 19*time.Second {
		t.Errorf("retry backoff = %v, want roughly the published 20s reset", remaining)
	}

	// And the next cycle does not send during the window.
	_ = d.drain(false)
	if got := len(rec.calls()); got != 1 {
		t.Errorf("calls = %d, want no attempt inside the reset window", got)
	}
}

// A final pass ignores pacing: Close() has one chance to deliver, and the data
// is gone either way if it does not.
func TestFinalDrainIgnoresPacing(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 1000), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{
			accepted:       len(items),
			allowanceSpent: true,
			resetAfter:     20 * time.Second,
		}
	})

	for i := 0; i < 50; i++ {
		d.queue.add(i)
	}

	_ = d.drain(true)

	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want everything attempted on a final pass", pending)
	}
	if got := len(rec.calls()); got != 5 {
		t.Errorf("calls = %d, want 5", got)
	}
}

// A periodic cycle is bounded so a large backlog drains over several passes
// rather than monopolising the flusher in one.
func TestPeriodicDrainIsBoundedPerCycle(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 1000), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 1000; i++ {
		d.queue.add(i)
	}

	_ = d.drain(false)

	if got := len(rec.calls()); got != maxChunksPerFlush {
		t.Fatalf("calls = %d, want the per-cycle cap of %d", got, maxChunksPerFlush)
	}
	if pending := d.queue.len(); pending != 1000-maxChunksPerFlush*10 {
		t.Errorf("pending = %d, want the remainder left for the next cycle", pending)
	}
}

// A final pass must not stop at the cycle cap. With MaxBufferSize above
// 16 x MaxBatchSize, Close() abandoned the remainder with no OnDrop call and no
// error - the data simply vanished.
func TestFinalDrainKeepsGoingPastTheCycleCap(t *testing.T) {
	rec := &recorder{}
	d := newTestDispatcher[int](testConfig(10, 1000), func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 1000; i++ {
		d.queue.add(i)
	}

	_ = d.drain(true)

	if got := len(rec.calls()); got != 100 {
		t.Fatalf("calls = %d, want 100 (1000 items in batches of 10)", got)
	}
	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want nothing abandoned", pending)
	}
}

// Concurrent passes used to run simultaneously: drain took no lock, so every
// caller skipped the back.ready() check and back.succeed() from one pass
// cleared a notBefore another had just set via back.fail(). A service calling
// Flush() per HTTP handler therefore sent one request per call under 429
// throttling with the pacing guarantee void.
func TestConcurrentDrainsDoNotOverlap(t *testing.T) {
	var mu sync.Mutex
	inFlight := 0
	maxInFlight := 0

	d := newTestDispatcher[int](testConfig(10, 10000), func(items []int) pushOutcome {
		mu.Lock()
		inFlight++
		if inFlight > maxInFlight {
			maxInFlight = inFlight
		}
		mu.Unlock()

		time.Sleep(2 * time.Millisecond)

		mu.Lock()
		inFlight--
		mu.Unlock()

		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 2000; i++ {
		d.queue.add(i)
	}

	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = d.drain(true)
		}()
	}
	wg.Wait()

	mu.Lock()
	defer mu.Unlock()
	if maxInFlight != 1 {
		t.Errorf("maxInFlight = %d, want exactly 1 pass at a time", maxInFlight)
	}
}

// A periodic pass skips when another is running; a final one waits its turn,
// because Close() and Flush() must not return having sent nothing.
func TestPeriodicDrainSkipsWhileAFinalPassHoldsTheLock(t *testing.T) {
	d := newTestDispatcher[int](testConfig(10, 100), func(items []int) pushOutcome {
		return pushOutcome{accepted: len(items)}
	})
	d.queue.add(1)

	d.drainMu.Lock()
	if err := d.drain(false); err != nil {
		t.Fatalf("drain(false) = %v, want nil", err)
	}
	d.drainMu.Unlock()

	if pending := d.queue.len(); pending != 1 {
		t.Errorf("pending = %d, want the item untouched by the skipped pass", pending)
	}
}

// After Close() there is no flush loop, so an item enqueued would sit in the
// queue forever: stats reported a growing Pending that nothing would drain.
func TestAddAfterCloseIsCountedAsADrop(t *testing.T) {
	var mu sync.Mutex
	var reasons []string

	cfg := testConfig(10, 100)
	cfg.OnDrop = func(_ string, _ int, reason string) {
		mu.Lock()
		reasons = append(reasons, reason)
		mu.Unlock()
	}

	d := newDispatcher[int](cfg, "metrics", func(items []int) pushOutcome {
		return pushOutcome{accepted: len(items)}
	})
	if err := d.close(); err != nil {
		t.Fatalf("close() = %v", err)
	}

	d.add(1)

	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want nothing stranded in the queue", pending)
	}
	if total := d.queue.droppedTotal(); total != 1 {
		t.Errorf("dropped total = %d, want 1", total)
	}

	mu.Lock()
	defer mu.Unlock()
	if len(reasons) != 1 || reasons[0] != reasonClientClosed {
		t.Errorf("OnDrop reasons = %v, want [%s]", reasons, reasonClientClosed)
	}
}

// Close() delivers what is pending. Nothing asserted this before, in any of the
// four SDKs, which is how several lifecycle defects shipped.
func TestCloseDeliversPendingData(t *testing.T) {
	rec := &recorder{}
	cfg := testConfig(10, 1000)

	d := newDispatcher[int](cfg, "metrics", func(items []int) pushOutcome {
		rec.record(len(items))
		return pushOutcome{accepted: len(items)}
	})

	for i := 0; i < 95; i++ {
		d.add(i)
	}

	if err := d.close(); err != nil {
		t.Fatalf("close() = %v", err)
	}

	total := 0
	for _, n := range rec.calls() {
		total += n
	}
	if total != 95 {
		t.Errorf("delivered %d items, want 95", total)
	}
	if pending := d.queue.len(); pending != 0 {
		t.Errorf("pending = %d, want 0", pending)
	}
	if dropped := d.queue.droppedTotal(); dropped != 0 {
		t.Errorf("dropped = %d, want 0", dropped)
	}
}

// End to end against a real HTTP server: the published allowance survives the
// round trip into the outcome, which is where a wrong header name would hide.
func TestDoPushReadsThePublishedAllowance(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-RateLimit-Limit", "1000")
		w.Header().Set(rateLimitRemainingHeader, "0")
		w.Header().Set(rateLimitResetHeader, "18")
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		writeJSON(w, map[string]interface{}{"accepted": 1})
	}))
	defer srv.Close()

	cfg := testConfig(1, 10)
	cfg.IngestorURL = srv.URL
	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}

	out := doPush(&http.Client{Timeout: 5 * time.Second}, cfg,
		"/api/v1/metrics/push", []byte(`{"metrics":[]}`), 1)

	if out.accepted != 1 {
		t.Fatalf("accepted = %d, want 1", out.accepted)
	}
	if !out.allowanceSpent {
		t.Error("allowanceSpent = false; the ingestor publishes these so a client can " +
			"pace itself instead of discovering the limit by being rejected")
	}
	if out.resetAfter != 18*time.Second {
		t.Errorf("resetAfter = %v, want 18s", out.resetAfter)
	}
}

// An unenforced quota publishes no headers at all, and that must not read as a
// spent allowance and stall the client.
func TestDoPushLeavesPacingAloneWhenNoQuotaIsPublished(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		writeJSON(w, map[string]interface{}{"accepted": 1})
	}))
	defer srv.Close()

	cfg := testConfig(1, 10)
	cfg.IngestorURL = srv.URL
	if err := cfg.Validate(); err != nil {
		t.Fatalf("Validate: %v", err)
	}

	out := doPush(&http.Client{Timeout: 5 * time.Second}, cfg,
		"/api/v1/metrics/push", []byte(`{"metrics":[]}`), 1)

	if out.allowanceSpent || out.resetAfter != 0 {
		t.Errorf("got (%v, %v), want (false, 0)", out.allowanceSpent, out.resetAfter)
	}
}

// End to end against a real HTTP server: a tenant whose backlog exceeds its
// per-minute allowance drains at the allowed rate instead of deadlocking.
func TestMetricsClientDrainsBacklogAgainstThrottlingServer(t *testing.T) {
	const grant = 40

	var mu sync.Mutex
	var requests []int
	accepted := 0

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get(partialAcceptHeader) == "" {
			t.Errorf("missing %s header", partialAcceptHeader)
		}

		var body struct {
			Metrics []Metric `json:"metrics"`
		}
		if err := decodeJSON(r, &body); err != nil {
			t.Errorf("decode: %v", err)
		}

		mu.Lock()
		requests = append(requests, len(body.Metrics))
		take := len(body.Metrics)
		if take > grant {
			take = grant
		}
		accepted += take
		mu.Unlock()

		rejected := len(body.Metrics) - take
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		writeJSON(w, map[string]interface{}{
			"accepted":       take,
			"rejected":       rejected,
			"reason":         reasonRateLimit,
			"retry_after_ms": 1,
		})
	}))
	defer srv.Close()

	cfg := testConfig(100, 1000)
	cfg.IngestorURL = srv.URL
	cfg.MinRetryBackoff = time.Millisecond
	cfg.MaxRetryBackoff = 2 * time.Millisecond

	client, err := NewMetricsClient(cfg)
	if err != nil {
		t.Fatalf("NewMetricsClient: %v", err)
	}
	defer func() { _ = client.Close() }()

	for i := 0; i < 200; i++ {
		if err := client.Gauge("queue.depth", float64(i), nil); err != nil {
			t.Fatalf("Gauge: %v", err)
		}
	}

	// Each cycle takes what the server allows; the rest stays queued.
	deadline := time.Now().Add(5 * time.Second)
	for client.QueueStats().Pending > 0 && time.Now().Before(deadline) {
		_ = client.Flush()
		time.Sleep(5 * time.Millisecond)
	}

	mu.Lock()
	gotAccepted := accepted
	gotRequests := append([]int(nil), requests...)
	mu.Unlock()

	if gotAccepted != 200 {
		t.Fatalf("accepted = %d, want all 200 metrics eventually delivered", gotAccepted)
	}
	for i, size := range gotRequests {
		if size > 100 {
			t.Fatalf("request %d carried %d metrics, want at most MaxBatchSize", i, size)
		}
	}
	if stats := client.QueueStats(); stats.Dropped != 0 {
		t.Errorf("dropped = %d, want 0", stats.Dropped)
	}
}
