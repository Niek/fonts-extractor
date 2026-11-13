import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from test_extract_fonts import make_font
from extract_fonts import ANDROID_AOSP_ASSETS, AOSP_ROOT, ROBOTO_RELEASE, Bundle, collect_android


class AndroidTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        font = self.root / "fixture.ttf"
        make_font(font)
        self.font = font.read_bytes()

    def test_android_bundle_keeps_fonts_licenses_and_download_provenance(self):
        archive = io.BytesIO()
        with ZipFile(archive, "w") as output:
            output.writestr("android/static/Roboto-Regular.ttf", self.font)
            output.writestr("other/not-selected.ttf", self.font)
        release = {"tag_name": "fixture-release", "assets": [{
            "name": "roboto.zip", "browser_download_url": "https://example.test/roboto.zip",
        }]}
        payloads = {}
        assets = []
        for source_path, name, _ in ANDROID_AOSP_ASSETS:
            data = self.font if name.endswith(".ttf") else f"upstream text: {name}\n".encode()
            payloads[f"{AOSP_ROOT}{source_path}?format=TEXT"] = base64.b64encode(data) + b"\n"
            assets.append((source_path, name, hashlib.sha256(data).hexdigest()))

        class Response(io.BytesIO):
            headers = {}

            def geturl(self):
                return "https://example.test/resolved"

        def response(request, timeout):
            url = request.full_url
            if url == ROBOTO_RELEASE:
                return Response(json.dumps(release).encode())
            if url == "https://example.test/roboto.zip":
                return Response(archive.getvalue())
            if url in payloads:
                return Response(payloads[url])
            if url.endswith("/NotoColorEmoji.ttf") or url.endswith("/DroidSansMono.ttf"):
                return Response(self.font)
            self.fail(f"Unexpected download: {url}")

        with patch("extract_fonts.ANDROID_AOSP_ASSETS", assets), patch("extract_fonts.urlopen", side_effect=response):
            [bundle] = collect_android(self.root)
        manifest = bundle.write(self.root / "dist")
        new_fonts = {"NotoSerif-Regular.ttf", "NotoSerif-Bold.ttf", "NotoSerif-Italic.ttf",
                     "NotoSerif-BoldItalic.ttf", "CutiveMono.ttf", "DancingScript-Regular.ttf"}
        text_files = {f"{family}.{kind}.txt" for family in ("NotoSerif", "CutiveMono", "DancingScript")
                      for kind in ("LICENSE", "METADATA")}
        entries = {entry["relative_path"]: entry for entry in manifest["files"]}
        self.assertEqual(set(entries), new_fonts | text_files | {"Roboto-Regular.ttf", "DroidSansMono.ttf", "NotoColorEmoji.ttf"})
        self.assertEqual(manifest["file_count"], 15)
        self.assertEqual(sum(entry["metadata_status"] == "complete" for entry in entries.values()), 9)
        self.assertEqual(manifest["files_with_metadata_errors"], 6)
        self.assertEqual(entries["Roboto-Regular.ttf"]["source"]["release_tag"], "fixture-release")
        with ZipFile(self.root / "dist/fonts-android.zip") as output:
            self.assertEqual(set(output.namelist()), {entry["path"] for entry in entries.values()})
            for name in new_fonts | text_files:
                entry = entries[name]
                source = entry["source"]
                encoded = payloads[source["url"]]
                decoded = base64.b64decode(encoded)
                self.assertEqual(output.read(entry["path"]), decoded)
                self.assertEqual(source["encoding"], "base64")
                self.assertEqual(source["download_sha256"], hashlib.sha256(encoded).hexdigest())
                self.assertEqual(entry["sha256"], hashlib.sha256(decoded).hexdigest())
                self.assertEqual(entry["metadata_status"], "complete" if name in new_fonts else "unreadable")

    def test_corrupt_decoded_asset_cannot_be_published(self):
        class Response(io.BytesIO):
            headers = {}

            def geturl(self):
                return "https://example.test/font?format=TEXT"

        bundle = Bundle("fonts-android", self.root)
        expected = hashlib.sha256(self.font).hexdigest()
        with patch("extract_fonts.urlopen", return_value=Response(base64.b64encode(self.font + b"corrupt"))):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                bundle.download_base64("https://example.test/font?format=TEXT", "font.ttf", expected)
        self.assertEqual(bundle.sources, {})
        with self.assertRaisesRegex(ValueError, "empty bundle"):
            bundle.write(self.root / "dist")
        self.assertFalse((self.root / "dist/fonts-android.zip").exists())
