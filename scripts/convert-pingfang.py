"""Export native PingFang as one portable collection, retaining quadratic outlines."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile

from fontTools.fontBuilder import FontBuilder
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.misc.roundTools import otRound
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont, newTable
from fontTools.ttLib.scaleUpem import scale_upem

FAMILIES = ("SC", "TC", "HK", "MO")
WEIGHTS = ("Ultralight", "Thin", "Light", "Regular", "Medium", "Semibold")
UPEM = 16000  # 16x native precision; coordinates must still fit signed 16-bit.
REMOVE = ("hvgl", "fvar", "avar", "HVAR", "VVAR", "MVAR", "STAT", "DSIG", "cidg", "VORG")


def convert(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pingfang-") as temporary:
        temporary = Path(temporary)
        exporter = temporary / "export"
        subprocess.run(["swiftc", "-O", str(Path(__file__).with_name("export-pingfang.swift")),
                        "-o", str(exporter)], check=True)
        paths = []
        for weight_number, weight in enumerate(WEIGHTS, 1):
            process = subprocess.Popen([str(exporter), f"PingFangSC-{weight}"],
                                       stdout=subprocess.PIPE, text=True)
            with process.stdout as stream:
                meta = json.loads(next(stream))
                source = TTCollection(meta["source"], lazy=True)
                indices = {family: next(i for i, font in enumerate(source.fonts)
                                       if font["name"].getDebugName(6) == f"PingFang{family}-Medium")
                           for family in FAMILIES}
                template = source.fonts[indices["SC"]]
                order = template.getGlyphOrder()
                assert len(order) == meta["glyphs"]
                # These four public faces share geometry and variation metrics.
                # Fail on a future format change instead of exporting the wrong outlines.
                for family in FAMILIES:
                    font = source.fonts[indices[family]]
                    for tag in ("hvgl", "hmtx", "HVAR", "vmtx", "VORG", "fvar", "avar"):
                        assert font.getTableData(tag) == template.getTableData(tag), (family, tag)
                    assert getattr(font["GSUB"].table, "FeatureVariations", None) is None
                factor = UPEM / meta["upem"]
                glyphs, horizontal, vertical, vertical_offsets = {}, {}, {}, {}
                max_error = 0
                for expected, line in enumerate(stream):
                    row = json.loads(line)
                    assert row["gid"] == expected
                    pen = TTGlyphPen(None)
                    for command in row["path"]:
                        op = int(command[0])
                        points = [(command[i] * factor, command[i + 1] * factor)
                                  for i in range(1, len(command), 2)]
                        if op == 0:
                            pen.moveTo(points[0])
                        elif op == 1:
                            pen.lineTo(points[0])
                        elif op == 2:
                            pen.qCurveTo(*points)
                        elif op == 4:
                            pen.closePath()
                        else:
                            raise ValueError(f"Unexpected path operation: {op}")
                    glyph = pen.glyph()
                    glyph.recalcBounds(None)
                    name = order[expected]
                    glyphs[name] = glyph
                    advance = otRound(row["advance"] * factor)
                    max_error = max(max_error, abs(advance / factor - row["advance"]))
                    horizontal[name] = (advance, getattr(glyph, "xMin", 0))
                    # Native vertical centering uses the default master's width,
                    # even when the named weight has a different horizontal advance.
                    vertical_offsets[name] = otRound(row["verticalOrigin"][0] * factor + advance / 2)
                    # CoreText reports the translation applied to horizontal outlines.
                    origin_y = -row["verticalOrigin"][1] * factor
                    vertical[name] = (otRound(row["verticalAdvance"] * factor),
                                      otRound(origin_y - getattr(glyph, "yMax", 0)))
                assert process.wait() == 0
                assert len(glyphs) == meta["glyphs"]
                assert max_error <= 0.5 / factor + 1e-9
                source.close()

            for family in FAMILIES:
                font = TTFont(meta["source"], fontNumber=indices[family], recalcTimestamp=False)
                # Retain each region's cmap and GSUB, addressing the same glyph IDs.
                font.setGlyphOrder(order)
                for tag in REMOVE:
                    if tag in font:
                        del font[tag]
                font["glyf"] = newTable("glyf")
                font["glyf"].glyphOrder = order
                font["glyf"].glyphs = {}
                scale_upem(font, UPEM)
                for data in (font["trak"].horizData, font["trak"].vertData):
                    for entry in data.values():
                        for size in entry:
                            entry[size] = otRound(entry[size] * factor)
                builder = FontBuilder(font=font)
                builder.setupGlyf(glyphs)
                builder.setupHorizontalMetrics(horizontal)
                builder.setupVerticalMetrics(vertical)
                builder.setupMaxp()
                assert "GPOS" not in font
                adjustments = "\n".join(f"pos {name} <{dx} 0 0 0>;"
                                        for name, dx in vertical_offsets.items() if dx)
                addOpenTypeFeaturesFromString(font, "languagesystem DFLT dflt;\nfeature vkrn {\n" +
                                               adjustments + "\n} vkrn;", tables=["GPOS"])
                font["head"].glyphDataFormat = 0
                font["head"].macStyle = 0
                font["OS/2"].usWeightClass = weight_number * 100
                font["OS/2"].fsSelection = 0x40 if weight == "Regular" else 0
                name = f"PingFang{family}-{weight}"
                names = {1: f"PingFang {family}", 2: weight, 3: f"{name};{meta['version']};CoreText",
                         4: f"PingFang {family} {weight}", 6: name, 16: f"PingFang {family}", 17: weight}
                for record in font["name"].names:
                    if record.nameID in names:
                        record.string = names[record.nameID].encode(record.getEncoding())
                for name_id, value in names.items():
                    font["name"].setName(value, name_id, 3, 1, 0x409)
                path = temporary / f"{name}.ttf"
                builder.save(path)
                font.close()
                paths.append(path)
            print(f"{meta['version']} {weight}: {len(glyphs)} glyphs; "
                  f"max advance rounding at 16px: {max_error * .016:.6f}px", flush=True)

        collection = TTCollection()
        collection.fonts = [TTFont(path, lazy=True, recalcTimestamp=False) for path in paths]
        collection.save(output, shareTables=True)
        collection.close()
        print(f"Saved {output}: 24 faces, {output.stat().st_size:,} bytes", flush=True)


if __name__ == "__main__":
    convert(Path(sys.argv[1]))
