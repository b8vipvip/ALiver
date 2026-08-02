#!/usr/bin/env python3
"""Vendor a traceable, source-only LiveTalking snapshot into ALiver.

Model weights, avatar datasets, recordings and large media files are deliberately
excluded. The resulting tree is ordinary repository content, not a git submodule.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath
from typing import BinaryIO

DEFAULT_REPOSITORY = "lipku/LiveTalking"
DEFAULT_REF = "main"
DEFAULT_DESTINATION = Path("services/livetalking")
MAX_FILE_BYTES = 2 * 1024 * 1024
EXCLUDED_DIRECTORIES = {
    ".git",
    ".github",
    "models",
    "data/avatars",
    "data/record",
    "__pycache__",
}
EXCLUDED_SUFFIXES = {
    ".pth",
    ".pt",
    ".onnx",
    ".ckpt",
    ".safetensors",
    ".pkl",
    ".mp4",
    ".mov",
    ".avi",
    ".webm",
    ".wav",
    ".mp3",
    ".flac",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".ico",
    ".zip",
    ".gz",
    ".tar",
    ".7z",
}


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ALiver-LiveTalking-vendor",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_url(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _read_json(url: str) -> dict:
    return json.loads(_read_url(url, timeout=60))


def _safe_member(member: tarfile.TarInfo) -> bool:
    path = PurePosixPath(member.name)
    return not path.is_absolute() and ".." not in path.parts


def _extract_archive(archive: bytes, destination: Path) -> Path:
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as handle:
        members = [member for member in handle.getmembers() if _safe_member(member)]
        if len(members) != len(handle.getmembers()):
            raise RuntimeError("Unsafe path found in upstream archive")
        handle.extractall(destination, members=members, filter="data")
    roots = [item for item in destination.iterdir() if item.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("Unexpected LiveTalking archive layout")
    return roots[0]


def _excluded(relative: Path, size: int) -> bool:
    posix = relative.as_posix()
    if any(posix == value or posix.startswith(value + "/") for value in EXCLUDED_DIRECTORIES):
        return True
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return True
    return size > MAX_FILE_BYTES


def vendor(repository: str, ref: str, destination: Path) -> dict:
    commit = _read_json(f"https://api.github.com/repos/{repository}/commits/{ref}")
    commit_sha = str(commit["sha"])
    archive = _read_url(
        f"https://api.github.com/repos/{repository}/tarball/{commit_sha}",
        timeout=240,
    )
    archive_sha256 = hashlib.sha256(archive).hexdigest()

    temporary = destination.parent / ".livetalking-vendor-tmp"
    source = _extract_archive(archive, temporary)
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True)

    copied = 0
    copied_bytes = 0
    for source_path in sorted(source.rglob("*")):
        if not source_path.is_file():
            continue
        relative = source_path.relative_to(source)
        size = source_path.stat().st_size
        if _excluded(relative, size):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
        copied += 1
        copied_bytes += size

    license_path = destination / "LICENSE"
    if not license_path.exists():
        raise RuntimeError("Upstream LICENSE was not included")
    shutil.copy2(license_path, destination / "UPSTREAM_LICENSE")

    metadata = {
        "upstream": f"https://github.com/{repository}",
        "ref": ref,
        "commit": commit_sha,
        "archive_sha256": archive_sha256,
        "copied_files": copied,
        "copied_bytes": copied_bytes,
        "license": "Apache-2.0",
        "excluded_directories": sorted(EXCLUDED_DIRECTORIES),
        "max_file_bytes": MAX_FILE_BYTES,
    }
    (destination / "UPSTREAM.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "UPSTREAM.md").write_text(
        "# Vendored LiveTalking source\n\n"
        f"- Upstream: https://github.com/{repository}\n"
        f"- Ref: `{ref}`\n"
        f"- Commit: `{commit_sha}`\n"
        f"- Copied source files: `{copied}`\n"
        f"- Copied bytes: `{copied_bytes}`\n"
        f"- Archive SHA-256: `{archive_sha256}`\n\n"
        "Model weights, avatar datasets, recordings, large media and binary assets "
        "are intentionally excluded. See `UPSTREAM_LICENSE`.\n",
        encoding="utf-8",
    )
    shutil.rmtree(temporary, ignore_errors=True)
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--ref", default=DEFAULT_REF)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    metadata = vendor(args.repository, args.ref, args.destination)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
