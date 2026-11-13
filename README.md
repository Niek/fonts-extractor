# fonts-extractor

This repo extracts the fonts from Windows and macOS runners and uploads those as a release. The fonts can be used in various projects.

The Android bundle includes Roboto, Droid Sans Mono and emoji, plus Android 15's
Noto Serif (four styles), Cutive Mono and variable Dancing Script from pinned AOSP
commits. The six added font files and their six license/provenance text files are
SHA-256 verified after base64 decoding. The manifest retains the source URLs
(including commits), encoded download hashes and decoded file hashes. Text files
remain in the ZIP and are marked as unreadable font metadata, consistently with
the existing manifest contract. See [extraction and manifests](.github/workflows/extract.md).

Tag a new version:

```bash
git tag v2.5
git push origin v2.5
```
