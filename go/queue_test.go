package client

import (
	"net/http"
	"testing"
	"time"
)

func TestQueueDropsOldestWhenFull(t *testing.T) {
	q := newQueue[int](3, DropOldest)

	for i := 1; i <= 5; i++ {
		q.add(i)
	}

	if got := q.len(); got != 3 {
		t.Fatalf("len = %d, want 3", got)
	}
	if got := q.droppedTotal(); got != 2 {
		t.Errorf("dropped = %d, want 2", got)
	}

	batch := q.take(3)
	if len(batch) != 3 || batch[0] != 3 || batch[2] != 5 {
		t.Errorf("batch = %v, want the three newest [3 4 5]", batch)
	}
}

func TestQueueDropsNewestWhenConfigured(t *testing.T) {
	q := newQueue[int](3, DropNewest)

	for i := 1; i <= 5; i++ {
		q.add(i)
	}

	batch := q.take(3)
	if len(batch) != 3 || batch[0] != 1 || batch[2] != 3 {
		t.Errorf("batch = %v, want the three oldest [1 2 3]", batch)
	}
	if got := q.droppedTotal(); got != 2 {
		t.Errorf("dropped = %d, want 2", got)
	}
}

func TestQueueTakeReturnsAtMostRequested(t *testing.T) {
	q := newQueue[int](10, DropOldest)
	for i := 1; i <= 5; i++ {
		q.add(i)
	}

	batch := q.take(2)
	if len(batch) != 2 || batch[0] != 1 || batch[1] != 2 {
		t.Fatalf("batch = %v, want [1 2]", batch)
	}
	if got := q.len(); got != 3 {
		t.Errorf("len = %d, want 3", got)
	}
}

func TestQueueRequeuePutsUnsentItemsBackInOrder(t *testing.T) {
	q := newQueue[int](10, DropOldest)
	q.add(3)

	q.requeue([]int{1, 2})

	batch := q.take(3)
	if len(batch) != 3 || batch[0] != 1 || batch[1] != 2 || batch[2] != 3 {
		t.Fatalf("batch = %v, want [1 2 3]", batch)
	}
}

func TestQueueRequeueRespectsTheBound(t *testing.T) {
	q := newQueue[int](3, DropOldest)
	q.add(8)
	q.add(9)

	dropped := q.requeue([]int{1, 2, 3, 4})

	if dropped != 3 {
		t.Fatalf("dropped = %d, want 3", dropped)
	}

	batch := q.take(3)
	if len(batch) != 3 || batch[0] != 4 || batch[2] != 9 {
		t.Errorf("batch = %v, want the newest three [4 8 9]", batch)
	}
}

func TestBackoffHonoursServerHint(t *testing.T) {
	now := time.Now()
	b := newBackoff(time.Second, 5*time.Minute)

	delay := b.fail(now, 10*time.Second)

	if delay < 10*time.Second {
		t.Fatalf("delay = %v, want at least the 10s hint", delay)
	}
	if b.ready(now.Add(9 * time.Second)) {
		t.Error("ready before the hint elapsed")
	}
	if !b.ready(now.Add(delay)) {
		t.Error("not ready after the delay elapsed")
	}
}

// max bounds the client's own exponential guesswork, not an explicit
// instruction from the other end. Clamping a Retry-After of 300s from an
// intermediary down to 60s retries five times inside a window something just
// asked for quiet.
func TestBackoffDoesNotClampTheServerHintToMax(t *testing.T) {
	now := time.Now()
	b := newBackoff(time.Second, time.Minute)

	delay := b.fail(now, 5*time.Minute)

	if delay < 5*time.Minute {
		t.Fatalf("delay = %v, want at least the 5m hint", delay)
	}
	if b.ready(now.Add(299 * time.Second)) {
		t.Error("ready before the hint elapsed")
	}
}

// Re-slicing to [:0] leaves the discarded elements in the backing array beyond
// len, where the GC still sees them. With T a *Metric, a shed of a full buffer
// kept every struct and its label map reachable indefinitely: memory never came
// back after a spike.
func TestQueueReleasesDiscardedItemsForCollection(t *testing.T) {
	q := newQueue[*int](4, DropOldest)
	for i := 0; i < 4; i++ {
		v := i
		q.add(&v)
	}

	if n := q.discard(); n != 4 {
		t.Fatalf("discard = %d, want 4", n)
	}

	// Inspect the backing array past len: every slot the queue gave up must be
	// nil, or the pointers it held are still live.
	tail := q.items[:cap(q.items)]
	for i, item := range tail {
		if item != nil {
			t.Errorf("items[%d] still references a discarded element", i)
		}
	}
}

func TestQueueTakeReleasesTheItemsItHandedOut(t *testing.T) {
	q := newQueue[*int](8, DropOldest)
	for i := 0; i < 6; i++ {
		v := i
		q.add(&v)
	}

	batch := q.take(4)
	if len(batch) != 4 {
		t.Fatalf("take = %d items, want 4", len(batch))
	}
	if q.len() != 2 {
		t.Fatalf("len = %d, want 2", q.len())
	}

	tail := q.items[:cap(q.items)]
	for i := q.len(); i < len(tail); i++ {
		if tail[i] != nil {
			t.Errorf("items[%d] still references an item already taken", i)
		}
	}
}

func TestBackoffGrowsExponentiallyAndIsCapped(t *testing.T) {
	now := time.Now()
	b := newBackoff(time.Second, 4*time.Second)

	for i := 0; i < 10; i++ {
		if delay := b.fail(now, 0); delay > 4*time.Second {
			t.Fatalf("delay = %v, want at most the 4s cap", delay)
		}
	}
}

// The ingestor publishes X-RateLimit-Remaining and X-RateLimit-Reset on every
// push, explicitly so a client can pace itself instead of discovering the limit
// by being rejected. Nothing read them.
func TestReadQuotaHeaders(t *testing.T) {
	cases := []struct {
		name        string
		remaining   string
		reset       string
		wantSpent   bool
		wantResetIn time.Duration
	}{
		{"allowance left", "500", "12", false, 0},
		{"allowance spent", "0", "12", true, 12 * time.Second},
		{"spent with no reset hint", "0", "", true, 0},
		{"quota not enforced", "", "", false, 0},
		{"garbage is ignored", "lots", "soon", false, 0},
		{"negative remaining reads as spent", "-3", "5", true, 5 * time.Second},
	}

	for _, tc := range cases {
		header := http.Header{}
		if tc.remaining != "" {
			header.Set(rateLimitRemainingHeader, tc.remaining)
		}
		if tc.reset != "" {
			header.Set(rateLimitResetHeader, tc.reset)
		}

		spent, resetIn := readQuotaHeaders(header)
		if spent != tc.wantSpent || resetIn != tc.wantResetIn {
			t.Errorf("%s: got (%v, %v), want (%v, %v)",
				tc.name, spent, resetIn, tc.wantSpent, tc.wantResetIn)
		}
	}
}

// Pacing is not a failure: holding off because the server said the allowance is
// spent must not advance the exponential schedule, or a paced client ends up
// backing off as if it were broken.
func TestBackoffPaceDoesNotAdvanceTheAttemptCounter(t *testing.T) {
	now := time.Now()
	b := newBackoff(time.Second, time.Minute)

	for i := 0; i < 5; i++ {
		b.pace(now, 10*time.Second)
	}

	if b.ready(now.Add(9 * time.Second)) {
		t.Error("ready before the reset window elapsed")
	}
	if !b.ready(now.Add(11 * time.Second)) {
		t.Error("not ready after the reset window elapsed")
	}

	// The next real failure must still start from the bottom of the schedule.
	if delay := b.fail(now, 0); delay > time.Second {
		t.Errorf("first failure after pacing delayed %v, want the minimum", delay)
	}
}

func TestBackoffResetsOnSuccess(t *testing.T) {
	now := time.Now()
	b := newBackoff(time.Second, time.Minute)

	b.fail(now, 30*time.Second)
	b.succeed()

	if !b.ready(now) {
		t.Error("ready = false after success, want true")
	}
	if got := b.delay(now); got != 0 {
		t.Errorf("delay = %v, want 0", got)
	}
}

func TestInterpretPushFullAcceptance(t *testing.T) {
	out := interpretPush(http.StatusOK, http.Header{}, []byte(`{"accepted":100,"rejected":0}`), 100)

	if out.accepted != 100 {
		t.Fatalf("accepted = %d, want 100", out.accepted)
	}
	if out.retry || out.shed || out.err != nil {
		t.Errorf("outcome = %+v, want a clean success", out)
	}
}

// The case the old client could not express: the server took part of the batch
// and the rest has to stay queued.
func TestInterpretPushPartialAcceptanceKeepsTail(t *testing.T) {
	body := []byte(`{"accepted":340,"rejected":660,"reason":"rate_limit","retry_after_ms":12000}`)

	out := interpretPush(http.StatusOK, http.Header{}, body, 1000)

	if out.accepted != 340 {
		t.Fatalf("accepted = %d, want 340", out.accepted)
	}
	if !out.retry {
		t.Error("retry = false, want true so the tail is kept")
	}
	if out.retryAfter != 12*time.Second {
		t.Errorf("retryAfter = %v, want 12s", out.retryAfter)
	}
	if out.reason != reasonRateLimit {
		t.Errorf("reason = %q, want %q", out.reason, reasonRateLimit)
	}
}

func TestInterpretPushRateLimitRetries(t *testing.T) {
	header := http.Header{}
	header.Set("Retry-After", "30")
	body := []byte(`{"accepted":0,"rejected":500,"reason":"rate_limit"}`)

	out := interpretPush(http.StatusTooManyRequests, header, body, 500)

	if !out.retry || out.shed {
		t.Fatalf("outcome = %+v, want retry", out)
	}
	if out.accepted != 0 {
		t.Errorf("accepted = %d, want 0", out.accepted)
	}
	if out.retryAfter != 30*time.Second {
		t.Errorf("retryAfter = %v, want 30s from the header", out.retryAfter)
	}
}

// Storage quota does not clear by waiting, so the data is shed rather than
// held: holding it is what grew the buffer until the host process died.
func TestInterpretPushStorageQuotaSheds(t *testing.T) {
	body := []byte(`{"accepted":0,"rejected":500,"reason":"storage_quota","retry_after_ms":300000}`)

	out := interpretPush(http.StatusTooManyRequests, http.Header{}, body, 500)

	if !out.shed {
		t.Fatalf("shed = false, want true")
	}
	if out.retry {
		t.Error("retry = true, want false")
	}
	if out.retryAfter != 5*time.Minute {
		t.Errorf("retryAfter = %v, want 5m", out.retryAfter)
	}
}

func TestInterpretPushStorageQuotaHasAShedFloor(t *testing.T) {
	body := []byte(`{"reason":"storage_quota"}`)

	out := interpretPush(http.StatusTooManyRequests, http.Header{}, body, 10)

	if out.retryAfter < minShedDuration {
		t.Errorf("retryAfter = %v, want at least %v", out.retryAfter, minShedDuration)
	}
}

func TestInterpretPushPermanentFailuresAreDropped(t *testing.T) {
	for _, status := range []int{http.StatusBadRequest, http.StatusRequestEntityTooLarge, http.StatusUnprocessableEntity} {
		out := interpretPush(status, http.Header{}, []byte(`{"error":"nope"}`), 10)

		if out.retry {
			t.Errorf("status %d: retry = true, want false", status)
		}
		if out.err == nil {
			t.Errorf("status %d: err = nil, want an error", status)
			continue
		}

		pushErr, ok := out.err.(*PushError)
		if !ok || !pushErr.Permanent {
			t.Errorf("status %d: err = %v, want a permanent PushError", status, out.err)
		}
	}
}

func TestInterpretPushRecoverableFailuresAreKept(t *testing.T) {
	// Auth and server errors can be fixed without the client's help, so the
	// data is kept rather than thrown away.
	for _, status := range []int{http.StatusUnauthorized, http.StatusForbidden, http.StatusNotFound,
		http.StatusInternalServerError, http.StatusBadGateway, http.StatusServiceUnavailable} {
		out := interpretPush(status, http.Header{}, nil, 10)

		if !out.retry {
			t.Errorf("status %d: retry = false, want true", status)
		}
	}
}

func TestInterpretPushCapsAcceptedAtBatchSize(t *testing.T) {
	out := interpretPush(http.StatusOK, http.Header{}, []byte(`{"accepted":9999}`), 10)

	if out.accepted != 10 {
		t.Fatalf("accepted = %d, want 10", out.accepted)
	}
}

func TestParseRetryAfterHeader(t *testing.T) {
	if got := parseRetryAfterHeader("45"); got != 45*time.Second {
		t.Errorf("seconds form = %v, want 45s", got)
	}
	if got := parseRetryAfterHeader(""); got != 0 {
		t.Errorf("empty = %v, want 0", got)
	}
	if got := parseRetryAfterHeader("-5"); got != 0 {
		t.Errorf("negative = %v, want 0", got)
	}
	if got := parseRetryAfterHeader("garbage"); got != 0 {
		t.Errorf("garbage = %v, want 0", got)
	}

	future := time.Now().Add(90 * time.Second).UTC().Format(http.TimeFormat)
	if got := parseRetryAfterHeader(future); got < 80*time.Second || got > 90*time.Second {
		t.Errorf("http date form = %v, want ~90s", got)
	}
}
