#!/usr/bin/env python3

from __future__ import annotations

import sys
from hashlib import sha512
from pathlib import Path
from typing import Optional

from vinetrimmer.utils.Widevine.protos.widevine_pb2 import FileHashes
from vinetrimmer.utils.Widevine.vmp import WidevineSignatureReader

"""
Script that generates a VMP blob for chromecdm
"""

WIN32_FILES = [Path(x) for x in [
    "chrome.exe",
    "chrome.dll",
    "chrome_child.dll",
    "widevinecdmadapter.dll",
    "widevinecdm.dll"
]]


def sha512file(filename: Path) -> bytes:
    """Compute SHA-512 digest of file."""
    sha = sha512()
    with filename.open("rb") as f:
        for b in iter(lambda: f.read(0x10000), b''):
            sha.update(b)
    return sha.digest()


def build_vmp_field(filenames: list[tuple[Path, Path, Path]]) -> bytes:
    """
    Create and fill out a FileHashes object.

    `filenames` is an array of pairs of filenames like (file, file_signature)
    such as ("module.dll", "module.dll.sig"). This does not validate the signature
    against the codesign root CA, or even the sha512 hash against the current signature+signer
    """
    file_hashes = FileHashes()

    for basename, file, sig in filenames:
        signature = WidevineSignatureReader.from_file(sig)
        s = file_hashes.signatures.add()
        s.filename = str(basename)
        s.test_signing = False  # we can't check this without parsing signer
        s.SHA512Hash = sha512file(file)
        s.main_exe = signature.mainexe
        s.signature = signature.signature

    file_hashes.signer = signature.signer
    return file_hashes.SerializeToString()


def get_files_with_signatures(
    path: Path, required_files: Optional[list[Path]] = None, random_order: bool = False, sig_ext: str = "sig"
) -> list[tuple[Path, Path, Path]]:
    """
    use on chrome dir (a given version).
    random_order would put any files it found in the dir with sigs,
    it's not the right way to do it and the browser does not do this.
    this function can still fail (generate wrong output) in subtle ways if
    the Chrome dir has copies of the exe/sigs, especially if those copies are modified in some way
    """
    if not required_files:
        required_files = WIN32_FILES
    all_files = path.rglob("*")
    sig_files = path.rglob(f"*.{sig_ext}")

    base_names = []
    for path in sig_files:
        orig_path = Path(path.stem)
        if orig_path not in all_files:
            print("signature file {} lacks original file {}".format(path, orig_path))
        base_names.append(path.name)

    if not set(base_names).issuperset(set(required_files)):
        # or should just make this warn as the next exception would be more specific
        raise ValueError("Missing a binary/signature pair from {}".format(required_files))

    files_to_hash = []
    if random_order:
        for path in sig_files:
            orig_path = Path(path.stem)
            files_to_hash.append((Path(orig_path.name), orig_path, path))
    else:
        for basename in required_files:
            found_file = False
            for path in sig_files:
                orig_path = Path(path.stem)
                if str(orig_path).endswith(str(basename)):
                    files_to_hash.append((basename, orig_path, path))
                    found_file = True
                    break
            if not found_file:
                raise Exception("Failed to locate a file sig/pair for {}".format(basename))

    return files_to_hash


def make_vmp_buff(browser_dir: Path, file_msg_out: Path) -> None:
    file_msg_out.write_bytes(build_vmp_field(get_files_with_signatures(browser_dir)))


if len(sys.argv) < 3:
    print("Usage: {} BrowserPathWithVersion OutputPBMessage.bin".format(sys.argv[0]))
else:
    make_vmp_buff(Path(sys.argv[1]), Path(sys.argv[2]))
