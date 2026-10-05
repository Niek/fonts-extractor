# Font extraction and manifests

Extract fonts from Windows and macOS runners, download Android fonts, and publish ZIP archives with matching JSON manifests.

For release tagging instructions, see the [README](../../README.md).

Each workflow run uploads all ten files as separate, unzipped artifacts named after the file; tag builds also attach them directly to the release:

| Bundle | Archive | Manifest |
| --- | --- | --- |
| macOS | `fonts-mac.zip` | `fonts-mac.json` |
| Windows | `fonts-win.zip` | `fonts-win.json` |
| Windows 11 | `fonts-win11.zip` | `fonts-win11.json` |
| Office | `fonts-office.zip` | `fonts-office.json` |
| Android | `fonts-android.zip` | `fonts-android.json` |

The ZIPs retain the existing `fonts-<bundle>/<filename>` layout. The JSON is a separate release asset and is not included inside the font ZIP.

## Manifest format

Each schema-version-2 manifest includes the archive's size and SHA-256, generation time, runner information, fontTools version, file/face counts, and a `files` array. Every archived file has exactly one entry:

- `path`: exact path inside the ZIP, also its local path after extraction (for example, `fonts-mac/PingFang.ttc`). `relative_path` omits the bundle directory.
- `source`: `local`, `repository`, or `web`. Local files record their original absolute path and exact parent directory. Repository files additionally record the path within the supplementary repository and its checked-out commit. Downloads record the requested URL, final redirected URL, and download SHA-256. Fonts extracted from downloads also record the original `archive_member`; the download hash identifies the source ZIP, while the file's `sha256` identifies the extracted font. Roboto includes its resolved release tag.
- `size_bytes` and `sha256`: measured from the unchanged font bytes included in the ZIP.
- `is_collection`, `face_count`, and `faces`: TTC/OTC and macOS `.dfont` files contain one metadata object per face, with its zero-based `index`. The `container_format` distinguishes resource containers, SFNT collections, and standalone fonts; `.dfont` faces also include resource IDs and names. A standalone font has one face at index 0. File sizes and hashes apply to the complete file, not individual collection faces.
- `metadata_status` and `metadata_errors`: unsupported files and damaged tables remain in the ZIP and manifest with explicit errors. A face/file can be `complete`, `partial`, or `unreadable`; completeness refers to the summary fields, not validation of every font table. Empty face-level errors and absent optional values are omitted.

Each face is a self-contained summary:

- Preferred family and style, numeric weight/width classes, italic/oblique flags, PostScript name, and version.
- Vendor ID, manufacturer/designer, copyright, vendor URL, and license URL when present. Non-ASCII vendor bytes are represented losslessly with Latin-1 plus `vendor_id_hex`.
- Embedded creation/modification dates in UTC, glyph count, units per em, outline format, monospace/color/variable flags, and italic angle.
- Unicode character count and distinct ISO 15924 script tags, rather than individual codepoints or ranges. Common (`Zyyy`), Inherited (`Zinh`), and Unknown (`Zzzz`) tags are omitted from the script list; their characters still contribute to the character count.
- Declared design languages (`meta.dlng`, such as `zh-Hans`) and the count of distinct declared supported languages (`meta.slng`). An absent declaration is omitted, not reported as zero.
- Distinct OpenType feature tags merged from GSUB and GPOS. Variable fonts include their axes with min/default/max values and the named-instance count.

The summary omits raw table inventories, localized name records and duplicate name variants, Unicode ranges, cmap records, OS/2 bitfields and PANOSE, detailed metrics, layout script/language records, full supported-language lists, named-instance records, STAT records, and long descriptive/license text. These remain available in the original font files. Shared values stay on each face so consumers can read a face without resolving references or inherited metadata. JSON stays indented and readable.

Embedded dates are decoded by fontTools (including its legacy timestamp corrections), not filesystem dates. Language declarations are claims from the font, not verified language coverage; script tags summarize the Unicode cmap. Glyph counts describe this version only; comparisons with older releases require comparing manifests. Legacy Type 1 resource files are retained with an explicit metadata error.

Schema 2 replaces the earlier detailed schema 1: file identity, provenance, hashes, and face indexing are preserved, while face metadata now uses the summaries above.

Collection preserves flattened filenames and source precedence: a later source replaces a duplicate basename, comparing names case-insensitively with `casefold()` on every OS. The last source's spelling, bytes, and provenance are used consistently for local copies, downloads, and archive members. A missing required source, failed download/checksum, or empty bundle fails the build.

Downloads retry connection failures, incomplete bodies, HTTP 408/429, and server errors up to three times; other HTTP errors fail immediately. When `GITHUB_TOKEN` is set, only HTTPS requests to `api.github.com` receive it, and the authorization header is not forwarded on redirects. CI supplies it to the Android download step. ZIPs use DEFLATE level 9, and the CLI suppresses fontTools warning chatter while retaining metadata error summaries. Unit tests run on Ubuntu and Windows; macOS runs extraction without repeating the suite.

## Run locally

Requires Python 3.11 or newer; CI uses Python 3.13.

```bash
python -m venv .venv
# Activate .venv for your shell, then:
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python scripts/extract_fonts.py macos --output dist
python scripts/extract_fonts.py android --output dist
python scripts/extract_fonts.py windows --repo repo --output dist
```

Run `macos` or `windows` on its corresponding OS; Android downloads work on either. Windows requires the supplementary repository checked out at `repo` (the workflow uses `REPO` and `PAC` secrets). `--windows-fonts` can override `C:/Windows/Fonts`.
