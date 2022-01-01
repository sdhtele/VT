from __future__ import annotations

import itertools
import os
import shutil
import sys
from pathlib import Path

import pytomlpp
from PyInstaller.__main__ import run

if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, SetVersion, StringFileInfo, StringStruct,
                                                     StringTable, VarFileInfo, VarStruct, VSVersionInfo)

"""Load pyproject.toml information."""
project = pytomlpp.load(Path(__file__).resolve().parent / "pyproject.toml")
poetry = project["tool"]["poetry"]

"""Configuration options that may be changed or referenced often."""
DEBUG = False  # When False, removes un-needed data after build has finished
NAME = poetry["name"]
AUTHOR = "rlaphoenix, and contributors"
VERSION = poetry["version"]
ICON_FILE = "assets/temp-icon.ico"  # pass None to use default icon
ONE_FILE = False  # Must be False if using setup.iss
CONSOLE = True  # If build is intended for GUI, set to False
ADDITIONAL_DATA: list[tuple[str, str]] = [
    # (local file path, destination in build output)
]
HIDDEN_IMPORTS: list[str] = []
EXTRA_ARGS = [
    "-y", "--win-private-assemblies", "--win-no-prefer-redirects"
]

"""Prepare environment to ensure output data is fresh."""
shutil.rmtree("build", ignore_errors=True)
shutil.rmtree("dist/vinetrimmer", ignore_errors=True)
# we don't want to use any spec, only the configuration set in this file
Path(f"{NAME}.spec").unlink(missing_ok=True)

"""Run PyInstaller with the provided configuration."""
run([
    "vinetrimmer/vinetrimmer.py",
    "-n", NAME,
    "-i", ["NONE", ICON_FILE][bool(ICON_FILE)],
    ["-D", "-F"][ONE_FILE],
    ["-w", "-c"][CONSOLE],
    *itertools.chain(*[["--add-data", os.pathsep.join(x)] for x in ADDITIONAL_DATA]),
    *itertools.chain(*[["--hidden-import", x] for x in HIDDEN_IMPORTS]),
    *EXTRA_ARGS
])

if sys.platform == "win32":
    """Set Version Info Structure."""
    VERSION_4_TUP = tuple(map(int, f"{VERSION}.0".split(".")))
    VERSION_4_STR = ".".join(map(str, VERSION_4_TUP))
    SetVersion(
        "dist/{0}/{0}.exe".format(NAME),
        VSVersionInfo(
            ffi=FixedFileInfo(
                filevers=VERSION_4_TUP,
                prodvers=VERSION_4_TUP
            ),
            kids=[
                StringFileInfo([StringTable(
                    "040904B0",  # ?
                    [
                        StringStruct("Comments", NAME),
                        StringStruct("CompanyName", AUTHOR),
                        StringStruct("FileDescription", "Widevine DRM downloader and decrypter."),
                        StringStruct("FileVersion", VERSION_4_STR),
                        StringStruct("InternalName", NAME),
                        StringStruct("LegalCopyright", f"Copyright (C) 2019-2021 {AUTHOR}"),
                        StringStruct("OriginalFilename", ""),
                        StringStruct("ProductName", NAME),
                        StringStruct("ProductVersion", VERSION_4_STR)
                    ]
                )]),
                VarFileInfo([VarStruct("Translation", [0, 1200])])  # ?
            ]
        )
    )

if not DEBUG:
    shutil.rmtree("build", ignore_errors=True)
    # we don't want to keep the generated spec
    Path(f"{NAME}.spec").unlink(missing_ok=True)
