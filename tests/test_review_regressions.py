import hashlib
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler
from zipfile import ZipFile

from fontTools.ttLib import TTFont

from test_extract_fonts import make_font
from extract_fonts import Bundle, download, sha256
from font_metadata import inspect_font


class Response(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def geturl(self):
        return "https://cdn.example.test/font.ttf"


class ReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.font = self.root / "fixture.ttf"
        make_font(self.font)

    def test_case_only_copy_collisions_keep_last_spelling_bytes_and_source(self):
        other = self.root / "other"
        other.mkdir()
        replacement = other / "FIXTURE.TTF"
        make_font(replacement, "Replacement")
        for index, (first, last) in enumerate(((self.font, replacement), (replacement, self.font))):
            with self.subTest(last=last.name):
                bundle = Bundle(f"fonts-{index}", self.root)
                bundle.copy(first)
                bundle.copy(last)
                manifest = bundle.write(self.root / "dist")
                self.assertEqual([p.name for p in bundle.directory.iterdir()], [last.name])
                self.assertEqual(manifest["file_count"], 1)
                entry = manifest["files"][0]
                self.assertEqual(entry["relative_path"], last.name)
                self.assertEqual(entry["source"]["path"], str(last))
                self.assertEqual(entry["sha256"], sha256(last))
                with ZipFile(self.root / "dist" / f"{bundle.name}.zip") as archive:
                    self.assertEqual(archive.namelist(), [f"{bundle.name}/{last.name}"])
                    self.assertEqual(archive.read(entry["path"]), last.read_bytes())

    def test_case_only_collisions_across_copy_download_and_archive(self):
        bundle = Bundle("fonts-test", self.root)
        bundle.copy(self.font)
        data = self.font.read_bytes()
        with patch("extract_fonts.urlopen", return_value=Response(data)):
            bundle.download_font("https://example.test/font.ttf", "Fixture.TTF")
        self.assertEqual(list(bundle.sources), ["Fixture.TTF"])
        self.assertEqual(bundle.sources["Fixture.TTF"]["type"], "web")
        self.assertEqual([p.name for p in bundle.directory.iterdir()], ["Fixture.TTF"])
        archive_path = self.root / "source.zip"
        with ZipFile(archive_path, "w") as archive:
            archive.write(self.font, "fonts/FIXTURE.ttf")
        source = {"type": "web", "url": "https://example.test/fonts.zip"}
        bundle.extract_archive(archive_path, source, "fonts/*")
        manifest = bundle.write(self.root / "dist")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual([p.name for p in bundle.directory.iterdir()], ["FIXTURE.ttf"])
        self.assertEqual(manifest["files"][0]["relative_path"], "FIXTURE.ttf")
        self.assertEqual(manifest["files"][0]["source"], {**source, "archive_member": "fonts/FIXTURE.ttf"})

    def test_permanent_http_errors_fail_without_retrying(self):
        for code in (400, 401, 403, 404):
            with self.subTest(code=code):
                error = HTTPError("https://example.test/font.ttf", code, "permanent", {}, None)
                with patch("extract_fonts.urlopen", side_effect=error) as request, patch("extract_fonts.time.sleep") as sleep:
                    with self.assertRaises(HTTPError):
                        download(error.url, self.root / "download.ttf")
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()

    def test_transient_http_errors_retry(self):
        data = self.font.read_bytes()
        for code in (408, 429, 500, 502, 503):
            with self.subTest(code=code):
                error = HTTPError("https://example.test/font.ttf", code, "transient", {}, None)
                with patch("extract_fonts.urlopen", side_effect=[error, Response(data)]) as request, patch("extract_fonts.time.sleep") as sleep:
                    download(error.url, self.root / "download.ttf")
                self.assertEqual(request.call_count, 2)
                sleep.assert_called_once_with(1)
                self.assertEqual((self.root / "download.ttf").read_bytes(), data)

    def test_retry_budget_is_bounded(self):
        with patch("extract_fonts.urlopen", side_effect=IncompleteRead(b"", 100)) as request, patch("extract_fonts.time.sleep") as sleep:
            with self.assertRaises(IncompleteRead):
                download("https://example.test/font.ttf", self.root / "download.ttf")
        self.assertEqual(request.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    def test_incomplete_body_retries_and_discards_partial_bytes(self):
        class InterruptedResponse(Response):
            def read(self, size=-1):
                if self.tell():
                    raise IncompleteRead(b"", 100)
                return super().read(20)
        data = self.font.read_bytes()
        first = InterruptedResponse(b"x" * 100)
        target = self.root / "download.ttf"
        with patch("extract_fonts.urlopen", side_effect=[first, Response(data)]) as request, patch("extract_fonts.time.sleep"):
            source = download("https://example.test/font.ttf", target)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(first.closed)
        self.assertEqual(target.read_bytes(), data)
        self.assertEqual(source["download_sha256"], hashlib.sha256(data).hexdigest())

    def test_short_content_length_retries_even_without_read_exception(self):
        data = self.font.read_bytes()
        short = Response(b"short", {"Content-Length": str(len(data))})
        with patch("extract_fonts.urlopen", side_effect=[short, Response(data, {"Content-Length": str(len(data))})]) as request, patch("extract_fonts.time.sleep"):
            download("https://example.test/font.ttf", self.root / "download.ttf")
        self.assertEqual(request.call_count, 2)
        self.assertEqual((self.root / "download.ttf").read_bytes(), data)

    def test_github_token_is_scoped_to_https_api_and_not_redirected(self):
        data = self.font.read_bytes()
        with patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}):
            for url, authorized in (
                ("https://api.github.com/repos/example/fonts/releases/latest", True),
                ("http://api.github.com/repos/example/fonts/releases/latest", False),
                ("https://api.github.com.example.test/font.ttf", False),
                ("https://github.com/example/fonts/releases/download/v1/font.ttf", False),
                ("https://raw.githubusercontent.com/example/fonts/main/font.ttf", False),
                ("https://updates.cdn-apple.com/font.zip", False),
            ):
                with self.subTest(url=url), patch("extract_fonts.urlopen", return_value=Response(data)) as open_url:
                    provenance = download(url, self.root / "download.ttf")
                    request = open_url.call_args.args[0]
                    self.assertEqual(request.get_header("Authorization"), "Bearer test-token" if authorized else None)
                    redirected = HTTPRedirectHandler().redirect_request(request, None, 302, "Found", {}, "https://cdn.example.test/font.ttf")
                    self.assertIsNone(redirected.get_header("Authorization"))
                    self.assertNotIn("test-token", json.dumps(provenance))
        with patch.dict("os.environ", {}, clear=True), patch("extract_fonts.urlopen", return_value=Response(data)) as open_url:
            download("https://api.github.com/repos/example/fonts/releases/latest", self.root / "download.ttf")
            self.assertIsNone(open_url.call_args.args[0].get_header("Authorization"))

    def test_valid_utf8_vendor_id_preserves_original_bytes(self):
        vendor_bytes = b"\xc3\xa9  "
        with TTFont(self.font, lazy=True) as font:
            offset = font.reader.tables["OS/2"].offset
        data = bytearray(self.font.read_bytes())
        data[offset + 58:offset + 62] = vendor_bytes
        self.font.write_bytes(data)
        # Pinned fontTools 4.66.1 decodes ASCII, not UTF-8, in sstruct.unpack.
        with TTFont(self.font) as font:
            self.assertEqual(font["OS/2"].achVendID, vendor_bytes)
        face = inspect_font(self.font)["faces"][0]
        self.assertEqual(face["vendor_id"].encode("latin-1"), vendor_bytes)
        self.assertEqual(face["vendor_id_hex"], vendor_bytes.hex())
        self.assertEqual(face["metadata_status"], "complete")

    def test_filler_scripts_are_omitted_without_changing_character_count(self):
        with TTFont(self.font) as font:
            for table in font["cmap"].tables:
                if table.isUnicode():
                    table.cmap.update({0x30: "A", 0x301: "A", 0xE000: "A"})
            font.save(self.font)
        face = inspect_font(self.font)["faces"][0]
        self.assertEqual(face["unicode"], {"codepoint_count": 5, "scripts": ["Hani", "Latn"]})
        with TTFont(self.font) as font:
            for table in font["cmap"].tables:
                if table.isUnicode():
                    table.cmap = {0x30: "A", 0x301: "A", 0xE000: "A"}
            font.save(self.font)
        face = inspect_font(self.font)["faces"][0]
        self.assertEqual(face["unicode"], {"codepoint_count": 3})


if __name__ == "__main__":
    unittest.main()
