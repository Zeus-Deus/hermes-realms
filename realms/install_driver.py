"""Explicit pinned driver install; never changes the user's global cua-driver."""

import argparse
import hashlib
from pathlib import Path
import os
import platform
import tarfile
import tempfile
import urllib.request

VERSION = "0.23.2"
ARCHIVE_SHA256 = "478e010d2b0426de9d8a07eb839802daa92137b33cb8affb93b991e91d76ce7e"
BINARY_SHA256 = "2aaad67b996d41cd909e4b7ac2ddd705653e8c7f9958010ac051838b02f64eeb"
URL = f"https://github.com/trycua/cua/releases/download/cua-driver-rs-v{VERSION}/cua-driver-rs-{VERSION}-linux-x86_64.tar.gz"
MEMBER = f"cua-driver-rs-{VERSION}-linux-x86_64/cua-driver"


def install_archive(archive, target):
    archive, target = Path(archive), Path(target)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Downloaded cua-driver release checksum mismatch")
    if (
        target.is_file()
        and not target.is_symlink()
        and target.stat().st_uid == os.getuid()
        and os.access(target, os.X_OK)
        and hashlib.sha256(target.read_bytes()).hexdigest() == BINARY_SHA256
    ):
        return str(target)
    with tarfile.open(archive, "r:gz") as package:
        member = package.getmember(MEMBER)
        if not member.isfile():
            raise ValueError("Driver archive member is not a regular file")
        stream = package.extractfile(member)
        if stream is None:
            raise ValueError("Missing driver archive payload")
        with stream:
            data = stream.read()
    if hashlib.sha256(data).hexdigest() != BINARY_SHA256:
        raise ValueError("Driver binary checksum mismatch")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".driver-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o700)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if not os.access(target, os.X_OK):
        raise PermissionError(
            "Installed driver is not executable on the target filesystem"
        )
    return str(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="Use an already downloaded pinned archive"
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "vendor/cua-driver",
    )
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        parser.error("This verified binary release is Linux x86_64 only")
    if args.archive:
        print(install_archive(args.archive, args.target))
        return
    with tempfile.TemporaryDirectory(prefix="realms-driver-download-") as directory:
        archive = Path(directory) / "release.tar.gz"
        with (
            urllib.request.urlopen(URL, timeout=60) as response,
            archive.open("wb") as stream,
        ):
            import shutil

            shutil.copyfileobj(response, stream)
        print(install_archive(archive, args.target))


if __name__ == "__main__":
    main()
