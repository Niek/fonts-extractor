"""Convert the runner's native PingFang into a portable, 24-face TTC."""

from pathlib import Path
import sys
import tempfile

import CoreText as CT
import Quartz as CG
import objc
from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTCollection, TTFont, newTable
from fontTools.ttLib.scaleUpem import scale_upem

FAMILIES = ("SC", "TC", "HK", "MO")
WEIGHTS = ("Ultralight", "Thin", "Light", "Regular", "Medium", "Semibold")
UPEM = 16000  # Preserve fractional outlines and advances at 16x native precision.


def draw(pen, element):
    if element.type == CG.kCGPathElementMoveToPoint:
        pen.moveTo(tuple(element.points[0]))
    elif element.type == CG.kCGPathElementAddLineToPoint:
        pen.lineTo(tuple(element.points[0]))
    elif element.type == CG.kCGPathElementAddQuadCurveToPoint:
        pen.qCurveTo(tuple(element.points[0]), tuple(element.points[1]))
    elif element.type == CG.kCGPathElementCloseSubpath:
        pen.closePath()
    else:
        raise ValueError("Expected quadratic PingFang outlines")


native = CT.CTFontCreateWithName("PingFangSC-Medium", UPEM, None)
source = CT.CTFontCopyAttribute(native, CT.kCTFontURLAttribute).path()
assert source.endswith("/Reserved/PingFangUI.ttc")
original = TTCollection(source, lazy=True)
indices = {family: next(i for i, font in enumerate(original.fonts)
                       if font["name"].getDebugName(6) == f"PingFang{family}-Medium")
           for family in FAMILIES}
template = original.fonts[indices["SC"]]
order = template.getGlyphOrder()
factor = UPEM / template["head"].unitsPerEm
version = template["name"].getDebugName(5)
# The regional faces share outlines; keep their individual cmap and GSUB tables.
for index in indices.values():
    font = original.fonts[index]
    for tag in ("hvgl", "hmtx", "HVAR", "vmtx", "VORG", "fvar", "avar"):
        assert font.getTableData(tag) == template.getTableData(tag), tag
    assert getattr(font["GSUB"].table, "FeatureVariations", None) is None
original.close()

output = Path(sys.argv[1])
output.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix="pingfang-") as temporary:
    paths = []
    for weight_number, weight in enumerate(WEIGHTS, 1):
        name = f"PingFangSC-{weight}"
        native = CT.CTFontCreateWithName(name, UPEM, None)
        assert CT.CTFontCopyPostScriptName(native) == name
        native = CT.CTFontCreateCopyWithAttributes(native, UPEM, None,
            CT.CTFontDescriptorCreateWithAttributes({CT.kCTFontOpticalSizeAttribute: "none"}))
        assert CT.CTFontGetGlyphCount(native) == len(order)
        glyphs, horizontal, vertical, adjustments = {}, {}, {}, []
        for gid, glyph_name in enumerate(order):
            with objc.autorelease_pool():
                pen = TTGlyphPen(None)
                path = CT.CTFontCreatePathForGlyph(native, gid, None)
                if path is not None:
                    CG.CGPathApply(path, pen, draw)
                glyph = pen.glyph()
                glyph.recalcBounds(None)
                glyphs[glyph_name] = glyph
                advance = otRound(CT.CTFontGetAdvancesForGlyphs(native, CT.kCTFontOrientationHorizontal, [gid], None, 1)[0])
                height = otRound(CT.CTFontGetAdvancesForGlyphs(native, CT.kCTFontOrientationVertical, [gid], None, 1)[0])
                origin = CT.CTFontGetVerticalTranslationsForGlyphs(native, [gid], None, 1)[0]
                horizontal[glyph_name] = (advance, getattr(glyph, "xMin", 0))
                vertical[glyph_name] = (height, otRound(-origin.height - getattr(glyph, "yMax", 0)))
                # Preserve native vertical centering when a weight changes the advance.
                dx = otRound(origin.width + advance / 2)
                if dx:
                    adjustments.append(f"pos {glyph_name} <{dx} 0 0 0>;")

        for family in FAMILIES:
            font = TTFont(source, fontNumber=indices[family], recalcTimestamp=False)
            font.setGlyphOrder(order)
            for tag in ("hvgl", "fvar", "avar", "HVAR", "VVAR", "MVAR", "STAT", "DSIG", "cidg", "VORG"):
                if tag in font:
                    del font[tag]
            font["glyf"] = newTable("glyf")
            font["glyf"].glyphOrder, font["glyf"].glyphs = order, {}
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
            addOpenTypeFeaturesFromString(font,
                "languagesystem DFLT dflt; feature vkrn {" + "\n".join(adjustments) + "} vkrn;", tables=["GPOS"])
            font["head"].glyphDataFormat = font["head"].macStyle = 0
            font["OS/2"].usWeightClass = weight_number * 100
            font["OS/2"].fsSelection = 0x40 if weight == "Regular" else 0
            name = f"PingFang{family}-{weight}"
            names = {1: f"PingFang {family}", 2: weight, 3: f"{name};{version};CoreText",
                     4: f"PingFang {family} {weight}", 6: name, 16: f"PingFang {family}", 17: weight}
            for record in font["name"].names:
                if record.nameID in names:
                    record.string = names[record.nameID].encode(record.getEncoding())
            for name_id, value in names.items():
                font["name"].setName(value, name_id, 3, 1, 0x409)
            path = Path(temporary) / f"{name}.ttf"
            builder.save(path)
            font.close()
            paths.append(path)
        print(f"Converted PingFang {version} {weight}", flush=True)

    collection = TTCollection()
    collection.fonts = [TTFont(path, lazy=True, recalcTimestamp=False) for path in paths]
    collection.save(output, shareTables=True)
    collection.close()
    print(f"Saved {output}: 24 faces, {output.stat().st_size:,} bytes")
