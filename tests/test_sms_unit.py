#!/usr/bin/env python3
"""Unit tests for the SMS layer that need neither a phone nor a running
`kdeconnectd`. They cover the pieces that have actually bitten us: address
marshalling for `sendWithoutConversation`, the completeness criterion,
diacritic-insensitive matching, `since` parsing, thread matching by phone
number, and the on-disk "full stream" marker.

The module under test opens the D-Bus session bus at import time, so the
bus is stubbed before the import. The `dbus` and `gi` *packages* must still
be importable (system packages `python3-dbus`, `python3-gobject`); the
tests do not talk to any bus.

Run:  python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dbus  # noqa: E402

with mock.patch("dbus.SessionBus"):
    import mcp_server  # noqa: E402

KDEConnectDBus = mcp_server.KDEConnectDBus


def _client():
    """A KDEConnectDBus whose bus is a MagicMock."""
    with mock.patch("dbus.SessionBus"):
        return KDEConnectDBus()


def _msg(thread_id, body, addresses, date_ms, msg_type=1, message_id=1):
    """Build a D-Bus-shaped message struct, as `activeConversations()` and
    `conversationCreated` deliver it."""
    return (
        0, body, [(a,) for a in addresses], date_ms, msg_type,
        1, thread_id, message_id, 1, [],
    )


class NormalizeTests(unittest.TestCase):
    def test_diacritics_and_case_fold_together(self):
        self.assertEqual(mcp_server._norm("Objednávka"), "objednavka")
        self.assertEqual(mcp_server._norm("objednavka"), "objednavka")
        self.assertEqual(mcp_server._norm("ŽLTÝ kôň"), "zlty kon")

    def test_empty_and_none(self):
        self.assertEqual(mcp_server._norm(""), "")
        self.assertEqual(mcp_server._norm(None), "")

    def test_german_sharp_s_casefolds(self):
        # casefold (not lower) is what makes "Straße" match "strasse"
        self.assertEqual(mcp_server._norm("Straße"), "strasse")


class ParseSinceTests(unittest.TestCase):
    def test_accepted_formats_agree(self):
        import datetime
        day = int(datetime.datetime(2026, 7, 1).timestamp() * 1000)
        noon = int(datetime.datetime(2026, 7, 1, 12, 0).timestamp() * 1000)
        self.assertEqual(mcp_server._parse_since("2026-07-01"), day)
        self.assertEqual(mcp_server._parse_since(" 2026-07-01 "), day)
        self.assertEqual(mcp_server._parse_since("2026-07-01 12:00"), noon)
        self.assertEqual(mcp_server._parse_since("2026-07-01 12:00:00"), noon)
        self.assertEqual(mcp_server._parse_since("2026-07-01T12:00"), noon)

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            mcp_server._parse_since("last tuesday")


class ParseMessageTests(unittest.TestCase):
    def test_struct_is_mapped_to_named_fields(self):
        m = mcp_server._parse_sms_message(
            _msg(42, "hello", ["+421900123456", "+421900000000"],
                 1_700_000_000_000, msg_type=2, message_id=7))
        self.assertEqual(m["thread_id"], 42)
        self.assertEqual(m["message_id"], 7)
        self.assertEqual(m["body"], "hello")
        self.assertEqual(m["addresses"], ["+421900123456", "+421900000000"])
        self.assertEqual(m["direction"], "sent")
        self.assertTrue(m["read"])
        self.assertEqual(m["attachment_count"], 0)
        self.assertEqual(m["date_ms"], 1_700_000_000_000)

    def test_unknown_type_is_not_an_error(self):
        m = mcp_server._parse_sms_message(_msg(1, "", ["x"], 0, msg_type=99))
        self.assertEqual(m["direction"], "type_99")


class StreamCompleteTests(unittest.TestCase):
    """The criterion that replaced "cache size stopped changing", which
    reported complete after 32 s with 90 of 1754 threads (2026-10-30)."""

    def test_empty_cache_is_never_complete(self):
        self.assertFalse(KDEConnectDBus._stream_complete(0, 0, True))

    def test_quiet_but_not_covered_is_incomplete(self):
        # 90 of 1754 re-reported, then silence: that is a stalled stream
        self.assertFalse(KDEConnectDBus._stream_complete(1754, 90, True))

    def test_covered_but_still_streaming_is_incomplete(self):
        self.assertFalse(KDEConnectDBus._stream_complete(1754, 1754, False))

    def test_full_coverage_and_quiet_is_complete(self):
        self.assertTrue(KDEConnectDBus._stream_complete(1754, 1754, True))

    def test_coverage_threshold_tolerates_deleted_threads(self):
        # threads deleted on the phone but still cached must not block
        # completion forever; 98 % is enough
        threshold = int(KDEConnectDBus.SMS_STREAM_COVERAGE * 1000)
        self.assertTrue(KDEConnectDBus._stream_complete(1000, threshold, True))
        self.assertFalse(KDEConnectDBus._stream_complete(1000, threshold - 1, True))


class CachedThreadsTests(unittest.TestCase):
    def test_newest_message_per_thread_wins(self):
        c = _client()
        iface = mock.Mock()
        iface.activeConversations.return_value = [
            _msg(1, "old", ["a"], 100),
            _msg(1, "new", ["a"], 200),
            _msg(2, "other", ["b"], 150),
            ("garbage",),               # unparsable entries are skipped
        ]
        seen = c._cached_threads(iface)
        self.assertEqual(set(seen), {1, 2})
        self.assertEqual(seen[1]["body"], "new")


class ThreadForPhoneTests(unittest.TestCase):
    def setUp(self):
        self.c = _client()
        self.iface = mock.Mock()
        self.iface.activeConversations.return_value = [
            _msg(10, "x", ["+421900123456"], 1),
            _msg(11, "y", ["+421900123456", "+421900000000"], 2),  # group
            _msg(12, "z", ["0900999888"], 3),
        ]

    def test_national_and_international_notation_match(self):
        self.assertEqual(self.c._thread_for_phone(self.iface, "+421900123456"), 10)
        self.assertEqual(self.c._thread_for_phone(self.iface, "0900123456"), 10)
        self.assertEqual(self.c._thread_for_phone(self.iface, "+421 900 999 888"), 12)

    def test_group_thread_is_not_matched(self):
        self.assertEqual(self.c._thread_for_phone(self.iface, "+421900000000"), None)

    def test_short_number_gives_none(self):
        self.assertIsNone(self.c._thread_for_phone(self.iface, "1234"))


class SendSmsMarshallingTests(unittest.TestCase):
    """Regression for the silently lost messages of 2026-09-12 and
    2026-10-05: the daemon demarshals each address as the struct `(s)`;
    a dict yields an empty address and the phone drops the packet."""

    def setUp(self):
        self.c = _client()
        self.iface = mock.Mock()
        self.c._get_conversations_interface = mock.Mock(return_value=self.iface)
        self.c._thread_for_phone = mock.Mock(return_value=None)
        self.c._wait_for_sent = mock.Mock(return_value=None)

    def test_phone_address_travels_as_struct_in_variant(self):
        self.c.send_sms("dev", "hi", phone="+421900123456", verify_seconds=0)
        (addresses, text, attachments), _ = \
            self.iface.sendWithoutConversation.call_args
        self.assertIsInstance(addresses, dbus.Array)
        self.assertEqual(addresses.signature, "v")
        self.assertEqual(len(addresses), 1)
        addr = addresses[0]
        self.assertIsInstance(addr, dbus.Struct)
        self.assertNotIsInstance(addr, dbus.Dictionary)
        self.assertEqual(addr.signature, "s")
        self.assertEqual(addr.variant_level, 1)
        self.assertEqual(tuple(addr), ("+421900123456",))
        self.assertEqual(text, "hi")
        self.assertEqual(len(attachments), 0)
        self.iface.replyToConversation.assert_not_called()

    def test_thread_reply_uses_reply_to_conversation(self):
        self.c.send_sms("dev", "hi", thread_id=5, verify_seconds=0)
        (tid, text, _), _ = self.iface.replyToConversation.call_args
        self.assertEqual(int(tid), 5)
        self.iface.sendWithoutConversation.assert_not_called()

    def test_exactly_one_target_required(self):
        with self.assertRaises(ValueError):
            self.c.send_sms("dev", "hi")
        with self.assertRaises(ValueError):
            self.c.send_sms("dev", "hi", thread_id=1, phone="+421900123456")
        with self.assertRaises(ValueError):
            self.c.send_sms("dev", "", phone="+421900123456")

    def test_status_reflects_readback(self):
        self.c._wait_for_sent.return_value = None
        r = self.c.send_sms("dev", "hi", phone="+421900123456", verify_seconds=0)
        self.assertEqual(r["status"], "unverified")
        self.assertIn("warning", r)

        hit = {"direction": "sent", "thread_id": 9, "date": "2026-10-05 10:00:00"}
        self.c._wait_for_sent.return_value = hit
        r = self.c.send_sms("dev", "hi", phone="+421900123456", verify_seconds=0)
        self.assertEqual(r["status"], "confirmed")
        self.assertEqual(r["thread_id_seen"], 9)

        self.c._wait_for_sent.return_value = {**hit, "direction": "failed"}
        r = self.c.send_sms("dev", "hi", phone="+421900123456", verify_seconds=0)
        self.assertEqual(r["status"], "failed")


class WaitForSentTests(unittest.TestCase):
    def test_outgoing_message_with_same_body_is_found(self):
        c = _client()
        iface = mock.Mock()
        sent_ms = 1_000_000
        iface.activeConversations.return_value = [
            _msg(3, "Ahoj", ["+421900123456"], sent_ms + 5_000, msg_type=2),
        ]
        hit = c._wait_for_sent(iface, "dev", "ahoj", sent_ms, verify_seconds=0)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["thread_id"], 3)

    def test_old_or_received_messages_do_not_count(self):
        c = _client()
        iface = mock.Mock()
        sent_ms = 10_000_000
        iface.activeConversations.return_value = [
            _msg(3, "ahoj", ["x"], sent_ms - 120_000, msg_type=2),   # too old
            _msg(4, "ahoj", ["x"], sent_ms + 1_000, msg_type=1),     # received
        ]
        c.load_thread_messages = mock.Mock(return_value=([], {}))
        self.assertIsNone(
            c._wait_for_sent(iface, "dev", "ahoj", sent_ms, verify_seconds=0))


class FullStreamMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "cache", "full_stream.json")
        self.c = _client()
        self.c._FULL_STREAM_FILE = self.path

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_same_daemon_same_or_larger_cache(self):
        self.c._remember_full_stream("dev", ":1.42", 1754)
        with open(self.path) as f:
            self.assertEqual(json.load(f)["dev"]["heads"], 1754)
        self.assertTrue(self.c._full_stream_valid("dev", ":1.42", 1754))
        self.assertTrue(self.c._full_stream_valid("dev", ":1.42", 1760))

    def test_daemon_restart_or_shrunken_cache_invalidates(self):
        self.c._remember_full_stream("dev", ":1.42", 1754)
        self.assertFalse(self.c._full_stream_valid("dev", ":1.99", 1754))  # new owner
        self.assertFalse(self.c._full_stream_valid("dev", ":1.42", 90))    # cache emptied
        self.assertFalse(self.c._full_stream_valid("other", ":1.42", 1754))
        self.assertFalse(self.c._full_stream_valid("dev", None, 1754))

    def test_missing_file_and_no_owner(self):
        self.assertFalse(self.c._full_stream_valid("dev", ":1.42", 10))
        self.c._remember_full_stream("dev", None, 10)
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
