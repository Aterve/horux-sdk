"""
Tests for the client-side send queue, retry pacing and response handling.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from dataclasses import dataclass
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from horux_client import _dispatch
from horux_client._dispatch import (
    DROP_NEWEST,
    DROP_OLDEST,
    MAX_CHUNKS_PER_FLUSH,
    MIN_SHED_SECONDS,
    PARTIAL_ACCEPT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    RATE_LIMIT_RESET_HEADER,
    REASON_CLIENT_CLOSED,
    REASON_INVALID_PAYLOAD,
    REASON_RATE_LIMIT,
    REASON_STORAGE_QUOTA,
    REASON_TRANSPORT,
    Backoff,
    BoundedQueue,
    Dispatcher,
    PushError,
    PushOutcome,
    do_push,
    interpret_push,
    parse_retry_after,
    read_quota_headers,
)


@dataclass
class FakeConfig:
    """The pacing surface Dispatcher reads, without the full HoruxConfig."""

    max_batch_size: int = 10
    max_buffer_size: int = 100
    drop_policy: str = DROP_OLDEST
    min_retry_backoff: float = 1.0
    max_retry_backoff: float = 60.0
    flush_interval: float = 3600.0
    http_timeout: float = 1.0
    ingestor_url: str = "http://ingestor.invalid"
    api_token: str = "tenant_x_secret"
    resource_id: str = "resource-1"
    on_drop: Callable[[str, int, str], None] | None = None


def accepted(n: int) -> PushOutcome:
    return PushOutcome(accepted=n)


class BoundedQueueTest(unittest.TestCase):
    def test_drops_oldest_when_full(self):
        q: BoundedQueue[int] = BoundedQueue(3, DROP_OLDEST)
        for i in range(1, 6):
            q.add(i)

        self.assertEqual(len(q), 3)
        self.assertEqual(q.dropped, 2)
        self.assertEqual(q.take(3), [3, 4, 5])

    def test_drops_newest_when_configured(self):
        q: BoundedQueue[int] = BoundedQueue(3, DROP_NEWEST)
        for i in range(1, 6):
            q.add(i)

        self.assertEqual(q.take(3), [1, 2, 3])
        self.assertEqual(q.dropped, 2)

    def test_take_returns_at_most_requested(self):
        q: BoundedQueue[int] = BoundedQueue(10, DROP_OLDEST)
        for i in range(1, 6):
            q.add(i)

        self.assertEqual(q.take(2), [1, 2])
        self.assertEqual(len(q), 3)

    def test_requeue_preserves_order(self):
        q: BoundedQueue[int] = BoundedQueue(10, DROP_OLDEST)
        q.add(3)

        q.requeue([1, 2])

        self.assertEqual(q.take(3), [1, 2, 3])

    def test_requeue_respects_the_bound(self):
        q: BoundedQueue[int] = BoundedQueue(3, DROP_OLDEST)
        q.add(8)
        q.add(9)

        self.assertEqual(q.requeue([1, 2, 3, 4]), 3)
        self.assertEqual(q.take(3), [4, 8, 9])

    def test_discard_counts_dropped(self):
        q: BoundedQueue[int] = BoundedQueue(10, DROP_OLDEST)
        for i in range(5):
            q.add(i)

        self.assertEqual(q.discard(), 5)
        self.assertEqual(len(q), 0)
        self.assertEqual(q.dropped, 5)


class BackoffTest(unittest.TestCase):
    def test_honours_server_hint(self):
        now = time.monotonic()
        b = Backoff(1.0, 300.0)

        delay = b.fail(hint=10.0, now=now)

        self.assertGreaterEqual(delay, 10.0)
        self.assertFalse(b.ready(now + 9.0))
        self.assertTrue(b.ready(now + delay))

    def test_pace_does_not_advance_the_attempt_counter(self):
        # Pacing is not a failure: holding off because the server said the
        # allowance is spent must not advance the exponential schedule, or a
        # paced client ends up backing off as if it were broken.
        now = time.monotonic()
        b = Backoff(1.0, 60.0)

        for _ in range(5):
            b.pace(10.0, now=now)

        self.assertFalse(b.ready(now + 9.0))
        self.assertTrue(b.ready(now + 11.0))
        self.assertLessEqual(b.fail(now=now), 1.0)

    def test_does_not_clamp_the_server_hint_to_max(self):
        # max bounds the client's own exponential guesswork, not an explicit
        # instruction from the other end. Clamping a `Retry-After: 300` from an
        # intermediary down to 60s retries five times inside a window something
        # just asked to stay quiet for.
        now = time.monotonic()
        b = Backoff(1.0, 60.0)

        delay = b.fail(hint=300.0, now=now)

        self.assertGreaterEqual(delay, 300.0)
        self.assertFalse(b.ready(now + 299.0))

    def test_grows_exponentially_up_to_the_cap(self):
        now = time.monotonic()
        b = Backoff(1.0, 4.0)

        for _ in range(10):
            self.assertLessEqual(b.fail(now=now), 4.0)

    def test_resets_on_success(self):
        b = Backoff(1.0, 60.0)
        b.fail(hint=30.0)
        b.succeed()

        self.assertTrue(b.ready())
        self.assertEqual(b.remaining(), 0.0)


class QuotaHeaderTest(unittest.TestCase):
    def test_reports_a_spent_allowance_and_its_reset_window(self):
        def headers(remaining=None, reset=None):
            h = {}
            if remaining is not None:
                h[RATE_LIMIT_REMAINING_HEADER] = remaining
            if reset is not None:
                h[RATE_LIMIT_RESET_HEADER] = reset
            return h

        self.assertEqual(read_quota_headers(headers("500", "12")), (False, 0.0))
        self.assertEqual(read_quota_headers(headers("0", "12")), (True, 12.0))
        self.assertEqual(read_quota_headers(headers("0")), (True, 0.0))
        self.assertEqual(read_quota_headers(headers("-3", "5")), (True, 5.0))

        # Absent means the quota is not enforced for this tenant; garbage must
        # not be read as a spent allowance and stall the client.
        self.assertEqual(read_quota_headers(headers()), (False, 0.0))
        self.assertEqual(read_quota_headers(headers("lots", "soon")), (False, 0.0))
        self.assertEqual(read_quota_headers(None), (False, 0.0))


class InterpretPushTest(unittest.TestCase):
    def test_full_acceptance(self):
        out = interpret_push(200, None, {"accepted": 100, "rejected": 0}, 100)

        self.assertEqual(out.accepted, 100)
        self.assertFalse(out.retry)
        self.assertFalse(out.shed)
        self.assertIsNone(out.error)

    def test_partial_acceptance_keeps_tail(self):
        # The case the old client could not express: the server took part of the
        # batch and the rest has to stay queued.
        body = {
            "accepted": 340,
            "rejected": 660,
            "reason": "rate_limit",
            "retry_after_ms": 12000,
        }

        out = interpret_push(200, None, body, 1000)

        self.assertEqual(out.accepted, 340)
        self.assertTrue(out.retry)
        self.assertEqual(out.retry_after, 12.0)
        self.assertEqual(out.reason, REASON_RATE_LIMIT)

    def test_rate_limit_reads_retry_after_header(self):
        out = interpret_push(
            429, {"Retry-After": "30"}, {"accepted": 0, "reason": "rate_limit"}, 500
        )

        self.assertTrue(out.retry)
        self.assertFalse(out.shed)
        self.assertEqual(out.retry_after, 30.0)

    def test_storage_quota_sheds(self):
        # Storage quota does not clear by waiting, so the data is shed rather
        # than held: holding it is what grew the buffer until the process died.
        out = interpret_push(429, None, {"reason": "storage_quota", "retry_after_ms": 300000}, 500)

        self.assertTrue(out.shed)
        self.assertFalse(out.retry)
        self.assertEqual(out.retry_after, 300.0)

    def test_storage_quota_has_a_shed_floor(self):
        out = interpret_push(429, None, {"reason": "storage_quota"}, 10)

        self.assertGreaterEqual(out.retry_after, MIN_SHED_SECONDS)

    def test_permanent_failures_are_dropped(self):
        for status in (400, 413, 422):
            out = interpret_push(status, None, {"error": "nope"}, 10)

            self.assertFalse(out.retry, status)
            self.assertIsInstance(out.error, PushError)
            self.assertTrue(out.error.permanent, status)

    def test_recoverable_failures_are_kept(self):
        for status in (401, 403, 404, 500, 502, 503):
            out = interpret_push(status, None, None, 10)

            self.assertTrue(out.retry, status)

    def test_accepted_is_capped_at_batch_size(self):
        out = interpret_push(200, None, {"accepted": 9999}, 10)

        self.assertEqual(out.accepted, 10)


class ParseRetryAfterTest(unittest.TestCase):
    def test_seconds_form(self):
        self.assertEqual(parse_retry_after("45"), 45.0)

    def test_missing_and_invalid(self):
        self.assertEqual(parse_retry_after(None), 0.0)
        self.assertEqual(parse_retry_after(""), 0.0)
        self.assertEqual(parse_retry_after("-5"), 0.0)
        self.assertEqual(parse_retry_after("garbage"), 0.0)

    def test_http_date_form(self):
        from email.utils import formatdate

        future = formatdate(time.time() + 90, usegmt=True)
        parsed = parse_retry_after(future)

        self.assertGreater(parsed, 80.0)
        self.assertLessEqual(parsed, 91.0)


class FakeResponse:
    def __init__(self, status_code: int, body: Any, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self) -> Any:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, response: Any):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class DoPushTest(unittest.TestCase):
    def test_announces_partial_accept_support(self):
        session = FakeSession(FakeResponse(200, {"accepted": 3}))

        out = do_push(session, FakeConfig(), "/api/v1/metrics/push", {"metrics": []}, 3)

        self.assertEqual(out.accepted, 3)
        self.assertEqual(session.calls[0]["headers"][PARTIAL_ACCEPT_HEADER], "1")

    def test_transport_failure_keeps_data(self):
        session = FakeSession(OSError("connection refused"))

        out = do_push(session, FakeConfig(), "/api/v1/logs/push", {"logs": []}, 5)

        self.assertTrue(out.retry)
        self.assertEqual(out.reason, REASON_TRANSPORT)

    def test_unparsable_body_falls_back_to_status(self):
        session = FakeSession(FakeResponse(503, ValueError("not json")))

        out = do_push(session, FakeConfig(), "/api/v1/logs/push", {"logs": []}, 5)

        self.assertTrue(out.retry)

    def test_reads_the_published_allowance_off_the_response(self):
        session = FakeSession(
            FakeResponse(
                200,
                {"accepted": 10},
                headers={
                    "X-RateLimit-Limit": "1000",
                    RATE_LIMIT_REMAINING_HEADER: "0",
                    RATE_LIMIT_RESET_HEADER: "18",
                },
            )
        )

        out = do_push(session, FakeConfig(), "/api/v1/metrics/push", {"metrics": []}, 10)

        self.assertEqual(out.accepted, 10)
        self.assertTrue(
            out.allowance_spent,
            "the ingestor publishes these so a client can pace itself rather than be rejected",
        )
        self.assertEqual(out.reset_after, 18.0)

    def test_an_unenforced_quota_leaves_pacing_alone(self):
        session = FakeSession(FakeResponse(200, {"accepted": 10}))

        out = do_push(session, FakeConfig(), "/api/v1/metrics/push", {"metrics": []}, 10)

        self.assertFalse(out.allowance_spent)
        self.assertEqual(out.reset_after, 0.0)


class DispatcherTest(unittest.TestCase):
    def setUp(self):
        self.dispatchers: list[Dispatcher] = []

    def tearDown(self):
        for dispatcher in self.dispatchers:
            dispatcher.close()

    def build(self, config: FakeConfig, send: Callable[[list[Any]], PushOutcome]) -> Dispatcher:
        dispatcher: Dispatcher = Dispatcher(config, "metrics", send)
        self.dispatchers.append(dispatcher)
        return dispatcher

    def test_drain_sends_in_batches(self):
        # A backlog is sent in batches of max_batch_size, never as one oversized
        # request. Sending the whole buffer in a single request is how a stalled
        # client grew a payload past the ingestor's 10MB limit and wedged.
        sizes: list[int] = []

        def send(items):
            sizes.append(len(items))
            return accepted(len(items))

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=100), send)
        for i in range(25):
            d._queue.add(i)

        d.flush()

        self.assertEqual(sizes, [10, 10, 5])
        self.assertEqual(len(d._queue), 0)

    def test_partial_acceptance_keeps_tail_and_stops(self):
        # The client makes exactly the progress its allowance permits.
        sizes: list[int] = []

        def send(items):
            sizes.append(len(items))
            return PushOutcome(
                accepted=4,
                retry=True,
                retry_after=10.0,
                reason=REASON_RATE_LIMIT,
                error=PushError(200, REASON_RATE_LIMIT, "throttled"),
            )

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=100), send)
        for i in range(25):
            d._queue.add(i)

        d.flush()

        self.assertEqual(sizes, [10], "exactly one attempt")
        self.assertEqual(len(d._queue), 21, "6 unsent plus the untouched 15")
        self.assertEqual(d._queue.take(6), [4, 5, 6, 7, 8, 9], "unsent keep their place")

    def test_backoff_blocks_the_next_cycle(self):
        # Without this, a throttled client retried on its normal cadence and -
        # because a full buffer also triggered a flush - ended up sending one
        # request per recorded datapoint.
        sizes: list[int] = []

        def send(items):
            sizes.append(len(items))
            return PushOutcome(
                retry=True,
                retry_after=30.0,
                reason=REASON_RATE_LIMIT,
                error=PushError(429, REASON_RATE_LIMIT, "throttled"),
            )

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=100), send)
        for i in range(5):
            d._queue.add(i)

        d._drain(final=False)
        self.assertEqual(len(sizes), 1)

        for _ in range(5):
            d._drain(final=False)
        self.assertEqual(len(sizes), 1, "no attempts during the backoff window")
        self.assertGreaterEqual(d.stats().retry_backoff, 30.0)

    def test_permanently_rejected_batch_is_dropped(self):
        sizes: list[int] = []
        drops: list[int] = []

        def send(items):
            sizes.append(len(items))
            if len(items) == 10:
                return PushOutcome(
                    reason=REASON_INVALID_PAYLOAD,
                    error=PushError(400, REASON_INVALID_PAYLOAD, "nope", permanent=True),
                )
            return accepted(len(items))

        config = FakeConfig(
            max_batch_size=10,
            max_buffer_size=100,
            on_drop=lambda stream, count, reason: drops.append(count),
        )
        d = self.build(config, send)
        for i in range(15):
            d._queue.add(i)

        d.flush()

        self.assertEqual(sizes, [10, 5], "bad batch dropped, next one sent")
        self.assertEqual(len(d._queue), 0)
        self.assertEqual(d._queue.dropped, 10)
        self.assertEqual(drops, [10])

    def test_storage_quota_sheds_instead_of_buffering(self):
        sizes: list[int] = []

        def send(items):
            sizes.append(len(items))
            return PushOutcome(
                shed=True,
                retry_after=300.0,
                reason=REASON_STORAGE_QUOTA,
                error=PushError(429, REASON_STORAGE_QUOTA, "over quota"),
            )

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=100), send)
        for i in range(30):
            d._queue.add(i)

        d.flush()

        self.assertEqual(len(d._queue), 0, "everything shed")
        self.assertTrue(d.stats().shedding)

        for i in range(50):
            d.add(i)
        self.assertEqual(len(d._queue), 0, "new data dropped at the door")

        d._drain(final=False)
        self.assertEqual(len(sizes), 1, "no further attempts during the shed window")

    def test_add_never_sends(self):
        # Recording never touches the network, no matter how full the buffer is.
        sizes: list[int] = []

        def send(items):
            sizes.append(len(items))
            return accepted(len(items))

        d = self.build(FakeConfig(max_batch_size=1000, max_buffer_size=2000), send)
        for i in range(500):
            d.add(i)

        self.assertEqual(sizes, [], "add() must not perform I/O")
        self.assertEqual(len(d._queue), 500)

    def test_add_bounds_the_buffer_and_reports(self):
        dropped: list[int] = []

        def send(items):
            return accepted(len(items))

        config = FakeConfig(
            max_batch_size=1000,
            max_buffer_size=20,
            on_drop=lambda stream, count, reason: dropped.append(count),
        )
        d = self.build(config, send)

        for i in range(100):
            d.add(i)

        self.assertEqual(len(d._queue), 20, "the bound holds")
        self.assertEqual(d._queue.dropped, 80)
        self.assertTrue(dropped, "on_drop was called")

    def test_paces_itself_when_the_allowance_is_spent(self):
        # A client that has just used the last of its allowance stops for the
        # window the server published, instead of sending the next batch and
        # being told 429. The ingestor sets those headers explicitly so a
        # client can pace itself; nothing read them.
        calls = []

        def send(items):
            calls.append(len(items))
            return PushOutcome(accepted=len(items), allowance_spent=True, reset_after=20.0)

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=1000), send)
        for i in range(200):
            d._queue.add(i)

        d._drain(final=False)

        self.assertEqual(len(calls), 1, "the cycle stops once the allowance is gone")
        self.assertEqual(len(d._queue), 190, "the remainder is kept")
        self.assertGreaterEqual(d.stats().retry_backoff, 19.0)

        d._drain(final=False)
        self.assertEqual(len(calls), 1, "no attempt inside the reset window")

        # A final pass ignores pacing: close() has one chance to deliver.
        d.flush()
        self.assertEqual(len(d._queue), 0, "a final pass attempts everything")

    def test_periodic_drain_is_bounded_per_cycle(self):
        # A periodic cycle is bounded so a large backlog drains over several
        # passes rather than monopolising the worker in one.
        calls = []

        def send(items):
            calls.append(len(items))
            return accepted(len(items))

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=1000), send)
        for i in range(1000):
            d._queue.add(i)

        d._drain(final=False)

        self.assertEqual(len(calls), MAX_CHUNKS_PER_FLUSH)
        self.assertEqual(len(d._queue), 1000 - MAX_CHUNKS_PER_FLUSH * 10)

    def test_final_drain_keeps_going_past_the_cycle_cap(self):
        # With max_buffer_size above 16 x max_batch_size, close() abandoned the
        # remainder with no drop report, no error and no log.
        calls = []

        def send(items):
            calls.append(len(items))
            return accepted(len(items))

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=1000), send)
        for i in range(1000):
            d._queue.add(i)

        d.flush()

        self.assertEqual(len(calls), 100, "1000 items in batches of 10")
        self.assertEqual(len(d._queue), 0, "nothing abandoned")

    # --- Lifecycle --------------------------------------------------------

    def test_close_delivers_pending_data(self):
        # Nothing asserted that close() delivers what is pending, in any of the
        # four SDKs, which is how several lifecycle defects shipped.
        sent: list[Any] = []

        def send(items):
            sent.extend(items)
            return accepted(len(items))

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=1000), send)
        for i in range(95):
            d.add(i)

        d.close()

        self.assertEqual(len(sent), 95)
        self.assertEqual(len(d._queue), 0)
        self.assertEqual(d._queue.dropped, 0)

    def test_close_is_idempotent(self):
        calls = []

        def send(items):
            calls.append(len(items))
            return accepted(len(items))

        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=100), send)
        d._queue.add(1)

        d.close()
        d.close()

        self.assertEqual(calls, [1])

    def test_close_gives_up_rather_than_blocking_forever(self):
        # join() returns after the grace period, but the worker's own pass may
        # still have up to 16 batches x http_timeout left. An unbounded acquire
        # here blocked on it, and HoruxClient.close() serialises metrics then
        # logs - so the worst case was roughly sixteen minutes of blocked
        # shutdown inside a SIGTERM handler, which is a SIGKILL.
        config = FakeConfig(max_batch_size=10, max_buffer_size=100, http_timeout=0.05)
        d = self.build(config, lambda items: accepted(len(items)))
        d._queue.add(1)

        # Stand in for a pass still in flight on the worker.
        d._drain_lock.acquire()
        try:
            with self.assertLogs("horux_client._dispatch", level="WARNING") as logs:
                started = time.monotonic()
                d.close()
                elapsed = time.monotonic() - started
        finally:
            d._drain_lock.release()

        self.assertLess(elapsed, 5.0, "close() must not wait on an unbounded acquire")
        self.assertTrue(
            any("left unsent" in line for line in logs.output),
            f"giving up must be reported, got {logs.output}",
        )
        self.assertEqual(len(d._queue), 1, "the item is left to the pass in flight")

    def test_add_after_close_is_counted_as_a_drop(self):
        # After close() there is no worker, so an item enqueued here would sit
        # in the queue forever: pending grew while dropped stayed 0.
        reports: list[Any] = []

        config = FakeConfig(
            max_batch_size=10,
            max_buffer_size=100,
            on_drop=lambda stream, count, reason: reports.append((count, reason)),
        )
        d = self.build(config, lambda items: accepted(len(items)))

        d.close()
        d.add(1)

        self.assertEqual(len(d._queue), 0, "nothing stranded in the queue")
        self.assertEqual(d._queue.dropped, 1)
        self.assertEqual(reports, [(1, REASON_CLIENT_CLOSED)])

    def test_an_atexit_hook_is_registered_as_a_safety_net(self):
        # The worker is a daemon thread, and Python kills daemon threads at
        # shutdown without running them, so a script that records metrics and
        # returns from main() - no close(), no `with` block - loses everything
        # pending. Nothing failed closed before this.
        from unittest import mock

        config = FakeConfig(max_batch_size=10, max_buffer_size=100)

        with mock.patch.object(_dispatch, "atexit") as fake_atexit:
            d = Dispatcher(config, "metrics", lambda items: accepted(len(items)))
            self.dispatchers.append(d)

            fake_atexit.register.assert_called_once_with(d.close)

            d.close()

            # Or every client that is closed properly still leaks a reference
            # to itself for the life of the process.
            fake_atexit.unregister.assert_called_once_with(d.close)

    def test_worker_thread_is_single_and_daemonized(self):
        # The old client started a thread per flush, so a client whose buffer
        # sat at the batch threshold started one per recorded datapoint.
        def send(items):
            return accepted(len(items))

        before = threading.active_count()
        d = self.build(FakeConfig(max_batch_size=10, max_buffer_size=100), send)

        for i in range(200):
            d.add(i)

        self.assertEqual(threading.active_count(), before + 1)
        self.assertTrue(d._worker.daemon)


if __name__ == "__main__":
    unittest.main()
