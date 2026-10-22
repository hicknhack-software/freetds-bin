import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("source", "scripts/source.py")
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.content = b"official source archive"
        self.release = {"draft": False, "assets": [{
            "name": "freetds-1.5.16.tar.bz2",
            "browser_download_url": "https://example.test/source.tar.bz2",
            "digest": "sha256:" + hashlib.sha256(self.content).hexdigest(),
        }]}
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.destination = Path(directory.name)

    @mock.patch.object(source.urllib.request, "urlopen")
    def test_verified_source_and_no_token_sent_to_download_host(self, urlopen):
        urlopen.side_effect = [io.BytesIO(json.dumps(self.release).encode()),
                               io.BytesIO(self.content)]
        with mock.patch.dict(os.environ, {"GH_TOKEN": "test-token"}):
            source.download("v1.5.16", self.destination)
        self.assertEqual((self.destination / "upstream.tar").read_bytes(), self.content)
        self.assertIn("Verified against GitHub asset digest: True",
                      (self.destination / "source-info.txt").read_text())
        request = urlopen.call_args_list[0].args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(urlopen.call_args_list[1].args,
                         ("https://example.test/source.tar.bz2",))

    @mock.patch.object(source.urllib.request, "urlopen")
    def test_corrupt_source_is_removed(self, urlopen):
        urlopen.side_effect = [io.BytesIO(json.dumps(self.release).encode()),
                               io.BytesIO(b"corrupted")]
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            source.download("v1.5.16", self.destination)
        self.assertFalse((self.destination / "upstream.tar").exists())

    @mock.patch.object(source.urllib.request, "urlopen")
    def test_missing_official_archive_fails(self, urlopen):
        self.release["assets"] = []
        urlopen.return_value = io.BytesIO(json.dumps(self.release).encode())
        with self.assertRaisesRegex(ValueError, "No official source archive"):
            source.download("v1.5.16", self.destination)

    @mock.patch.object(source.urllib.request, "urlopen")
    def test_invalid_tag_fails_before_network_access(self, urlopen):
        with self.assertRaisesRegex(ValueError, "Invalid upstream tag"):
            source.download("../invalid", self.destination)
        urlopen.assert_not_called()
