"""Bounded transport contracts; no live storage, credentials or deployment."""

import hashlib
import io
import unittest
from unittest.mock import MagicMock

from labelloop_api.management_objects import BoundedMinioObjects


def key_for(data):
    digest = hashlib.sha256(data).hexdigest()
    return f"sha256/{digest[:2]}/{digest}"


class ObjectTransportTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.transport = BoundedMinioObjects(self.client, "fixture", 8)
        self.response = self.client.get_object.return_value

    def assert_closed(self):
        self.response.close.assert_called_once_with()
        self.response.release_conn.assert_called_once_with()

    def test_construction_does_no_io_and_requires_explicit_bounds(self):
        self.client.assert_not_called()
        self.client.get_object.assert_not_called()
        for limit in (True, 0, -1, 256 * 1024 * 1024 + 1, "8", None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                BoundedMinioObjects(self.client, "fixture", limit)
        for bucket in ("", None, 1):
            with self.subTest(bucket=bucket), self.assertRaises(ValueError):
                BoundedMinioObjects(self.client, bucket, 8)

    def test_exact_limit_requires_eof_and_releases_connection(self):
        data = b"12345678"
        self.response.read.side_effect = io.BytesIO(data).read
        self.assertEqual(self.transport.read(key_for(data)), data)
        self.assertEqual([c.args for c in self.response.read.call_args_list], [(9,), (1,)])
        self.client.get_object.assert_called_once_with("fixture", key_for(data))
        self.assert_closed()

    def test_short_reads_do_not_imply_eof(self):
        self.response.read.side_effect = [b"1", b"23", b"4", b""]
        self.assertEqual(self.transport.read(key_for(b"1234")), b"1234")
        self.assert_closed()

    def test_empty_content_is_hash_checked(self):
        self.response.read.return_value = b""
        self.assertEqual(self.transport.read(key_for(b"")), b"")
        self.assert_closed()

    def test_limit_plus_one_refuses_without_draining_stream(self):
        self.response.read.return_value = b"123456789"
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.transport.read(key_for(b"123456789"))
        self.response.read.assert_called_once_with(9)
        self.assert_closed()

    def test_chunks_are_bounded_even_for_large_objects(self):
        data = b"x" * (1024 * 1024 + 1)
        transport = BoundedMinioObjects(self.client, "fixture", len(data))
        self.response.read.side_effect = io.BytesIO(data).read
        self.assertEqual(transport.read(key_for(data)), data)
        self.assertTrue(
            all(0 < c.args[0] <= 1024 * 1024 for c in self.response.read.call_args_list)
        )
        self.assert_closed()

    def test_wrong_digest_and_truncated_content_refuse(self):
        for value in (b"wrong", b"123"):
            with self.subTest(value=value):
                self.response.reset_mock()
                self.response.read.return_value = None
                self.response.read.side_effect = io.BytesIO(value).read
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    self.transport.read(key_for(b"1234"))
                self.assert_closed()

    def test_malformed_keys_refuse_before_object_access(self):
        digest = hashlib.sha256(b"1234").hexdigest()
        for key in (
            "../object",
            "https://example.invalid/object",
            "sha256/00/" + digest,
            key_for(b"1234") + "/extra",
            key_for(b"1234").upper(),
            None,
            1,
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.transport.read(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.transport.put(key, b"1234")
        self.client.get_object.assert_not_called()
        self.client.put_object.assert_not_called()

    def test_nonbyte_and_oversized_transport_chunks_refuse(self):
        for chunk in (None, "1234", bytearray(b"1234"), b"1234567890"):
            with self.subTest(chunk=chunk):
                self.response.reset_mock()
                self.response.read.return_value = chunk
                with self.assertRaisesRegex(ValueError, "transport response"):
                    self.transport.read(key_for(b"1234"))
                self.assert_closed()

    def test_read_error_closes_and_releases(self):
        self.response.read.side_effect = OSError("synthetic transport failure")
        with self.assertRaises(OSError):
            self.transport.read(key_for(b"1234"))
        self.assert_closed()

    def test_close_error_still_releases(self):
        self.response.read.return_value = b""
        self.response.close.side_effect = OSError("synthetic close failure")
        with self.assertRaises(OSError):
            self.transport.read(key_for(b""))
        self.assert_closed()

    def test_get_failure_does_not_claim_response_cleanup(self):
        self.client.get_object.side_effect = OSError("synthetic request failure")
        with self.assertRaises(OSError):
            self.transport.read(key_for(b"1234"))
        self.response.close.assert_not_called()
        self.response.release_conn.assert_not_called()

    def test_put_rejects_bad_or_oversized_content_before_io(self):
        for data in (b"wrong", b"123456789", "1234", bytearray(b"1234"), None):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.transport.put(key_for(b"1234"), data)
        self.client.put_object.assert_not_called()

    def test_put_uses_verified_bytes_and_binary_content_type(self):
        self.transport.put(key_for(b"1234"), b"1234")
        args, kwargs = self.client.put_object.call_args
        self.assertEqual(args[:2], ("fixture", key_for(b"1234")))
        self.assertEqual(args[2].read(), b"1234")
        self.assertEqual(kwargs, {"length": 4, "content_type": "application/octet-stream"})
        self.client.get_object.assert_not_called()

    def test_put_failure_propagates_without_read_or_retry(self):
        self.client.put_object.side_effect = OSError("synthetic upload failure")
        with self.assertRaises(OSError):
            self.transport.put(key_for(b"1234"), b"1234")
        self.client.put_object.assert_called_once()
        self.client.get_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
