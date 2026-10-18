#!/usr/bin/env python3
"""Download a public Google Drive file or a direct HTTP(S) URL safely.

The resulting file is validated as a safetensors file before the build continues.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests


def drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"drive.google.com", "docs.google.com"}:
        return None
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    return query_id if query_id and re.fullmatch(r"[A-Za-z0-9_-]+", query_id) else None


def download_drive(session: requests.Session, file_id: str, destination: Path) -> None:
    endpoint = "https://drive.google.com/uc"
    response = session.get(endpoint, params={"export": "download", "id": file_id}, stream=True, timeout=(30, 180))
    response.raise_for_status()

    token = None
    for cookie_name, cookie_value in response.cookies.items():
        if cookie_name.startswith("download_warning"):
            token = cookie_value
            break

    if not token:
        # Google sometimes renders a confirmation page instead of setting a cookie.
        prefix = response.raw.read(1024 * 1024, decode_content=True)
        match = re.search(rb"[?&]confirm=([0-9A-Za-z_-]+)", prefix)
        if match:
            token = match.group(1).decode("ascii")
        response.close()
        if token:
            response = session.get(
                endpoint,
                params={"export": "download", "id": file_id, "confirm": token},
                stream=True,
                timeout=(30, 180),
            )
            response.raise_for_status()
        else:
            # If the first response was already the file, save the consumed prefix too.
            response = session.get(endpoint, params={"export": "download", "id": file_id}, stream=True, timeout=(30, 180))
            response.raise_for_status()

    with destination.open("wb") as output:
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if chunk:
                output.write(chunk)
    response.close()


def download_direct(session: requests.Session, url: str, destination: Path) -> None:
    with session.get(url, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    output.write(chunk)


def validate_safetensors(path: Path) -> None:
    size = path.stat().st_size
    if size < 16:
        raise ValueError(f"Downloaded file is too small to be a safetensors file ({size} bytes)")
    with path.open("rb") as stream:
        header_size = int.from_bytes(stream.read(8), "little", signed=False)
        if header_size < 2 or header_size > min(size - 8, 100 * 1024 * 1024):
            raise ValueError("Downloaded content does not have a valid safetensors header; check the Drive permissions/link.")
        header = stream.read(header_size)
    try:
        metadata = json.loads(header)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Downloaded content is not a valid safetensors header (possibly a Google Drive HTML page).") from exc
    if not isinstance(metadata, dict) or "__metadata__" not in metadata and not metadata:
        raise ValueError("Downloaded safetensors header is empty or malformed.")


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: download_drive_file.py <share-or-direct-url> <destination>", file=sys.stderr)
        return 2
    url, output_arg = sys.argv[1], sys.argv[2]
    if not url.startswith(("https://", "http://")):
        print("Adapter URL must start with HTTP or HTTPS.", file=sys.stderr)
        return 2

    destination = Path(output_arg)
    destination.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "AGCN-Live-Voice-build/1.0"})
    try:
        file_id = drive_file_id(url)
        with tempfile.TemporaryDirectory() as temp_dir:
            temporary = Path(temp_dir) / destination.name
            if file_id:
                download_drive(session, file_id, temporary)
            else:
                download_direct(session, url, temporary)
            validate_safetensors(temporary)
            shutil.move(str(temporary), str(destination))
        print(f"Validated safetensors file: {destination} ({destination.stat().st_size} bytes)")
        return 0
    except Exception as exc:
        destination.unlink(missing_ok=True)
        print(f"Adapter download/validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
