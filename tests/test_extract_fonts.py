import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError
from zipfile import ZipFile

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont, newTable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from extract_fonts import Bundle, collect_windows, download, sha256
from font_metadata import inspect_font, utc_timestamp


def make_font(path, family="Fixture", variable=False):
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A", "uni4E2D"])
    builder.setupCharacterMap({0x41: "A", 0x4E2D: "uni4E2D"})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in [".notdef", "A", "uni4E2D"]})
    builder.setupHorizontalMetrics({name: (500, 0) for name in [".notdef", "A", "uni4E2D"]})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({
        "familyName": family, "styleName": "Medium", "psName": f"{family}-Medium",
        "fullName": f"{family} Medium", "copyright": "© 2024–2026 Fixture",
        "version": "Version 1.250", "licenseDescription": "Fixture license",
    })
    builder.setupOS2(usWeightClass=500, usWidthClass=5, achVendID="TEST")
    builder.setupPost()
    builder.font.recalcTimestamp = False
    builder.font["head"].created = 3867548400  # 2026-07-22 07:00:00 UTC
    builder.font["head"].modified = 3867599445  # 2026-07-22 21:10:45 UTC
    builder.font["meta"] = newTable("meta")
    builder.font["meta"].data = {"dlng": "zh-Hans", "slng": "zh-Hans, en"}
    builder.font["name"].setName("测试", 1, 3, 1, 0x804)
    addOpenTypeFeaturesFromString(builder.font, "languagesystem DFLT dflt; feature salt { sub A by uni4E2D; } salt;")
    if variable:
        builder.setupFvar([("wght", 100, 500, 900, "Weight")], [{"location": {"wght": 500}, "stylename": "Medium"}])
        builder.setupStat([{"tag": "wght", "name": "Weight", "values": [{"value": 500, "name": "Medium"}]}])
    builder.save(path)


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.font = self.root / "fixture.ttf"
        make_font(self.font)

    def test_metadata_and_embedded_dates(self):
        original = self.font.read_bytes()
        metadata = inspect_font(self.font)
        self.assertEqual(metadata["metadata_status"], "complete")
        self.assertEqual(metadata["face_count"], 1)
        face = metadata["faces"][0]
        self.assertEqual(face["family"], "Fixture")
        self.assertEqual(face["postscript_name"], "Fixture-Medium")
        self.assertEqual(face["default_style"]["weight_class"], 500)
        self.assertEqual(face["vendor_id"], "TEST")
        self.assertEqual(face["copyright"], "© 2024–2026 Fixture")
        self.assertEqual(face["created_utc"], "2026-07-22T07:00:00Z")
        self.assertEqual(face["modified_utc"], "2026-07-22T21:10:45Z")
        self.assertEqual(face["glyph_count"], 3)
        self.assertEqual(face["units_per_em"], 1000)
        self.assertEqual(face["language_metadata"]["design_languages"], ["zh-Hans"])
        self.assertEqual(face["unicode"]["codepoint_count"], 2)
        self.assertEqual(face["unicode"]["scripts"], ["Hani", "Latn"])
        self.assertIn("salt", face["opentype_features"])
        self.assertEqual(original, self.font.read_bytes())
        self.assertIsNone(utc_timestamp(0))

    def test_summary_omits_inventories_but_preserves_useful_counts(self):
        with TTFont(self.font) as font:
            font["meta"].data["slng"] = "en, zh-Hans, en, "
            font.save(self.font)
        face = inspect_font(self.font)["faces"][0]
        for key in ("tables", "names", "name_languages", "os2", "layout", "stat", "post", "bounds", "horizontal_metrics", "vertical_metrics", "created_seconds_since_1904", "modified_seconds_since_1904"):
            self.assertNotIn(key, face)
        self.assertEqual(set(face["unicode"]), {"codepoint_count", "scripts"})
        self.assertEqual(face["language_metadata"], {"design_languages": ["zh-Hans"], "supported_language_count": 2})
        self.assertEqual(face["outline_format"], "TrueType")
        self.assertFalse(face["is_color"])
        self.assertFalse(face["is_monospace"])
        self.assertEqual(face["italic_angle"], 0)
        self.assertNotIn("designer", face)
        self.assertNotIn("metadata_errors", face)

    def test_absent_language_declarations_do_not_imply_zero_support(self):
        with TTFont(self.font) as font:
            font["meta"].data = {"dlng": "zh-Hans"}
            font.save(self.font)
        face = inspect_font(self.font)["faces"][0]
        self.assertEqual(face["language_metadata"], {"design_languages": ["zh-Hans"]})

    def test_collection_includes_every_face(self):
        second = self.root / "second.ttf"
        make_font(second, "Second")
        collection_path = self.root / "faces.ttc"
        with TTFont(self.font) as first, TTFont(second) as other:
            collection = TTCollection()
            collection.fonts = [first, other]
            collection.save(collection_path)
        metadata = inspect_font(collection_path)
        self.assertTrue(metadata["is_collection"])
        self.assertEqual(metadata["face_count"], 2)
        self.assertEqual([f["family"] for f in metadata["faces"]], ["Fixture", "Second"])
        self.assertEqual([f["index"] for f in metadata["faces"]], [0, 1])
        self.assertEqual(metadata["metadata_status"], "complete")
        # A broken second face must not hide the readable first face.
        data = bytearray(collection_path.read_bytes())
        struct.pack_into(">I", data, 16, len(data) + 100)
        collection_path.write_bytes(data)
        metadata = inspect_font(collection_path)
        self.assertEqual(metadata["metadata_status"], "partial")
        self.assertEqual(metadata["faces"][0]["family"], "Fixture")
        self.assertEqual(metadata["faces"][1]["metadata_status"], "unreadable")

    def test_variable_metadata(self):
        make_font(self.font, variable=True)
        face = inspect_font(self.font)["faces"][0]
        self.assertEqual(face["metadata_status"], "complete", face.get("metadata_errors", []))
        self.assertTrue(face["is_variable"])
        self.assertEqual(face["variation_axes"][0], {"tag": "wght", "name": "Weight", "minimum": 100, "default": 500, "maximum": 900})
        self.assertEqual(face["named_instance_count"], 1)
        self.assertNotIn("named_instances", face)
        self.assertNotIn("stat", face)

    def test_dfont_includes_every_sfnt_resource(self):
        font_bytes = self.font.read_bytes()
        block = struct.pack(">I", len(font_bytes)) + font_bytes
        resource_data = block + block
        references = b"".join(
            struct.pack(">hHI4s", 128 + index, 0xFFFF, index * len(block), b"\0" * 4)
            for index in range(2)
        )
        type_list = struct.pack(">H4sHH", 0, b"sfnt", 1, 10) + references
        map_length = 28 + len(type_list)
        header = struct.pack(">IIII", 256, 256 + len(resource_data), len(resource_data), map_length)
        resource_map = header + b"\0" * 8 + struct.pack(">HH", 28, map_length) + type_list
        path = self.root / "fixture.dfont"
        path.write_bytes(header + b"\0" * 240 + resource_data + resource_map)
        metadata = inspect_font(path)
        self.assertEqual(metadata["metadata_status"], "complete", metadata)
        self.assertEqual(metadata["container_format"], "dfont")
        self.assertEqual(metadata["face_count"], 2)
        self.assertEqual([f["resource_id"] for f in metadata["faces"]], [128, 129])
        self.assertEqual([f["family"] for f in metadata["faces"]], ["Fixture", "Fixture"])

    def test_non_ascii_vendor_id_is_losslessly_json_serializable(self):
        with TTFont(self.font, lazy=True) as font:
            offset = font.reader.tables["OS/2"].offset
        data = bytearray(self.font.read_bytes())
        data[offset + 58:offset + 62] = b" \xc1\x80 "
        self.font.write_bytes(data)
        face = json.loads(json.dumps(inspect_font(self.font)))["faces"][0]
        self.assertEqual(face["metadata_status"], "complete")
        self.assertEqual(face["vendor_id_hex"], "20c18020")
        self.assertEqual(face["vendor_id"].encode("latin-1"), b" \xc1\x80 ")

    def test_broken_optional_table_preserves_other_metadata(self):
        with TTFont(self.font, lazy=True) as font:
            offset = font.reader.tables["post"].offset
        data = bytearray(self.font.read_bytes())
        struct.pack_into(">I", data, offset, 0x00070000)
        self.font.write_bytes(data)
        metadata = inspect_font(self.font)
        self.assertEqual(metadata["metadata_status"], "partial")
        face = metadata["faces"][0]
        self.assertEqual(face["family"], "Fixture")
        self.assertEqual(face["glyph_count"], 3)
        self.assertTrue(any(e["table"] == "post" for e in face.get("metadata_errors", [])))

    def test_manifest_matches_zip_bytes_and_keeps_unreadable_files(self):
        bundle = Bundle("fonts-test", self.root)
        unicode_font = self.root / "测试 font.ttf"
        unicode_font.write_bytes(self.font.read_bytes())
        bad_font = self.root / "bad.ttf"
        bad_font.write_bytes(b"not a font")
        bundle.copy(unicode_font)
        bundle.copy(bad_font)
        output = self.root / "dist"
        manifest = bundle.write(output)
        self.assertEqual(manifest, json.loads((output / "fonts-test.json").read_text(encoding="utf-8")))
        self.assertEqual(manifest["file_count"], 2)
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["files_with_metadata_errors"], 1)
        self.assertEqual(manifest["archive"]["sha256"], sha256(output / "fonts-test.zip"))
        with ZipFile(output / "fonts-test.zip") as archive:
            self.assertEqual(set(archive.namelist()), {f["path"] for f in manifest["files"]})
            for entry in manifest["files"]:
                data = archive.read(entry["path"])
                self.assertEqual(entry["size_bytes"], len(data))
                self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(entry["source"]["directory"], str(self.root))
                self.assertEqual(entry["source"]["path"], str(self.root / entry["relative_path"]))

    def test_duplicate_basename_tracks_the_final_source(self):
        other = self.root / "other"
        other.mkdir()
        replacement = other / self.font.name
        make_font(replacement, "Replacement")
        bundle = Bundle("fonts-test", self.root)
        bundle.copy(self.font)
        bundle.copy(replacement)
        manifest = bundle.write(self.root / "dist")
        self.assertEqual(manifest["file_count"], 1)
        entry = manifest["files"][0]
        self.assertEqual(entry["source"]["path"], str(replacement))
        self.assertEqual(entry["sha256"], sha256(replacement))
        self.assertEqual(entry["faces"][0]["family"], "Replacement")

    def test_archive_member_provenance_and_selection(self):
        archive = self.root / "download.zip"
        with ZipFile(archive, "w") as zip_file:
            zip_file.write(self.font, "android/static/Fixture.ttf")
            zip_file.writestr("android/static/", b"")
            zip_file.writestr("other/unwanted.txt", b"unwanted")
        source = {"type": "web", "url": "https://example.test/release.zip", "resolved_url": "https://example.test/release.zip", "download_sha256": sha256(archive)}
        bundle = Bundle("fonts-test", self.root)
        bundle.extract_archive(archive, source, "android/static/*")
        manifest = bundle.write(self.root / "dist")
        self.assertEqual(manifest["file_count"], 1)
        entry = manifest["files"][0]
        self.assertEqual(entry["source"], {**source, "archive_member": "android/static/Fixture.ttf"})
        self.assertEqual(entry["sha256"], sha256(self.font))
        with self.assertRaises(ValueError):
            bundle.extract_archive(archive, source, "missing/*")

    def test_download_tracks_redirects_and_validates_checksum(self):
        class Response(io.BytesIO):
            headers = {}

            def geturl(self):
                return "https://cdn.example.test/font.ttf"
        data = self.font.read_bytes()
        target = self.root / "download.ttf"
        with patch("extract_fonts.urlopen", return_value=Response(data)):
            source = download("https://example.test/font.ttf", target, hashlib.sha256(data).hexdigest())
        self.assertEqual(source["url"], "https://example.test/font.ttf")
        self.assertEqual(source["resolved_url"], "https://cdn.example.test/font.ttf")
        self.assertEqual(source["download_sha256"], sha256(target))
        with patch("extract_fonts.urlopen", return_value=Response(data)), self.assertRaises(ValueError):
            download("https://example.test/font.ttf", target, "0" * 64)
        with patch("extract_fonts.urlopen", side_effect=[URLError("temporary"), Response(data)]) as request, patch("extract_fonts.time.sleep"):
            download("https://example.test/font.ttf", target)
            self.assertEqual(request.call_count, 2)

    def test_windows_selection_and_repository_provenance(self):
        repo = self.root / "repo"
        windows = self.root / "system"
        windows.mkdir()
        (windows / "System.TTF").write_bytes(self.font.read_bytes())
        (windows / "ignored.txt").write_text("ignore", encoding="utf-8")
        for relative in ("w10_basic/holomdl2.ttf", "w10_basic/Sitka.ttc", "w10_basic/Shared.ttf", "w10_office_extended/Shared.ttf", "w10_office_extended/Office Font.ttf", "w11/SitkaVF.ttf", "w11/SegUIVar.ttf", "w11/SegoeIcons.ttf", "w11/seguiemj.ttf"):
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.font.read_bytes())
        with patch("extract_fonts.subprocess.check_output", return_value="abc123\n"):
            win, office, win11 = collect_windows(self.root, repo, windows)
        self.assertEqual(set(win.sources), {"System.TTF", "holomdl2.ttf", "Sitka.ttc"})
        self.assertEqual(set(office.sources), {"Office Font.ttf"})
        self.assertEqual(len(win11.sources), 4)
        self.assertEqual(win.sources["holomdl2.ttf"]["repository_path"], "w10_basic/holomdl2.ttf")
        self.assertEqual(win.sources["holomdl2.ttf"]["revision"], "abc123")
        self.assertEqual(win.sources["System.TTF"]["type"], "local")

    def test_empty_bundle_is_rejected(self):
        bundle = Bundle("fonts-test", self.root)
        with self.assertRaises(ValueError):
            bundle.write(self.root / "dist")


if __name__ == "__main__":
    unittest.main()
