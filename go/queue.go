package client

import (
	"math"
	"math/rand"
	"sync"
	"time"
)

// DropPolicy decides which end of a full buffer is sacrificed when the
// ingestor cannot keep up.
type DropPolicy string

const (
	// DropOldest discards the oldest pending items. The default: during an
	// outage recent telemetry is usually the more useful half.
	DropOldest DropPolicy = "oldest"

	// DropNewest discards incoming items and preserves the existing backlog.
	DropNewest DropPolicy = "newest"
)

// maxChunksPerFlush bounds the work one flush cycle does, so a large backlog
// is drained over several cycles instead of in one long burst.
const maxChunksPerFlush = 16

// queue is a bounded FIFO of items waiting to be sent.
//
// The bound is the point: an unbounded buffer turns a rate limit or an outage
// into unbounded memory growth inside the host application, and the telemetry
// client is never worth taking the application down for.
type queue[T any] struct {
	mu      sync.Mutex
	items   []T
	maxSize int
	policy  DropPolicy
	dropped int64
}

func newQueue[T any](maxSize int, policy DropPolicy) *queue[T] {
	return &queue[T]{
		items:   make([]T, 0, 64),
		maxSize: maxSize,
		policy:  policy,
	}
}

// shiftLeft moves items[n:] to the front and zeroes the vacated tail, returning
// the shortened slice.
//
// The zeroing is the point. Re-slicing alone - items[:0], or
// append(items[:0], items[n:]...) - leaves the discarded elements in the
// backing array beyond len, where the garbage collector still sees them. T is
// *Metric or *LogEntry, so after a storage-quota shed of a full 10,000-item
// buffer all 10,000 structs and their label maps stayed reachable
// indefinitely: memory never came back after a spike. The other three SDKs are
// clean here by construction (splice, del self._items[:n], ArrayDeque.pollFirst
// nulls its slot).
func shiftLeft[T any](items []T, n int) []T {
	kept := copy(items, items[n:])

	var zero T
	for i := kept; i < len(items); i++ {
		items[i] = zero
	}

	return items[:kept]
}

// add appends an item, dropping to stay within the bound. It reports how many
// items were dropped to make room.
func (q *queue[T]) add(item T) int {
	q.mu.Lock()
	defer q.mu.Unlock()

	if len(q.items) < q.maxSize {
		q.items = append(q.items, item)
		return 0
	}

	if q.policy == DropNewest {
		q.dropped++
		return 1
	}

	// Drop from the front to make room for the newer item.
	overflow := len(q.items) - q.maxSize + 1
	q.items = shiftLeft(q.items, overflow)
	q.items = append(q.items, item)
	q.dropped += int64(overflow)
	return overflow
}

// take removes and returns up to n items from the front.
func (q *queue[T]) take(n int) []T {
	q.mu.Lock()
	defer q.mu.Unlock()

	if len(q.items) == 0 {
		return nil
	}
	if n > len(q.items) {
		n = len(q.items)
	}

	batch := make([]T, n)
	copy(batch, q.items[:n])
	q.items = shiftLeft(q.items, n)

	return batch
}

// requeue returns unsent items to the front of the queue, preserving order and
// honoring the bound. It reports how many items had to be dropped.
func (q *queue[T]) requeue(items []T) int {
	if len(items) == 0 {
		return 0
	}

	q.mu.Lock()
	defer q.mu.Unlock()

	combined := make([]T, 0, len(items)+len(q.items))
	combined = append(combined, items...)
	combined = append(combined, q.items...)

	dropped := 0
	if len(combined) > q.maxSize {
		dropped = len(combined) - q.maxSize
		if q.policy == DropNewest {
			combined = combined[:q.maxSize]
		} else {
			combined = combined[dropped:]
		}
		q.dropped += int64(dropped)
	}

	q.items = combined
	return dropped
}

// discard drops everything pending, for data the server will never accept.
func (q *queue[T]) discard() int {
	q.mu.Lock()
	defer q.mu.Unlock()

	n := len(q.items)
	q.items = shiftLeft(q.items, n)
	q.dropped += int64(n)
	return n
}

func (q *queue[T]) countDropped(n int) {
	if n <= 0 {
		return
	}

	q.mu.Lock()
	defer q.mu.Unlock()
	q.dropped += int64(n)
}

func (q *queue[T]) len() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.items)
}

func (q *queue[T]) droppedTotal() int64 {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.dropped
}

// backoff paces retries after a failed or throttled flush.
//
// Without it a client that keeps data on failure retries on its normal flush
// cadence, and because a full buffer also triggers a flush on every recorded
// item, a throttled client ends up sending a request per datapoint.
type backoff struct {
	mu        sync.Mutex
	attempt   int
	notBefore time.Time
	min       time.Duration
	max       time.Duration
}

func newBackoff(min, max time.Duration) *backoff {
	return &backoff{min: min, max: max}
}

// ready reports whether a flush may be attempted now.
func (b *backoff) ready(now time.Time) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return !now.Before(b.notBefore)
}

// fail records a failure and schedules the next attempt. A server-supplied
// hint wins over the exponential schedule, since the server knows exactly when
// the allowance returns; jitter on top keeps a fleet of clients from retrying
// in lockstep. max bounds the exponential branch only.
func (b *backoff) fail(now time.Time, hint time.Duration) time.Duration {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.attempt++

	var delay time.Duration
	if hint > 0 {
		// The hint wins outright and is deliberately not clamped to max: that
		// bound is on the client's own exponential guesswork, not on an
		// explicit instruction from the other end. Clamping a Retry-After of
		// 300s from an intermediary (CDN, API gateway, WAF under load) to 60s
		// retries five times inside a window something just asked for quiet.
		delay = hint + time.Duration(rand.Int63n(int64(hint/4)+1))
	} else {
		exp := float64(b.min) * math.Pow(2, float64(b.attempt-1))
		if exp > float64(b.max) {
			exp = float64(b.max)
		}
		// Full jitter: anywhere in (0, exp].
		delay = time.Duration(rand.Int63n(int64(exp)) + 1)
		if delay > b.max {
			delay = b.max
		}
	}

	b.notBefore = now.Add(delay)
	return delay
}

// pace holds off the next cycle without recording a failure. The server has
// published that the allowance is spent, so sending again would only earn a
// rejection - but nothing has actually failed, so the exponential attempt
// counter must not advance.
func (b *backoff) pace(now time.Time, delay time.Duration) {
	if delay <= 0 {
		delay = b.min
	}

	b.mu.Lock()
	defer b.mu.Unlock()

	if until := now.Add(delay); until.After(b.notBefore) {
		b.notBefore = until
	}
}

// succeed clears the backoff after a clean flush.
func (b *backoff) succeed() {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.attempt = 0
	b.notBefore = time.Time{}
}

// delay reports the remaining wait, for stats.
func (b *backoff) delay(now time.Time) time.Duration {
	b.mu.Lock()
	defer b.mu.Unlock()

	if now.Before(b.notBefore) {
		return b.notBefore.Sub(now)
	}
	return 0
}
