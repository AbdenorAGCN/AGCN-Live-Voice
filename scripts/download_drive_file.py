#!/usr/bin/env python3
"""Download a public Google Drive file or a direct HTTP(S) URL safely.

Google Drive sometimes serves a confirmation form instead of the file. This
helper follows that form, then validates the resulting safetensors header.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests


class DownloadFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[tuple[str, dict[str, str]]] = []
        self._action: str | None = None
        self._fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag.lower() == "form":
            self._action = values.get("action", "")
            self._fields = {}
        elif tag.lower() == "input" and self._action is not None:
            name = values.get("name")
            if name:
                self._fields[name] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._action is not None:
            self.forms.append((self._action, dict(self._fields)))
            self._action = None
            self._fields = {}


def drive_file_id(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"drive.google.com", "docs.google.com"}:
        return None
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    return query_id if query_id and re.fullmatch(r"[A-Za-z0-9_-]+", query_id) else None


def save_response(response: requests.Response, destination: Path) -> None:
    response.raise_for_status()
    with destination.open("wb") as output:
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if chunk:
                output.write(chunk)


def response_looks_html(response: requests.Response, prefix: bytes) -> bool:
    content_type = response.headers.get("Content-Type", "").lower()
    return "text/html" in content_type or prefix.lstrip().lower().startswith(
        (b"<!doctype html", b"<html", b"<form")
    )


def download_drive(session: requests.Session, file_id: str, destination: Path) -> None:
    endpoint = "https://drive.google.com/uc"
    response = session.get(
        endpoint,
        params={"export": "download", "id": file_id},
        stream=True,
        timeout=(30, 180),
    )
    response.raise_for_status()
    first = next(response.iter_content(chunk_size=1024 * 1024), b"")

    if not response_looks_html(response, first):
        with destination.open("wb") as output:
            if first:
                output.write(first)
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                if chunk:
                    output.write(chunk)
        response.close()
        return

    # The response is a Drive confirmation page; read only the small HTML page.
    chunks = [first]
    total = len(first)
    for chunk in response.iter_content(chunk_size=256 * 1024):
        chunks.append(chunk)
        total += len(chunk)
        if total >= 4 * 1024 * 1024:
            break
    html = b"".join(chunks).decode("utf-8", errors="replace")
    response.close()

    parser = DownloadFormParser()
    parser.feed(html)
    if not parser.forms:
        raise ValueError(
            "Google Drive returned an HTML page instead of the model file. "
            "Verify that General access is 'Anyone with the link' and that the link opens without signing in."
        )

    # Prefer the form that carries a file id and download/export fields.
    forms = sorted(
        parser.forms,
        key=lambda form: (
            0 if form[1].get("id") == file_id else 1,
            0 if "download" in form[0].lower() else 1,
        ),
    )
    action, fields = forms[0]
    if "id" not in fields:
        fields["id"] = file_id
    if "export" not in fields:
        fields["export"] = "download"
    action_url = urljoin("https://drive.google.com", action)
    response = session.get(
        action_url,
        params=fields,
        stream=True,
        timeout=(30, 180),
    )
    response.raise_for_status()
    first = next(response.iter_content(chunk_size=1024 * 1024), b"")
    if response_looks_html(response, first):
        response.close()
        raise ValueError(
            "Google Drive still returned a confirmation/login page. "
            "Verify sharing permissions and that the file is downloadable without signing in."
        )
    with destination.open("wb") as output:
        if first:
            output.write(first)
        for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
            if chunk:
                output.write(chunk)
    response.close()


def download_direct(session: requests.Session, url: str, destination: Path) -> None:
    with session.get(url, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        first = next(response.iter_content(chunk_size=1024 * 1024), b"")
        if response_looks_html(response, first):
            raise ValueError("The supplied direct URL returned HTML instead of the model file.")
        with destination.open("wb") as output:
            if first:
                output.write(first)
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
            with path.open("rb") as retry:
                preview = retry.read(160).decode("utf-8", errors="replace")
            raise ValueError(
                "Downloaded content does not have a valid safetensors header. "
                f"File size: {size} bytes; preview: {preview!r}"
            )
        header = stream.read(header_size)
    try:
        metadata = json.loads(header)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Downloaded content is not a valid safetensors header.") from exc
    if not isinstance(metadata, dict) or not metadata:
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
