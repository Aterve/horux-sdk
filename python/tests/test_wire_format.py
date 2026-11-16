"""
The JSON this client puts on the wire.

Nothing in any of the four SDK suites asserted the bytes a datapoint produces,
and that blind spot hid a total-loss defect: the Java client serialised every
timestamp as a numeric epoch, the ingestor answered 400, and the dispatcher -
correctly treating 400 as permanent - dropped the batch. 100% of pushes were
discarded with 25 tests green.

The ingestor decodes ``timestamp`` into Go's ``*time.Time``, whose
``UnmarshalJSON`` accepts an RFC 3339 string with a UTC offset and nothing
else. Verified directly against that decoder:

    {"timestamp":1760000000.123}              -> input is not a JSON string
    {"timestamp":"2026-10-13T09:00:00.1234"}  -> cannot parse "" as "Z07:00"
    {"timestamp":"2026-10-13T09:00:00+00:00"} -> ok
"""

import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from horux_client._dispatch import wire_timestamp
from horux_client.logs_client import LogsClient
from horux_client.metrics_client import MetricsClient
from horux_client.types import LogEntry, LogLevel, Metric, MetricType

#: RFC 3339 with a mandatory offset, which is what Go's time.Time requires.
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$")


class WireTimestampTest(unittest.TestCase):
    def test_an_aware_timestamp_keeps_its_offset(self):
        at = datetime(2026, 10, 13, 9, 0, 0, 123456, tzinfo=timezone.utc)

        self.assertEqual(wire_timestamp(at), "2026-10-13T09:00:00.123456+00:00")
        self.assertRegex(wire_timestamp(at), RFC3339)

    def test_a_non_utc_offset_is_preserved_rather_than_dropped(self):
        at = datetime(2026, 10, 13, 9, 0, tzinfo=timezone(timedelta(hours=2)))

        self.assertEqual(wire_timestamp(at), "2026-10-13T09:00:00+02:00")
        self.assertRegex(wire_timestamp(at), RFC3339)

    def test_a_naive_timestamp_is_given_an_offset(self):
        # datetime.now() is naive, and isoformat() on it produces no offset at
        # all. Go rejects that with `cannot parse "" as "Z07:00"`, the ingestor
        # returns 400, and this client drops the batch as permanently
        # unacceptable - so a caller doing the obvious thing lost everything.
        rendered = wire_timestamp(datetime(2026, 10, 13, 9, 0, 0, 123456))

        self.assertRegex(
            rendered,
            RFC3339,
            "a naive datetime must still reach the server with a UTC offset",
        )

    def test_none_stays_none_so_the_server_stamps_it(self):
        self.assertIsNone(wire_timestamp(None))


class SerialisationTest(unittest.TestCase):
    def test_a_metric_carries_an_rfc3339_string_timestamp(self):
        serialised = MetricsClient._serialize(
            Metric(
                name="http_requests_total",
                type=MetricType.COUNTER,
                value=1.0,
                timestamp=datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc),
            )
        )

        self.assertIsInstance(serialised["timestamp"], str)
        self.assertRegex(serialised["timestamp"], RFC3339)
        self.assertEqual(serialised["type"], "counter")
        self.assertEqual(serialised["value"], 1.0)

    def test_a_metric_from_a_naive_timestamp_is_still_acceptable(self):
        serialised = MetricsClient._serialize(
            Metric(
                name="m",
                type=MetricType.GAUGE,
                value=0.0,
                timestamp=datetime(2026, 10, 13, 9, 0),
            )
        )

        self.assertRegex(serialised["timestamp"], RFC3339)

    def test_unset_metric_fields_are_omitted_rather_than_sent_as_null(self):
        # The ingestor validates per type: empty `buckets` on a histogram, or a
        # missing `value` on a counter, is a 400 this client would then drop.
        serialised = MetricsClient._serialize(Metric(name="m", type=MetricType.COUNTER, value=1.0))

        self.assertNotIn("buckets", serialised)
        self.assertNotIn("quantiles", serialised)
        self.assertNotIn("labels", serialised)

    def test_a_log_entry_carries_an_rfc3339_string_timestamp(self):
        serialised = LogsClient._serialize(
            LogEntry(
                level=LogLevel.ERROR,
                message="boom",
                timestamp=datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc),
            )
        )

        self.assertIsInstance(serialised["timestamp"], str)
        self.assertRegex(serialised["timestamp"], RFC3339)
        self.assertEqual(serialised["level"], "error")
        self.assertEqual(serialised["message"], "boom")


if __name__ == "__main__":
    unittest.main()
