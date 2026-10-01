# fonts-extractor

This repo extracts the fonts from Windows and macOS runners and uploads those as a release. The fonts can be used in various projects.

The macOS job uses GitHub's `xcode-27` image (macOS 27). It exports the native
PingFang SC/TC/HK/MO faces through CoreText and builds one `PingFang.ttc` with six
weights per family. Regional character maps and shaping tables are retained.
The previous Apple CDN download is commented out in the workflow.

PingFang's native `hvgl` outlines are converted to standard quadratic TrueType
outlines at 16,000 units per em, limiting coordinate and advance rounding to
1/32,000 em. This preserves the runner's font version, but does not guarantee
pixel-identical rendering across CoreText and FreeType. CI checks every glyph's
advance and bounds against CoreText, reports pixel differences for text samples,
and checks all 24 faces with Fontconfig, FreeType and HarfBuzz on Linux. Vertical
centering corrections use the standard `vkrn` feature; the vertical HarfBuzz
checks explicitly enable it. The native
comparison report is available in the `pingfang-verification` workflow artifact.

Tag a new version:

```bash
git tag v2.5
git push origin v2.5
```
