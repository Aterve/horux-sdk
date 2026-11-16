"""
Send queue, retry pacing and response handling shared by the metrics and logs
clients.

The ingestor enforces a per-minute ingestion allowance. When a client exceeds
it the data is worth keeping, but keeping it requires three things that are easy
to get wrong: a bound on what is held, batches no larger than the server will
accept, and a wait between attempts. Without them a throttled client grows its
buffer without limit and retries the whole thing on every recorded datapoint.
"""

from __future__ import annotations

import atexit
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Generic, Sequence, TypeVar

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Rejection reasons reported by the ingestor.
REASON_RATE_LIMIT = "rate_limit"
REASON_STORAGE_QUOTA = "storage_quota"
REASON_BUFFER_FULL = "buffer_full"
REASON_BACKEND_UNAVAILABLE = "backend_unavailable"
REASON_TRANSPORT = "transport"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_REJECTED = "rejected"

#: Client-side: recorded after close(), when no worker is left to send it.
REASON_CLIENT_CLOSED = "client_closed"


def wire_timestamp(value: datetime | None) -> str | None:
    """
    Render a timestamp in the one format the ingestor accepts.

    The ingestor decodes this field into Go's ``*time.Time``, whose
    ``UnmarshalJSON`` requires an RFC 3339 string *with a UTC offset*. A naive
    ``datetime`` - which is exactly what ``datetime.now()`` hands back -
    serialises as ``2026-10-13T09:00:00.123456`` with no offset, and Go rejects
    that with ``cannot parse "" as "Z07:00"``. The handler turns it into
    ``400 invalid request body``, which this client correctly classifies as
    permanent and drops: a caller who passed ``datetime.now()`` would lose
    every datapoint and see only a throttled drop report.

    A naive value is interpreted as local time, which is what
    ``datetime.now()`` meant by it.
    """
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.astimezone()

    return value.isoformat()


#: Tells the ingestor this client understands a partial acceptance, i.e. that it
#: will keep the unaccepted tail rather than re-sending the whole batch. Without
#: it the server rejects a batch that does not fit whole, which a client holding
#: a large backlog can never get past.
PARTIAL_ACCEPT_HEADER = "X-Horux-Partial-Accept"

#: Which end of a full buffer is sacrificed when the ingestor cannot keep up.
#: "oldest" (the default) keeps the most recent telemetry, which during an
#: outage is usually the more useful half.
DROP_OLDEST = "oldest"
DROP_NEWEST = "newest"

#: Bounds the work one flush cycle does, so a large backlog drains over several.
MAX_CHUNKS_PER_FLUSH = 16

#: Floor for how long to shed data after a storage quota rejection (seconds).
MIN_SHED_SECONDS = 60.0

#: Throttle for drop notifications, so an outage does not itself become a flood.
DROP_REPORT_INTERVAL = 30.0

#: Statuses whose payload the ingestor will never accept, however long we wait.
PERMANENT_STATUSES = frozenset({400, 413, 422})


class PushError(Exception):
    """A rejected push."""

    def __init__(self, status: int, reason: str, message: str, permanent: bool = False):
        super().__init__(f"horux: {status} {reason}: {message}" if status else f"horux: {message}")
        #: HTTP status returned by the ingestor, or 0 for a transport failure.
        self.status = status
        #: The ingestor's machine-readable reason, when it gave one.
        self.reason = reason
        self.message = message
        #: True when the payload will never be accepted, so the data was dropped.
        self.permanent = permanent


@dataclass
class PushOutcome:
    """What the ingestor did with one batch."""

    #: How many items were taken. The ingestor always takes a prefix of the
    #: batch, so items[accepted:] is exactly what is left over.
    accepted: int = 0

    #: The leftover is worth keeping and sending again.
    retry: bool = False

    #: The leftover should be dropped and further data discarded until
    #: retry_after passes: a storage quota does not clear by waiting a moment,
    #: and holding data for it only grows the buffer.
    shed: bool = False

    #: The server's hint for when to come back (seconds).
    retry_after: float = 0.0

    #: True when the ingestor published a remaining quota of zero for this
    #: tenant, with ``reset_after`` the wait until the allowance is back.
    #:
    #: The ingestor sets these on every push explicitly "so a client can pace
    #: itself instead of discovering the limit by being rejected"; reading them
    #: is what stops that costing a wasted round trip and a 429.
    allowance_spent: bool = False

    reset_after: float = 0.0

    reason: str = ""

    error: PushError | None = None


@dataclass
class QueueStats:
    """The state of the client-side send queue."""

    #: Items waiting to be sent.
    pending: int

    #: Items this client has discarded in total.
    dropped: int

    #: The configured buffer bound.
    capacity: int

    #: Remaining wait before the next send attempt (seconds).
    retry_backoff: float

    #: True while data is dropped on arrival, which happens when the tenant is
    #: over its storage quota.
    shedding: bool


def parse_retry_after(value: str | None) -> float:
    """Read both forms of Retry-After, seconds and HTTP date."""
    if not value:
        return 0.0

    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        return seconds if seconds > 0 else 0.0

    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return 0.0

    if when is None:
        return 0.0

    delta = when.timestamp() - time.time()
    return delta if delta > 0 else 0.0


#: The allowance the ingestor publishes on every push.
RATE_LIMIT_REMAINING_HEADER = "X-RateLimit-Remaining"
RATE_LIMIT_RESET_HEADER = "X-RateLimit-Reset"


def read_quota_headers(headers: dict[str, str] | None) -> tuple[bool, float]:
    """
    Report whether the tenant's allowance is spent and, if so, the wait until it
    returns.

    ``X-RateLimit-Reset`` is a whole number of *seconds until the full
    allowance is available again*, not an absolute time. An absent header means
    the quota is not enforced for this tenant, and a malformed one must not be
    read as a spent allowance - that would stall a client on garbage.
    """
    if not headers:
        return False, 0.0

    try:
        remaining = int(headers.get(RATE_LIMIT_REMAINING_HEADER, ""))
    except (TypeError, ValueError):
        return False, 0.0

    if remaining > 0:
        return False, 0.0

    try:
        seconds = int(headers.get(RATE_LIMIT_RESET_HEADER, ""))
    except (TypeError, ValueError):
        return True, 0.0

    return True, float(seconds) if seconds > 0 else 0.0


def interpret_push(
    status: int,
    headers: dict[str, str] | None,
    body: dict[str, Any] | None,
    batch_size: int,
) -> PushOutcome:
    """
    Turn an ingestor response into an outcome.

    The distinction that matters is whether the leftover is worth keeping:
    anything the server might accept later is retried, a payload it will never
    accept is dropped rather than retried forever, and data that has nowhere to
    go at all (storage quota) is shed.
    """
    parsed = body or {}
    message = parsed.get("error") or parsed.get("message") or "push rejected"
    reason = parsed.get("reason") or ""

    retry_after = float(parsed.get("retry_after_ms") or 0) / 1000.0
    if not retry_after and headers:
        retry_after = parse_retry_after(headers.get("Retry-After"))

    accepted = parsed.get("accepted") or 0
    if not isinstance(accepted, int) or accepted < 0:
        accepted = 0
    accepted = min(accepted, batch_size)

    allowance_spent, reset_after = read_quota_headers(headers)

    if reason == REASON_STORAGE_QUOTA:
        return PushOutcome(
            accepted=accepted,
            shed=True,
            retry_after=max(retry_after, MIN_SHED_SECONDS),
            reason=REASON_STORAGE_QUOTA,
            error=PushError(status, REASON_STORAGE_QUOTA, message),
        )

    if status == 200:
        if accepted >= batch_size:
            return PushOutcome(
                accepted=batch_size,
                allowance_spent=allowance_spent,
                reset_after=reset_after,
            )

        # Partially accepted: keep the tail, come back when told to.
        reason = reason or REASON_REJECTED
        return PushOutcome(
            accepted=accepted,
            retry=True,
            retry_after=retry_after,
            reason=reason,
            error=PushError(status, reason, message),
        )

    if status in PERMANENT_STATUSES:
        # The payload itself is unacceptable; retrying it forever would block
        # every later batch behind data that can never be sent.
        reason = reason or REASON_INVALID_PAYLOAD
        return PushOutcome(
            accepted=accepted,
            reason=reason,
            error=PushError(status, reason, message, permanent=True),
        )

    # Rate limits, auth problems and server errors can all clear on their own,
    # so the data is kept.
    reason = reason or (REASON_RATE_LIMIT if status == 429 else REASON_BACKEND_UNAVAILABLE)
    return PushOutcome(
        accepted=accepted,
        retry=True,
        retry_after=retry_after,
        reason=reason,
        error=PushError(status, reason, message),
    )


def transport_outcome(error: Exception) -> PushOutcome:
    """A connection problem is transient by nature, so the data is kept."""
    return PushOutcome(
        retry=True,
        reason=REASON_TRANSPORT,
        error=PushError(0, REASON_TRANSPORT, str(error)),
    )


class BoundedQueue(Generic[T]):
    """
    A bounded FIFO of items waiting to be sent.

    The bound is the point: an unbounded buffer turns a rate limit or an outage
    into unbounded memory growth inside the host application, and the telemetry
    client is never worth taking the application down for.
    """

    def __init__(self, max_size: int, policy: str):
        self._items: list[T] = []
        self._max_size = max_size
        self._policy = policy
        self._dropped = 0
        self._lock = threading.Lock()

    def add(self, item: T) -> int:
        """Append an item, dropping to stay within the bound."""
        with self._lock:
            if len(self._items) < self._max_size:
                self._items.append(item)
                return 0

            if self._policy == DROP_NEWEST:
                self._dropped += 1
                return 1

            overflow = len(self._items) - self._max_size + 1
            del self._items[:overflow]
            self._items.append(item)
            self._dropped += overflow
            return overflow

    def take(self, n: int) -> list[T]:
        """Remove and return up to n items from the front."""
        with self._lock:
            batch = self._items[:n]
            del self._items[: len(batch)]
            return batch

    def requeue(self, items: Sequence[T]) -> int:
        """Return unsent items to the front, preserving order and the bound."""
        if not items:
            return 0

        with self._lock:
            combined = list(items) + self._items

            dropped = 0
            if len(combined) > self._max_size:
                dropped = len(combined) - self._max_size
                if self._policy == DROP_NEWEST:
                    combined = combined[: self._max_size]
                else:
                    combined = combined[dropped:]
                self._dropped += dropped

            self._items = combined
            return dropped

    def discard(self) -> int:
        """Drop everything pending, for data the server will never accept."""
        with self._lock:
            n = len(self._items)
            self._items.clear()
            self._dropped += n
            return n

    def count_dropped(self, n: int) -> None:
        if n <= 0:
            return

        with self._lock:
            self._dropped += n

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped


class Backoff:
    """
    Paces retries after a failed or throttled flush.

    Without it a client that keeps data on failure retries on its normal flush
    cadence, and because a full buffer also triggered a flush on every recorded
    item, a throttled client ended up sending a request per datapoint.
    """

    def __init__(self, min_delay: float, max_delay: float):
        self._min = min_delay
        self._max = max_delay
        self._attempt = 0
        self._not_before = 0.0
        self._lock = threading.Lock()

    def ready(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return now >= self._not_before

    def fail(self, hint: float = 0.0, now: float | None = None) -> float:
        """
        Record a failure and schedule the next attempt. A server-supplied hint
        wins over the exponential schedule, since the server knows exactly when
        the allowance returns; jitter on top keeps a fleet from retrying in
        lockstep. ``max`` bounds the exponential branch only.
        """
        now = time.monotonic() if now is None else now

        with self._lock:
            self._attempt += 1

            if hint > 0:
                # The hint wins outright and is deliberately not clamped to
                # max: that bound is on the client's own exponential guesswork,
                # not on an explicit instruction from the other end. Clamping a
                # ``Retry-After: 300`` from an intermediary (CDN, API gateway,
                # WAF under load) to 60s retries five times inside a window
                # something just asked to stay quiet for.
                delay = hint + random.uniform(0, hint / 4)
            else:
                exp = min(self._min * (2 ** (self._attempt - 1)), self._max)
                # Full jitter: anywhere in (0, exp].
                delay = min(random.uniform(0, exp) or exp, self._max)

            self._not_before = now + delay
            return delay

    def pace(self, delay: float, now: float | None = None) -> None:
        """
        Hold off the next cycle without recording a failure.

        The server has published that the allowance is spent, so sending again
        would only earn a rejection - but nothing has actually failed, so the
        exponential attempt counter must not advance.
        """
        now = time.monotonic() if now is None else now
        delay = delay if delay > 0 else self._min

        with self._lock:
            self._not_before = max(self._not_before, now + delay)

    def succeed(self) -> None:
        with self._lock:
            self._attempt = 0
            self._not_before = 0.0

    def remaining(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self._lock:
            return max(0.0, self._not_before - now)


class Dispatcher(Generic[T]):
    """
    Owns the pending queue, the flush cycle and the retry state for one stream
    (metrics or logs).

    Recording an item only enqueues it; the HTTP request always happens on the
    dispatcher's single worker thread. Sending from the caller's thread is what
    turned a throttled client into a request per datapoint - and, because each
    send was handed to a fresh thread, into a thread per datapoint as well.
    """

    def __init__(
        self,
        config: Any,
        stream: str,
        send: Callable[[list[T]], PushOutcome],
    ):
        self._config = config
        self._stream = stream
        self._send = send

        self._queue: BoundedQueue[T] = BoundedQueue(config.max_buffer_size, config.drop_policy)
        self._backoff = Backoff(config.min_retry_backoff, config.max_retry_backoff)

        self._shed_until = 0.0
        self._last_report = 0.0
        self._state_lock = threading.Lock()

        self._drain_lock = threading.Lock()
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._closed = False

        self._worker = threading.Thread(
            target=self._run, name=f"horux-{stream}-flusher", daemon=True
        )
        self._worker.start()

        # The worker is a daemon thread, so telemetry never holds the
        # interpreter open - but Python kills daemon threads at shutdown
        # *without running them*, so a script that records metrics and simply
        # returns from main() loses everything pending. HoruxClient offers
        # __enter__/__exit__, but nothing failed closed. close() is idempotent,
        # so this hook is harmless when the caller does close explicitly.
        atexit.register(self.close)

    def add(self, item: T) -> None:
        """Enqueue an item. Never performs I/O and never blocks on the network."""
        if self.closed():
            # There is no worker left. Without this the item was enqueued
            # anyway and never sent and never counted, so a library logging
            # during its own teardown grew stats().pending while dropped
            # stayed 0.
            self._queue.count_dropped(1)
            self._report(1, REASON_CLIENT_CLOSED)
            return

        if self.shedding():
            # Nowhere to put this data; dropping it now keeps memory flat.
            self._queue.count_dropped(1)
            self._report(1, REASON_STORAGE_QUOTA)
            return

        dropped = self._queue.add(item)
        if dropped:
            self._report(dropped, REASON_BUFFER_FULL)

        if len(self._queue) >= self._config.max_batch_size:
            # Nudge the worker instead of sending from the caller's thread.
            self._wake.set()

    def flush(self) -> None:
        """Drain synchronously, ignoring the retry backoff."""
        self._drain(final=True)

    def close(self) -> None:
        """Stop the worker and make a final delivery attempt."""
        with self._state_lock:
            if self._closed:
                return
            self._closed = True

        self._stopped.set()
        self._wake.set()

        grace = self._config.http_timeout + 1.0
        self._worker.join(timeout=grace)

        # The final pass is bounded. ``join`` returns after the grace period,
        # but the worker's own pass may still have up to MAX_CHUNKS_PER_FLUSH
        # batches x http_timeout left, and an unbounded acquire here then
        # blocked on it - HoruxClient.close() serialises metrics then logs, so
        # the worst case was roughly sixteen minutes of blocked shutdown inside
        # a SIGTERM handler, which is a SIGKILL.
        self._drain(final=True, lock_timeout=grace)

        atexit.unregister(self.close)

    def closed(self) -> bool:
        """True once close() has run; add() then reports drops instead of queuing."""
        with self._state_lock:
            return self._closed

    def stats(self) -> QueueStats:
        return QueueStats(
            pending=len(self._queue),
            dropped=self._queue.dropped,
            capacity=self._config.max_buffer_size,
            retry_backoff=self._backoff.remaining(),
            shedding=self.shedding(),
        )

    def shedding(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._state_lock:
            return now < self._shed_until

    def _run(self) -> None:
        while not self._stopped.is_set():
            # Clear before waiting, not after. An add() that set the event in
            # the window between wait() returning and clear() had its signal
            # discarded, which defeated the batch-threshold nudge exactly when
            # the buffer was filling fastest. Clearing first means a set that
            # lands before the wait makes the wait return immediately, and a
            # set that lands during a pass is still covered by the queue check.
            self._wake.clear()

            if not self._stopped.is_set() and len(self._queue) < self._config.max_batch_size:
                # Wake on either the flush interval or a filled batch.
                self._wake.wait(timeout=self._config.flush_interval)

            if self._stopped.is_set():
                return

            try:
                self._drain(final=False)
            except Exception:  # pragma: no cover - a flush must never kill the worker
                logger.exception("horux: unexpected error while flushing %s", self._stream)

    def _drain(self, final: bool, lock_timeout: float | None = None) -> None:
        """
        Send pending items in batches of at most max_batch_size, stopping at the
        first batch that is not fully accepted, so a throttled client makes
        exactly the progress its allowance permits and no more.

        ``lock_timeout`` bounds how long a final pass waits for a pass already
        in flight; without it close() can block for minutes.
        """
        if final and lock_timeout is not None:
            acquired = self._drain_lock.acquire(timeout=lock_timeout)
        else:
            acquired = self._drain_lock.acquire(blocking=final)

        if not acquired:
            # Another thread is already draining; overlapping passes would send
            # the same backlog twice over.
            if final:
                logger.warning(
                    "horux: gave up waiting for an in-flight %s flush; %d items left unsent",
                    self._stream,
                    len(self._queue),
                )
            return

        try:
            now = time.monotonic()

            if not final and not self._backoff.ready(now):
                return

            if self.shedding(now):
                dropped = self._queue.discard()
                if dropped:
                    self._report(dropped, REASON_STORAGE_QUOTA)
                return

            # A final pass runs until the queue is empty. The cap bounds a
            # periodic cycle so a large backlog drains over several; applying
            # it to close() abandons everything past 16 batches with no drop
            # report and no error once max_buffer_size exceeds
            # 16 x max_batch_size. This terminates: a partial acceptance and a
            # throttle both return, and every path that continues the loop
            # shrinks the queue.
            chunks = 0
            while final or chunks < MAX_CHUNKS_PER_FLUSH:
                chunks += 1

                batch = self._queue.take(self._config.max_batch_size)
                if not batch:
                    return

                outcome = self._send(batch)
                unsent = batch[outcome.accepted :]

                if outcome.shed:
                    self._queue.count_dropped(len(unsent))
                    dropped = len(unsent) + self._queue.discard()
                    self._begin_shed(time.monotonic() + outcome.retry_after)
                    self._report(dropped, outcome.reason)
                    return

                if outcome.retry:
                    dropped = self._queue.requeue(unsent)
                    if dropped:
                        self._report(dropped, REASON_BUFFER_FULL)
                    self._backoff.fail(outcome.retry_after)
                    logger.debug(
                        "horux: %s push throttled (%s), %d items kept",
                        self._stream,
                        outcome.reason,
                        len(unsent),
                    )
                    return

                if outcome.error is not None:
                    # Permanent: drop this batch and keep going with the next.
                    self._queue.count_dropped(len(unsent))
                    self._report(len(unsent), outcome.reason)
                    continue

                self._backoff.succeed()

                if len(batch) < self._config.max_batch_size:
                    # The queue is drained; nothing to gain from another request.
                    return

                if not final and outcome.allowance_spent:
                    # The ingestor published that this tenant's allowance is
                    # spent. Sending the next batch would only earn a 429 and a
                    # round trip, which is exactly what publishing those
                    # headers exists to avoid. This is pacing, not a failure,
                    # so the exponential attempt counter is left alone.
                    self._backoff.pace(outcome.reset_after)
                    return
        finally:
            self._drain_lock.release()

    def _begin_shed(self, until: float) -> None:
        with self._state_lock:
            self._shed_until = max(self._shed_until, until)

    def _report(self, count: int, reason: str) -> None:
        """Surface dropped data, throttled so an outage is not itself a flood."""
        if count <= 0:
            return

        now = time.monotonic()
        with self._state_lock:
            if now - self._last_report < DROP_REPORT_INTERVAL:
                return
            self._last_report = now

        logger.warning(
            "horux: dropped %d %s (%s); total dropped %d",
            count,
            self._stream,
            reason,
            self._queue.dropped,
        )

        on_drop = getattr(self._config, "on_drop", None)
        if on_drop is not None:
            try:
                on_drop(self._stream, count, reason)
            except Exception:  # pragma: no cover - a hook must not break sending
                logger.exception("horux: on_drop hook raised")


def do_push(
    session: requests.Session,
    config: Any,
    path: str,
    payload: dict[str, Any],
    batch_size: int,
) -> PushOutcome:
    """Post one batch to the ingestor and interpret the response."""
    try:
        response = session.post(
            f"{config.ingestor_url}{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {config.api_token}",
                "Content-Type": "application/json",
                PARTIAL_ACCEPT_HEADER: "1",
            },
            timeout=config.http_timeout,
        )
    except Exception as error:  # noqa: BLE001 - deliberate, see below
        # Every transport failure must become an outcome rather than an escaped
        # exception. This runs on the background dispatch thread, where an
        # exception does not reach the caller: it kills the thread, and the client
        # then buffers forever with nothing sending and nothing logged. requests
        # raises a documented hierarchy, but a broken proxy, a bad TLS context or
        # an urllib3 change can surface something outside it, and the correct
        # handling for all of them is identical.
        return transport_outcome(error)

    try:
        body = response.json()
    except ValueError:
        body = None

    if not isinstance(body, dict):
        body = None

    return interpret_push(response.status_code, dict(response.headers), body, batch_size)
