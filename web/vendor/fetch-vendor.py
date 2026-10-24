"""Fetch pinned, unmodified upstream browser assets and retain provenance/licenses.

The build uses checked-in assets; this maintenance script is never needed by visitors.
Only explicit archive members are read in memory. No archive paths are extracted.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
MAX_DOWNLOAD = 16 * 1024 * 1024
entries: list[dict] = []


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "DroneMap-vendor-maintenance/1.0"}), timeout=60) as response:
        data = response.read(MAX_DOWNLOAD + 1)
    if not data or len(data) > MAX_DOWNLOAD:
        raise RuntimeError(f"Empty or oversized upstream file: {url}")
    return data


def save(name: str, data: bytes, *, version: str, package: str, source: str, license_name: str) -> None:
    destination = ROOT / name
    if destination.parent != ROOT or destination.name != name:
        raise RuntimeError("Only flat, explicit vendor filenames are supported")
    destination.write_bytes(data)
    entries.append({"file": name, "package": package, "version": version, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "source": source, "license": license_name})


def npm(package: str, version: str, members: dict[str, str], license_name: str) -> None:
    metadata_url = f"https://registry.npmjs.org/{package}/{version}"
    metadata = json.loads(fetch(metadata_url))
    if metadata["version"] != version or metadata["name"] != package:
        raise RuntimeError("Unexpected npm metadata")
    archive_url = metadata["dist"]["tarball"]
    archive = fetch(archive_url)
    # npm metadata uses SHA-512 integrity; verify it before trusting archive content.
    algorithm, encoded = metadata["dist"]["integrity"].split("-", 1)
    if algorithm != "sha512" or base64.b64encode(hashlib.sha512(archive).digest()).decode() != encoded:
        raise RuntimeError(f"Upstream npm integrity mismatch for {package}")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member_name, output_name in members.items():
            member = tar.getmember(member_name)
            if not member.isfile() or member.size > MAX_DOWNLOAD:
                raise RuntimeError(f"Unexpected archive member {member_name}")
            save(output_name, tar.extractfile(member).read(), version=version, package=package, source=f"{archive_url}#{member_name}", license_name=license_name)


def main() -> None:
    tailwind_version = "3.4.17"
    tailwind_url = f"https://cdn.tailwindcss.com/{tailwind_version}"
    tailwind = fetch(tailwind_url)
    if not tailwind.startswith(b"(()=>") and b"tailwind" not in tailwind[:500]:
        raise RuntimeError("Unexpected Tailwind browser runtime response")
    save("tailwind-3.4.17.js", tailwind, version=tailwind_version, package="tailwindcss", source=tailwind_url, license_name="Tailwind-LICENSE.txt")
    license_url = f"https://raw.githubusercontent.com/tailwindlabs/tailwindcss/v{tailwind_version}/LICENSE"
    save("Tailwind-LICENSE.txt", fetch(license_url), version=tailwind_version, package="tailwindcss", source=license_url, license_name="Tailwind-LICENSE.txt")
    npm("@turf/turf", "6.5.0", {"package/turf.min.js": "turf-6.5.0.min.js", "package/LICENSE": "Turf-LICENSE.txt"}, "Turf-LICENSE.txt")
    npm("leaflet-control-geocoder", "4.0.0", {"package/dist/Control.Geocoder.js": "Control.Geocoder-4.0.0.js", "package/dist/Control.Geocoder.js.map": "Control.Geocoder.js.map", "package/dist/Control.Geocoder.css": "Control.Geocoder-4.0.0.css", "package/LICENSE": "Geocoder-LICENSE.txt"}, "Geocoder-LICENSE.txt")
    manifest = {"schema": 1, "note": "Pinned upstream assets are stored unmodified, with original license notices. The maintenance script is optional; browser startup reads local assets.", "assets": entries}
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"downloaded": [{"file": e["file"], "bytes": e["bytes"], "version": e["version"]} for e in entries]}, indent=2))


if __name__ == "__main__":
    main()
