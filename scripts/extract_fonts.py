"""Collect fonts with provenance and create matching ZIP/JSON release assets."""

import argparse
import fnmatch
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zipfile import ZipFile, ZIP_DEFLATED

import fontTools

from font_metadata import inspect_font


PINGFANG_URL = "https://updates.cdn-apple.com/2025/mobileassets/072-04650/9FABB3FE-D4A6-4563-8CCE-20B5E692A0C9/com_apple_MobileAsset_Font8/3e99191bcfad76a1b28ab8410e037db50a26d721.zip"
PINGFANG_SHA256 = "468483656d9f597807ec9b3927ac6ad318984620139a38c58578846958d91b85"
ROBOTO_RELEASE = "https://api.github.com/repos/googlefonts/roboto-3-classic/releases/latest"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(url, destination, expected_sha256=None):
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "fonts-extractor"})
            token = os.environ.get("GITHUB_TOKEN")
            parsed_url = urlsplit(url)
            if token and parsed_url.scheme == "https" and parsed_url.hostname == "api.github.com":
                # urllib copies regular headers to redirects, including other hosts.
                request.add_unredirected_header("Authorization", f"Bearer {token}")
            with urlopen(request, timeout=120) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
                content_length = response.headers.get("Content-Length")
                if content_length is not None and output.tell() < int(content_length):
                    # Bounded reads may return EOF without raising IncompleteRead.
                    raise IncompleteRead(b"", int(content_length) - output.tell())
                resolved_url = response.geturl()
            break
        except HTTPError as error:
            if error.code not in (408, 429) and not 500 <= error.code < 600:
                raise
            if attempt == 3:
                raise
            error.close()
        except (URLError, TimeoutError, ConnectionError, IncompleteRead):
            if attempt == 3:
                raise
        time.sleep(2 ** attempt)
    digest = sha256(destination)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {url}: expected {expected_sha256}, got {digest}")
    return {"type": "web", "url": url, "resolved_url": resolved_url, "download_sha256": digest}


class Bundle:
    def __init__(self, name, staging):
        self.name = name
        self.directory = staging / name
        self.directory.mkdir()
        self.sources = {}
        self._names = {}

    def _destination(self, name):
        # Apply the same last-source-wins rule on every runner filesystem.
        # Remove the old entry first so case-insensitive disks retain the new spelling.
        previous = self._names.get(name.casefold())
        if previous is not None and previous != name:
            (self.directory / previous).unlink()
            del self.sources[previous]
        self._names[name.casefold()] = name
        return self.directory / name

    def copy(self, path, repository=None, revision=None):
        path = path.absolute()
        source = {"type": "local", "path": str(path), "directory": str(path.parent)}
        if repository is not None:
            source.update(type="repository", repository_path=path.relative_to(repository.absolute()).as_posix(), revision=revision)
        shutil.copyfile(path, self._destination(path.name))
        # As in the original workflow, later sources replace duplicate basenames.
        self.sources[path.name] = source

    def copy_tree(self, directory, suffixes=None):
        if not directory.is_dir():
            raise FileNotFoundError(f"Font source directory does not exist: {directory}")
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and (suffixes is None or path.suffix.lower() in suffixes):
                self.copy(path)

    def download_font(self, url, name):
        self.sources[name] = download(url, self._destination(name))

    def extract_archive(self, archive, source, pattern):
        with ZipFile(archive) as zip_file:
            members = sorted((m for m in zip_file.infolist() if not m.is_dir() and fnmatch.fnmatchcase(m.filename, pattern)), key=lambda m: m.filename)
            if not members:
                raise ValueError(f"No archive members matched {pattern!r} in {archive.name}")
            for member in members:
                name = PurePosixPath(member.filename).name
                # Flatten paths just like unzip -j, without extracting archive paths.
                if name in ("", ".", "..") or "\\" in name:
                    raise ValueError(f"Invalid font archive member: {member.filename}")
                with zip_file.open(member) as stream, self._destination(name).open("wb") as output:
                    shutil.copyfileobj(stream, output)
                self.sources[name] = {**source, "archive_member": member.filename}

    def write(self, output):
        if not self.sources:
            raise ValueError(f"Refusing to publish an empty bundle: {self.name}")
        output.mkdir(parents=True, exist_ok=True)
        files = []
        archive_path = output / f"{self.name}.zip"
        with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for name, source in sorted(self.sources.items()):
                path = self.directory / name
                archive_member = f"{self.name}/{name}"
                entry = {
                    "path": archive_member, "relative_path": name, "source": source,
                    "size_bytes": path.stat().st_size, "sha256": sha256(path),
                    **inspect_font(path),
                }
                files.append(entry)
                if entry["metadata_status"] != "complete":
                    print(f"Metadata {entry['metadata_status']}: {archive_member}", flush=True)
                archive.write(path, archive_member)
        manifest = {
            "schema_version": 2, "bundle": self.name,
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "generator": {"fonttools_version": fontTools.__version__, "commit": os.environ.get("GITHUB_SHA")},
            "runner": {"system": platform.system(), "release": platform.release(), "machine": platform.machine(), "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion")},
            "archive": {"filename": archive_path.name, "size_bytes": archive_path.stat().st_size, "sha256": sha256(archive_path)},
            "file_count": len(files), "face_count": sum(f.get("face_count", 0) for f in files),
            "files_with_metadata_errors": sum(f["metadata_status"] != "complete" for f in files),
            "files": files,
        }
        manifest_path = output / f"{self.name}.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Created {archive_path.name} and {manifest_path.name}: {manifest['file_count']} files, {manifest['face_count']} faces, {manifest['files_with_metadata_errors']} files with metadata errors", flush=True)
        return manifest


def collect_macos(staging):
    bundle = Bundle("fonts-mac", staging)
    bundle.copy_tree(Path("/System/Library/Fonts"))
    resources = Path("/System/Library/PrivateFrameworks/FontServices.framework/Versions/A/Resources")
    # Reserved/PingFangUI.ttc has native HVF outlines that Fontconfig cannot load.
    bundle.copy_tree(resources / "Fonts/ApplicationSupport")
    bundle.copy_tree(resources / "Fonts/Subsets")
    # Portable PingFang 21.0d1e1, all four regional families and six weights.
    archive = staging / "PingFang.zip"
    source = download(PINGFANG_URL, archive, PINGFANG_SHA256)
    bundle.extract_archive(archive, source, "AssetData/PingFang.ttc")
    return [bundle]


def collect_android(staging):
    bundle = Bundle("fonts-android", staging)
    bundle.download_font("https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/fonts/NotoColorEmoji.ttf", "NotoColorEmoji.ttf")
    bundle.download_font("https://github.com/googlefonts/googlefontsdirectory-old/raw/refs/heads/main/apache/droidsansmono/DroidSansMono.ttf", "DroidSansMono.ttf")
    release_path = staging / "roboto-release.json"
    download(ROBOTO_RELEASE, release_path)
    release = json.loads(release_path.read_text(encoding="utf-8"))
    assets = [a for a in release["assets"] if a["name"].lower().endswith(".zip")]
    if len(assets) != 1:
        raise ValueError("Expected exactly one Roboto ZIP release asset")
    archive = staging / "roboto.zip"
    source = download(assets[0]["browser_download_url"], archive)
    source.update(release_api_url=ROBOTO_RELEASE, release_tag=release["tag_name"])
    bundle.extract_archive(archive, source, "android/static/*")
    return [bundle]


def copy_required(bundle, repository, revision, pattern):
    paths = sorted(path for path in repository.glob(pattern) if path.is_file())
    if not paths:
        raise FileNotFoundError(f"No repository fonts matched {pattern}")
    for path in paths:
        bundle.copy(path, repository, revision)


def collect_windows(staging, repository, windows_fonts):
    revision = subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()
    win = Bundle("fonts-win", staging)
    win.copy_tree(windows_fonts, {".ttf", ".ttc"})
    for pattern in ("w10_basic/holomdl2.ttf", "w10_basic/Sitka*"):
        copy_required(win, repository, revision, pattern)
    office = Bundle("fonts-office", staging)
    basic, extended = repository / "w10_basic", repository / "w10_office_extended"
    if not basic.is_dir() or not extended.is_dir():
        raise FileNotFoundError("Missing w10_basic or w10_office_extended repository directory")
    for path in sorted(extended.rglob("*")):
        if path.is_file() and not (basic / path.relative_to(extended)).exists():
            office.copy(path, repository, revision)
    win11 = Bundle("fonts-win11", staging)
    for pattern in ("w11/SitkaVF*", "w11/SegUIVar.ttf", "w11/SegoeIcons.ttf", "w11/seguiemj.ttf"):
        copy_required(win11, repository, revision, pattern)
    return [win, office, win11]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("macos", "android", "windows"))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--repo", type=Path, default=Path("repo"))
    parser.add_argument("--windows-fonts", type=Path, default=Path("C:/Windows/Fonts"))
    args = parser.parse_args()
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory(prefix="fonts-extractor-") as temporary:
        staging = Path(temporary)
        if args.platform == "macos":
            bundles = collect_macos(staging)
        elif args.platform == "android":
            bundles = collect_android(staging)
        else:
            bundles = collect_windows(staging, args.repo, args.windows_fonts)
        for bundle in bundles:
            bundle.write(args.output)


if __name__ == "__main__":
    main()
