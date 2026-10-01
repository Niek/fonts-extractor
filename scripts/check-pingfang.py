"""Check the generated collection with Fontconfig, FreeType and HarfBuzz on Linux."""

import json
from pathlib import Path
import subprocess
import sys

import freetype
from fontTools.ttLib import TTCollection
import uharfbuzz as hb

path = Path(sys.argv[1])
reference = json.loads(Path(sys.argv[2]).read_text())
collection = TTCollection(path, lazy=True)
assert len(collection.fonts) == len(reference["faces"]) == 24
names = subprocess.check_output(["fc-scan", "--format", "%{postscriptname}\n", str(path)], text=True).splitlines()
assert sorted(names) == sorted(face["name"] for face in reference["faces"])
data = path.read_bytes()
fonts = {}
loaded = 0
for i, font in enumerate(collection.fonts):
    name = font["name"].getDebugName(6)
    assert "glyf" in font and not {"hvgl", "fvar", "HVAR"}.intersection(font.keys())
    expected = next(face for face in reference["faces"] if face["name"] == name)
    assert font["name"].getDebugName(5) == expected["version"]
    face = freetype.Face(str(path), index=i)
    assert face.num_glyphs == expected["glyphs"]
    for gid in range(face.num_glyphs):
        face.load_glyph(gid, freetype.FT_LOAD_NO_SCALE | freetype.FT_LOAD_NO_HINTING |
                        freetype.FT_LOAD_NO_BITMAP | freetype.FT_LOAD_PEDANTIC)
        loaded += 1
    shaped = hb.Font(hb.Face(data, i))
    shaped.scale = (16000, 16000)
    fonts[name] = shaped

max_width_error = 0
for sample in reference["samples"]:
    buffer = hb.Buffer()
    buffer.add_str(sample["text"])
    buffer.guess_segment_properties()
    hb.shape(fonts[sample["name"]], buffer)
    assert [glyph.codepoint for glyph in buffer.glyph_infos] == sample["glyphs"], sample
    advances = [pos.x_advance * sample["size"] / 16000 for pos in buffer.glyph_positions]
    for actual, expected in zip(advances, sample["nativeAdvances"]):
        assert abs(actual - expected) <= sample["size"] / 32000 + 0.00001, sample
    max_width_error = max(max_width_error, abs(sum(advances) - sample["nativeWidth"]))

print(json.dumps({"faces": len(fonts), "freetypeVersion": freetype.version(),
                  "freetypeGlyphsLoaded": loaded, "shapingSamples": len(reference["samples"]),
                  "maxWidthErrorPx": max_width_error,
                  "coreTextPixelIdenticalSamples": sum(s["changedPixels"] == 0 for s in reference["samples"]),
                  "coreTextMaxPixelDelta": max(s["maxPixelDelta"] for s in reference["samples"])}, indent=2))
