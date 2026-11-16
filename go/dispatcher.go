package client

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Rejection reasons reported by the ingestor.
const (
	reasonRateLimit          = "rate_limit"
	reasonStorageQuota       = "storage_quota"
	reasonBufferFull         = "buffer_full"
	reasonBackendUnavailable = "backend_unavailable"
	reasonTransport          = "transport"
	reasonRejected           = "rejected"

	// reasonClientClosed is client-side: recorded after Close(), when there is
	// no flush loop left to send it.
	reasonClientClosed = "client_closed"
)

// partialAcceptHeader tells the ingestor this client understands a partial
// acceptance, i.e. that it will keep the unaccepted tail rather than re-sending
// the whole batch. Without it the server rejects a batch that does not fit
// whole, which a client holding a large backlog can never get past.
const partialAcceptHeader = "X-Horux-Partial-Accept"

// minShedDuration is the floor for how long to shed data after the ingestor
// reports a storage quota, if it gives no hint of its own.
const minShedDuration = time.Minute

// pushOutcome is what the ingestor did with one batch.
type pushOutcome struct {
	// accepted is how many items were taken. The ingestor always takes a
	// prefix of the batch, so items[accepted:] is exactly what is left over.
	accepted int

	// retry means the leftover is worth keeping and sending again.
	retry bool

	// shed means the leftover should be dropped and further data discarded
	// until retryAfter passes: a storage quota does not clear by waiting a
	// moment, and holding data for it only grows the buffer.
	shed bool

	// retryAfter is the server's hint for when to come back.
	retryAfter time.Duration

	// allowanceSpent reports that the ingestor published a remaining quota of
	// zero for this tenant, and resetAfter how long until the allowance is back.
	// The ingestor sets these on every push explicitly "so a client can pace
	// itself instead of discovering the limit by being rejected"; reading them
	// is how that stops costing a wasted round trip and a 429.
	allowanceSpent bool
	resetAfter     time.Duration

	reason string
	err    error
}

// quotaHeaders are the allowance the ingestor publishes on every push.
const (
	rateLimitLimitHeader     = "X-RateLimit-Limit"
	rateLimitRemainingHeader = "X-RateLimit-Remaining"
	rateLimitResetHeader     = "X-RateLimit-Reset"
)

// readQuotaHeaders reports whether the tenant's allowance is spent and, if so,
// how long until it returns. The reset header is a whole number of seconds
// until the full allowance is available again, not an absolute time.
func readQuotaHeaders(header http.Header) (spent bool, resetAfter time.Duration) {
	remaining, err := strconv.Atoi(header.Get(rateLimitRemainingHeader))
	if err != nil || remaining > 0 {
		// Absent (quota not enforced for this tenant) or allowance left.
		return false, 0
	}

	seconds, err := strconv.Atoi(header.Get(rateLimitResetHeader))
	if err != nil || seconds <= 0 {
		return true, 0
	}

	return true, time.Duration(seconds) * time.Second
}

// pushResponseBody is the shared response shape of both push endpoints.
type pushResponseBody struct {
	Accepted     int    `json:"accepted"`
	Rejected     int    `json:"rejected"`
	Message      string `json:"message"`
	Reason       string `json:"reason"`
	RetryAfterMs int64  `json:"retry_after_ms"`
	Error        string `json:"error"`
}

// interpretPush turns an ingestor response into an outcome.
//
// The distinction that matters is whether the leftover is worth keeping:
// anything the server might accept later is retried, a payload it will never
// accept is dropped rather than retried forever, and data that has nowhere to
// go at all (storage quota) is shed.
func interpretPush(status int, header http.Header, body []byte, batchSize int) pushOutcome {
	var parsed pushResponseBody
	_ = json.Unmarshal(body, &parsed) // best effort; errors fall back to status

	retryAfter := time.Duration(parsed.RetryAfterMs) * time.Millisecond
	if retryAfter == 0 {
		retryAfter = parseRetryAfterHeader(header.Get("Retry-After"))
	}

	accepted := parsed.Accepted
	if accepted < 0 {
		accepted = 0
	}
	if accepted > batchSize {
		accepted = batchSize
	}

	allowanceSpent, resetAfter := readQuotaHeaders(header)

	if parsed.Reason == reasonStorageQuota {
		if retryAfter < minShedDuration {
			retryAfter = minShedDuration
		}
		return pushOutcome{
			accepted:   accepted,
			shed:       true,
			retryAfter: retryAfter,
			reason:     reasonStorageQuota,
			err:        &PushError{Status: status, Reason: reasonStorageQuota, Message: responseMessage(parsed)},
		}
	}

	switch {
	case status == http.StatusOK:
		if accepted >= batchSize {
			return pushOutcome{
				accepted:       batchSize,
				allowanceSpent: allowanceSpent,
				resetAfter:     resetAfter,
			}
		}

		// Partially accepted: keep the tail, come back when told to.
		reason := parsed.Reason
		if reason == "" {
			reason = reasonRejected
		}
		return pushOutcome{
			accepted:   accepted,
			retry:      true,
			retryAfter: retryAfter,
			reason:     reason,
			err:        &PushError{Status: status, Reason: reason, Message: responseMessage(parsed)},
		}

	case status == http.StatusBadRequest,
		status == http.StatusRequestEntityTooLarge,
		status == http.StatusUnprocessableEntity:
		// The payload itself is unacceptable; retrying it forever would block
		// every later batch behind data that can never be sent.
		reason := parsed.Reason
		if reason == "" {
			reason = "invalid_payload"
		}
		return pushOutcome{
			accepted: accepted,
			reason:   reason,
			err:      &PushError{Status: status, Reason: reason, Message: responseMessage(parsed), Permanent: true},
		}

	default:
		// Rate limits, auth problems and server errors can all clear on their
		// own, so the data is kept.
		reason := parsed.Reason
		if reason == "" {
			if status == http.StatusTooManyRequests {
				reason = reasonRateLimit
			} else {
				reason = reasonBackendUnavailable
			}
		}
		return pushOutcome{
			accepted:   accepted,
			retry:      true,
			retryAfter: retryAfter,
			reason:     reason,
			err:        &PushError{Status: status, Reason: reason, Message: responseMessage(parsed)},
		}
	}
}

func responseMessage(parsed pushResponseBody) string {
	if parsed.Error != "" {
		return parsed.Error
	}
	return parsed.Message
}

// parseRetryAfterHeader reads both forms of Retry-After, seconds and HTTP date.
func parseRetryAfterHeader(value string) time.Duration {
	if value == "" {
		return 0
	}

	if seconds, err := strconv.Atoi(value); err == nil {
		if seconds < 0 {
			return 0
		}
		return time.Duration(seconds) * time.Second
	}

	if when, err := http.ParseTime(value); err == nil {
		if d := time.Until(when); d > 0 {
			return d
		}
	}

	return 0
}

// sendFunc delivers one batch to the ingestor.
type sendFunc[T any] func(items []T) pushOutcome

// dispatcher owns the pending queue, the flush cycle and the retry state for
// one stream (metrics or logs).
//
// Recording an item only enqueues it: the HTTP request always happens on the
// dispatcher's own goroutine. Flushing from the caller's goroutine is what
// turned a throttled client into a request per datapoint, because a buffer
// stuck at the batch threshold satisfied the "buffer full, flush now" test on
// every single call.
type dispatcher[T any] struct {
	cfg   *Config
	name  string
	queue *queue[T]
	back  *backoff
	send  sendFunc[T]

	mu        sync.Mutex
	shedUntil time.Time
	lastLog   time.Time
	closed    bool

	// drainMu serializes flush passes. Go is the one language here where two
	// goroutines genuinely run at once, and the only mutex above covers
	// shedUntil/lastLog.
	drainMu sync.Mutex

	wake      chan struct{}
	done      chan struct{}
	wg        sync.WaitGroup
	closeOnce sync.Once
}

func newDispatcher[T any](cfg *Config, name string, send sendFunc[T]) *dispatcher[T] {
	d := &dispatcher[T]{
		cfg:   cfg,
		name:  name,
		queue: newQueue[T](cfg.MaxBufferSize, cfg.DropPolicy),
		back:  newBackoff(cfg.MinRetryBackoff, cfg.MaxRetryBackoff),
		send:  send,
		wake:  make(chan struct{}, 1),
		done:  make(chan struct{}),
	}

	d.wg.Add(1)
	go d.run()

	return d
}

// add enqueues an item. It never performs I/O and never blocks on the network.
func (d *dispatcher[T]) add(item T) {
	if d.isClosed() {
		// The flush loop is gone, so an item enqueued here would sit in the
		// queue forever: stats would report a growing Pending that nothing
		// would ever drain, and OnDrop would never fire.
		d.queue.countDropped(1)
		d.report(1, reasonClientClosed)
		return
	}

	if d.shedding(time.Now()) {
		// Nowhere to put this data; dropping it now keeps memory flat.
		d.queue.countDropped(1)
		d.report(1, reasonStorageQuota)
		return
	}

	if dropped := d.queue.add(item); dropped > 0 {
		d.report(dropped, reasonBufferFull)
	}

	if d.queue.len() >= d.cfg.MaxBatchSize {
		// Nudge the flusher instead of sending from the caller's goroutine.
		select {
		case d.wake <- struct{}{}:
		default:
		}
	}
}

func (d *dispatcher[T]) run() {
	defer d.wg.Done()

	ticker := time.NewTicker(d.cfg.FlushInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ticker.C:
			_ = d.drain(false)
		case <-d.wake:
			_ = d.drain(false)
		case <-d.done:
			_ = d.drain(true)
			return
		}
	}
}

// drain sends pending items in batches of at most MaxBatchSize. It stops at the
// first batch that is not fully accepted, so a throttled client makes exactly
// the progress its allowance permits and no more.
func (d *dispatcher[T]) drain(final bool) error {
	// One pass at a time. Without this, concurrent Flush() calls - a reasonable
	// reading of the API is to call it at the end of each HTTP handler - each
	// skipped the back.ready() check and sent simultaneously, and back.succeed()
	// from one pass cleared a notBefore another had just set via back.fail().
	// At concurrency 50 under 429 throttling that is 50 requests per burst with
	// the pacing guarantee void, which is exactly the amplification the backoff
	// exists to prevent. The other three SDKs all guard this; Go did not.
	//
	// A final pass waits its turn; a periodic one simply skips, because a pass
	// is already doing its work.
	if final {
		d.drainMu.Lock()
	} else if !d.drainMu.TryLock() {
		return nil
	}
	defer d.drainMu.Unlock()

	now := time.Now()

	if !d.readyToSend(final, now) {
		return nil
	}

	var lastErr error

	// A final pass runs until the queue is empty. maxChunksPerFlush bounds a
	// periodic cycle so a large backlog drains over several; applying it to
	// Close() abandons everything past 16 batches with no OnDrop call and no
	// error, for anyone who raises MaxBufferSize above 16 x MaxBatchSize. This
	// terminates: a partial acceptance and a throttle both return, and every
	// path that continues the loop shrinks the queue.
	for i := 0; final || i < maxChunksPerFlush; i++ {
		batch := d.queue.take(d.cfg.MaxBatchSize)
		if len(batch) == 0 {
			return lastErr
		}

		out := d.send(batch)

		stop, err := d.applyOutcome(out, batch[out.accepted:], now)
		if err != nil {
			lastErr = err
		}
		if stop {
			return lastErr
		}
		if err != nil {
			// Permanent: this batch is gone, but the next one may go through.
			continue
		}

		d.back.succeed()

		if len(batch) < d.cfg.MaxBatchSize {
			// The queue is drained; nothing to gain from another request.
			return lastErr
		}

		if !final && out.allowanceSpent {
			// The ingestor published that this tenant's allowance is spent.
			// Sending the next batch would only earn a 429 and a round trip,
			// which is exactly what publishing the headers is meant to avoid.
			// This is pacing, not a failure, so the exponential attempt
			// counter is left alone.
			d.back.pace(time.Now(), out.resetAfter)
			return lastErr
		}
	}

	return lastErr
}

// readyToSend applies the two guards that can end a pass before anything is sent:
// the backoff's pacing (which a final pass ignores, because Close() must try), and
// an active shed window (where the right move is to discard rather than buffer, since
// a storage quota does not clear by waiting).
//
// Extracted from drain for the same reason as applyOutcome — gocyclo. Together they
// take drain from 17 to 12 against a threshold of 15.
func (d *dispatcher[T]) readyToSend(final bool, now time.Time) bool {
	if !final && !d.back.ready(now) {
		return false
	}

	if d.shedding(now) {
		if dropped := d.queue.discard(); dropped > 0 {
			d.report(dropped, reasonStorageQuota)
		}
		return false
	}

	return true
}

// applyOutcome records one batch's result against the queue, the backoff and the
// drop reporter.
//
// It returns stop=true when the pass must end, and a non-nil err for anything the
// caller should report. The three combinations are the three real outcomes: a shed
// or a throttle stops the pass with that error; a permanent error returns
// stop=false with the error, so the caller records it and tries the next batch;
// and a clean send returns neither.
//
// Extracted from drain purely to keep it under the gocyclo threshold of 15 — it
// had reached 17, and the CI lint caught it where `go build`, `go vet` and
// `go test` could not. The behavior is unchanged: each branch does exactly what
// it did inline, and drain still decides what to return.
func (d *dispatcher[T]) applyOutcome(out pushOutcome, unsent []T, now time.Time) (bool, error) {
	switch {
	case out.shed:
		d.queue.countDropped(len(unsent))
		dropped := len(unsent) + d.queue.discard()
		d.beginShed(now.Add(out.retryAfter))
		d.report(dropped, out.reason)
		return true, out.err

	case out.retry:
		if dropped := d.queue.requeue(unsent); dropped > 0 {
			d.report(dropped, reasonBufferFull)
		}
		d.back.fail(now, out.retryAfter)
		return true, out.err

	case out.err != nil:
		d.queue.countDropped(len(unsent))
		d.report(len(unsent), out.reason)
		return false, out.err
	}

	return false, nil
}

// flush drains synchronously, for Flush() and Close().
func (d *dispatcher[T]) flush() error {
	return d.drain(true)
}

// close stops the flush loop after a final delivery attempt.
func (d *dispatcher[T]) close() error {
	d.closeOnce.Do(func() {
		d.mu.Lock()
		d.closed = true
		d.mu.Unlock()

		close(d.done)
		d.wg.Wait()
	})
	return nil
}

// isClosed reports whether close() has been called.
func (d *dispatcher[T]) isClosed() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.closed
}

// shedding reports whether data is being dropped at the door.
func (d *dispatcher[T]) shedding(now time.Time) bool {
	d.mu.Lock()
	defer d.mu.Unlock()

	return now.Before(d.shedUntil)
}

func (d *dispatcher[T]) beginShed(until time.Time) {
	d.mu.Lock()
	defer d.mu.Unlock()

	if until.After(d.shedUntil) {
		d.shedUntil = until
	}
}

// report surfaces dropped data through the configured hook, throttled so a
// sustained outage does not itself become a flood.
func (d *dispatcher[T]) report(count int, reason string) {
	if count <= 0 {
		return
	}

	d.mu.Lock()
	now := time.Now()
	throttled := now.Sub(d.lastLog) < dropReportInterval
	if !throttled {
		d.lastLog = now
	}
	d.mu.Unlock()

	if throttled {
		return
	}

	if d.cfg.OnDrop != nil {
		d.cfg.OnDrop(d.name, count, reason)
	}
}

// QueueStats describes the client-side send queue.
type QueueStats struct {
	// Pending is how many items are waiting to be sent.
	Pending int

	// Dropped is how many items this client has discarded in total.
	Dropped int64

	// Capacity is the configured buffer bound.
	Capacity int

	// RetryBackoff is the remaining wait before the next send attempt.
	RetryBackoff time.Duration

	// Shedding is true while data is being dropped on arrival, which happens
	// when the tenant is over its storage quota.
	Shedding bool
}

func (d *dispatcher[T]) stats() QueueStats {
	now := time.Now()

	return QueueStats{
		Pending:      d.queue.len(),
		Dropped:      d.queue.droppedTotal(),
		Capacity:     d.cfg.MaxBufferSize,
		RetryBackoff: d.back.delay(now),
		Shedding:     d.shedding(now),
	}
}

// dropReportInterval throttles drop notifications.
const dropReportInterval = 30 * time.Second

// PushError describes a rejected push.
type PushError struct {
	// Status is the HTTP status returned by the ingestor, or 0 for a
	// transport-level failure.
	Status int

	// Reason is the ingestor's machine-readable reason, when it gave one.
	Reason string

	// Message is the human-readable detail.
	Message string

	// Permanent is true when the payload will never be accepted, so the
	// client dropped it instead of retrying.
	Permanent bool
}

func (e *PushError) Error() string {
	msg := e.Message
	if msg == "" {
		msg = "push rejected"
	}

	if e.Status == 0 {
		return "horux: " + msg
	}
	return "horux: " + strconv.Itoa(e.Status) + " " + e.Reason + ": " + msg
}

// doPush posts one batch to the ingestor and interprets the response.
func doPush(httpClient *http.Client, cfg *Config, path string, body []byte, batchSize int) pushOutcome {
	req, err := http.NewRequestWithContext(context.Background(), http.MethodPost, cfg.IngestorURL+path, bytes.NewReader(body))
	if err != nil {
		return transportOutcome(err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+cfg.APIToken)
	req.Header.Set(partialAcceptHeader, "1")

	resp, err := httpClient.Do(req)
	if err != nil {
		// A connection problem is transient by nature; keep the data.
		return transportOutcome(err)
	}
	defer func() { _ = resp.Body.Close() }()

	respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 64*1024))

	return interpretPush(resp.StatusCode, resp.Header, respBody, batchSize)
}

func transportOutcome(err error) pushOutcome {
	return pushOutcome{
		retry:  true,
		reason: reasonTransport,
		err:    &PushError{Reason: reasonTransport, Message: err.Error()},
	}
}

// encodeOutcome reports a batch that could not even be serialized, which no
// amount of retrying will fix.
func encodeOutcome(err error) pushOutcome {
	return pushOutcome{
		reason: "invalid_payload",
		err:    &PushError{Reason: "invalid_payload", Message: err.Error(), Permanent: true},
	}
}
