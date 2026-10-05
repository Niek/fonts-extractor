"""Read concise font metadata without rewriting fonts or loading their outlines."""

from datetime import datetime, timedelta, timezone
from io import BytesIO

from fontTools import unicodedata
from fontTools.misc.macRes import ResourceReader
from fontTools.ttLib import TTFont
from fontTools.ttLib.sfnt import readTTCHeader


NAME_IDS = {
    0: "copyright", 5: "version", 6: "postscript_name",
    8: "manufacturer", 9: "designer", 11: "vendor_url", 14: "license_url",
}


def utc_timestamp(value):
    # OpenType timestamps count seconds from 1904, not the Unix epoch.
    if not value:
        return None
    return (datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=value)).isoformat().replace("+00:00", "Z")


def omit_empty(value):
    """Omit absent optional metadata, keeping meaningful zeroes and false flags."""
    if isinstance(value, dict):
        cleaned = {key: omit_empty(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item is not None and item != [] and item != {} and item != ""}
    return value


def face_metadata(font, index):
    result = {"index": index, "metadata_errors": []}
    # A damaged/unsupported table must not hide the remaining metadata or faces.
    def read(tag, extract):
        if tag in font:
            try:
                extract(font[tag])
            except Exception as error:
                result["metadata_errors"].append({"table": tag, "error": f"{type(error).__name__}: {error}"})

    outline_formats = {"glyf": "TrueType", "CFF ": "CFF", "CFF2": "CFF2", "HVGL": "HVF", "hvgl": "HVF"}
    result["outline_format"] = next((label for tag, label in outline_formats.items() if tag in font), None)
    result["is_color"] = any(tag in font for tag in ("COLR", "CBDT", "sbix", "SVG "))
    result["is_variable"] = "fvar" in font

    def names(table):
        result["family"] = table.getBestFamilyName()
        result["default_style"] = {"name": table.getBestSubFamilyName()}
        result.update({label: table.getDebugName(name_id) for name_id, label in NAME_IDS.items()})

    read("name", names)

    def head(table):
        result.update({
            "units_per_em": table.unitsPerEm,
            "created_utc": utc_timestamp(table.created), "modified_utc": utc_timestamp(table.modified),
        })

    read("head", head)
    read("maxp", lambda t: result.update(glyph_count=t.numGlyphs))

    def os2(table):
        vendor = table.achVendID
        # Some legacy fonts contain non-ASCII vendor bytes (e.g. Pilgiche).
        # Latin-1 is reversible; retain hex as well instead of losing bytes.
        result["vendor_id"] = vendor.decode("latin-1") if isinstance(vendor, bytes) else vendor
        if isinstance(vendor, bytes):
            result["vendor_id_hex"] = vendor.hex()
        result.setdefault("default_style", {}).update({
            "weight_class": table.usWeightClass, "width_class": table.usWidthClass,
            "italic": bool(table.fsSelection & 1), "oblique": bool(table.fsSelection & (1 << 9)),
        })

    read("OS/2", os2)
    read("post", lambda t: result.update(is_monospace=bool(t.isFixedPitch), italic_angle=t.italicAngle))

    def cmap(table):
        codepoints = set().union(*(t.cmap.keys() for t in table.tables if t.isUnicode()))
        result["unicode"] = {
            "codepoint_count": len(codepoints),
            "scripts": sorted({unicodedata.script(cp) for cp in codepoints}),
        }

    read("cmap", cmap)

    def meta(table):
        languages = {
            tag: sorted({value.strip() for value in table.data.get(tag, "").split(",") if value.strip()})
            for tag in ("dlng", "slng")
        }
        result["language_metadata"] = {}
        if "dlng" in table.data:
            result["language_metadata"]["design_languages"] = languages["dlng"]
        if "slng" in table.data:
            result["language_metadata"]["supported_language_count"] = len(languages["slng"])

    read("meta", meta)
    features = set()

    def layout(table):
        feature_list = getattr(table.table, "FeatureList", None)
        if feature_list:
            features.update(record.FeatureTag for record in feature_list.FeatureRecord)

    for tag in ("GSUB", "GPOS"):
        read(tag, layout)
    result["opentype_features"] = sorted(features)

    def name(name_id):
        return font["name"].getDebugName(name_id) if "name" in font else None

    def variations(table):
        result["variation_axes"] = [omit_empty({
            "tag": axis.axisTag, "name": name(axis.axisNameID),
            "minimum": axis.minValue, "default": axis.defaultValue, "maximum": axis.maxValue,
        }) for axis in table.axes]
        result["named_instance_count"] = len(table.instances)

    read("fvar", variations)
    result["metadata_status"] = "partial" if result["metadata_errors"] else "complete"
    return omit_empty(result)


def inspect_font(path):
    result = {"faces": [], "metadata_errors": []}
    try:
        resources = None
        with path.open("rb") as stream:
            collection = stream.read(4) == b"ttcf"
            if path.suffix.lower() == ".dfont":
                resources = ResourceReader(stream).get("sfnt", [])
                if not resources:
                    raise ValueError("No sfnt font resources in dfont")
                count = len(resources)
            else:
                count = readTTCHeader(stream).numFonts if collection else 1
        result["container_format"] = "dfont" if resources is not None else "collection" if collection else "sfnt"
        result["is_collection"] = collection or resources is not None
        result["face_count"] = count
        for index in range(count):
            try:
                # Own the stream so a constructor failure also closes the file.
                with (BytesIO(resources[index].data) if resources is not None else path.open("rb")) as stream, TTFont(stream, fontNumber=index if collection else -1, lazy=True, recalcTimestamp=False) as font:
                    face = face_metadata(font, index)
            except Exception as error:
                face = {"index": index, "metadata_status": "unreadable", "metadata_errors": [{"error": f"{type(error).__name__}: {error}"}]}
            if resources is not None:
                face.update(resource_id=resources[index].id, resource_name=resources[index].name)
            result["faces"].append(face)
        statuses = {face["metadata_status"] for face in result["faces"]}
        result["metadata_status"] = "complete" if statuses == {"complete"} else "partial"
        if statuses == {"unreadable"}:
            result["metadata_status"] = "unreadable"
    except Exception as error:
        result.update(metadata_status="unreadable", metadata_errors=[{"error": f"{type(error).__name__}: {error}"}])
    return result
