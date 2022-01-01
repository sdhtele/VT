<p align="center">
    🍃✂️ <a href="https://github.com/WVDUMP/vinetrimmer">Vinetrimmer</a>
    <br/>
    <sup><em>Widevine DRM downloader and decrypter.</em></sup>
</p>

## Thanks to wvleaks for this leak 
 
## Message to Motion Pictures Association

- We have mailed you the personal details of the developers of this
- Discord Chat Dumps has been mailed
- Links of all discord Chat groups added
- twitter details of the private repo vinetrimmer has been send
- WE just want that you patch all these sites

## NEED CDM ?

- 🎉 Need L1 CDM FOR AMZN ,NF,DSNP mail us on wvfuck@protonmail.com,CDM is not free paid only

<p align="center">
    <img src="https://open.vscode.dev/badges/open-in-vscode.svg" alt="Open in Visual Studio Code"/>
    </a>
    <img src="https://img.shields.io/badge/license-GPL--3.0-orange" alt="GPL-3.0 License">
    </a>
    <img src="https://github.com/WVDUMP/vinetrimmer/actions/workflows/build.yml/badge.svg" alt="Build status">
    </a>
    <a href="https://python.org">
        <img src="https://img.shields.io/badge/python-3.7%2B-informational" alt="Python version">
    </a>
    <img src="https://deepsource.io/gh/WVDUMP/vinetrimmer.svg/?label=active+issues" alt="DeepSource">
    </a>
</p>

## Features

- 🎉 20 Services Supported and counting
- 🧩 Modularised class-based OOP services
- 🗃️ Local and Remote SQL-based Key Vault database
- ⚙️ TOML app and user secrets configuration
- 📦 Local .wvd and Remote API-based Widevine Device support
- 🛠️ Multiple CLI commands and sub-commands
- 👥 Multi-profile support per-service with credentials and/or cookies
- ❤️ Supports Netscape format cookies and `username:password` format credentials
- 🤘 Forever FOSS!

## To-do

There's various TODO's all around the project's code as comments as well as in the doc-strings of the root functions
found in [vinetrimmer.py](vinetrimmer/vinetrimmer.py), feel free to check them if you want something to do.

## Installation

### Requirements

Install the following dependencies in the listed order. Ensure shaka-packager is added to the environment path.

1. [python], 3.7.0 or newer
2. [pip], v19.0 or newer - Python package management
3. [poetry], latest recommended - Python dependency management
4. [shaka-packager], latest recommended - Battle-tested encryption suite created by Google

  [python]: <https://python.org>
  [pip]: <https://pip.pypa.io/en/stable/installing>
  [poetry]: <https://python-poetry.org/docs/#installation>
  [shaka-packager]: <https://github.com/google/shaka-packager/releases/latest>

The following are optional, but will most likely be used:

- [MKVToolNix], v54 or newer for Muxing, Demuxing, and Remuxing.
  Required if not using --no-mux
- [FFMPEG], latest recommended for Repacking, Remuxing, and Identifying streams.
  Required if stream requires a repack, e.g. Disney+
- [CCExtractor], latest recommended for EIA (CEA) Closed Captions extraction (might only be doing CEA 608 and assumes Field 1 Channel 1).
  Required if a c608 box exists (e.g. iTunes) or ffprobe can find a CEA 608 track embedded in the video bitstream (e.g. CTV).
- [NodeJS], v12 or newer for Netflix web-data JS Object to JSON conversion.
  Required if using Netflix

  [MKVToolNix]: <https://mkvtoolnix.download/downloads.html>
  [FFMPEG]: <https://fmpeg.org>
  [CCExtractor]: <https://github.com/CCExtractor/ccextractor>
  [NodeJS]: <https://nodejs.org>

Ensure any dependency that has no installer (e.g. portable .exe files) are stored somewhere and added to environment PATH.

### Steps

1. `poetry config virtualenvs.in-project true` (optional but recommended)
2. `poetry install`
3. You now have a `.venv` folder in your project root directory. Python and dependencies are installed here.
4. You now also have `vt` shim executable installed in the virtual-env.
   Example usage method: `poetry shell` then `vt -h` or `poetry run vt -h`
5. For more ways to use `vt` or the virtual-env, follow [Poetry Docs: Using your virtual environment].
   You could even add the `.venv\Scripts` to your environment path to have `vt` available on any terminal.

  [Poetry Docs: Using your virtual environment]: <https://python-poetry.org/docs/basic-usage/#using-your-virtual-environment>

**Important:** Do not run [Pip as Admin]. pip should not even be used with vinetrimmer, ever, unless you wish to directly install to
the system Python installation with `pip install .`.

  [Pip as Admin]: <https://WVDUMP.github.io/VSGAN/pip-as-admin>

## Usage

The first step to configuring and using vinetrimmer is setting up the data available for use.

See the [Data-directory](#data-directory-data) structure for the majority of data preparation. Then look at the
`vinetrimmer.toml` file to configure the application settings and profile credentials.

Vinetrimmer currently has three commands: `dl`, `cfg` and `prv`. `dl` allows you to download titles. `cfg` allows you to
configure your vinetrimmer setup. `prv` allows you to provision Widevine keyboxes.

For more usage information, see `vt -h` or e.g. `vt dl -h` for help on each command.

## Config directory

The config directory is where the main configuration for vinetrimmer and each service is stored.
It is usually at `%LOCALAPPDATA%\vinetrimmer` on Windows, `~/Library/Preferences/vinetrimmer` on macOS,
and `~/.config/vinetrimmer` on Linux. You can do `vt dl -h` to see the exact path for your current platform.
Example config files are available in the `example_configs` directory in the repo.

### Structure:

-   vinetrimmer.toml
-   Services/ ¬
    -   _service_name_.toml (e.g. `DisneyPlus.toml`)


## Data directory

The data directory is where various data for use is stored, e.g. [Profiles](#profiles), [Cookies](#cookies), and
[.wvd WideVineDevices](#widevine-device-wvd-files). It is usually at `%LOCALAPPDATA%\vinetrimmer` on Windows,
`~/Library/Application Support/vinetrimmer` on macOS, and `~/.local/share/vinetrimmer` on Linux. You can do
`vt dl -h` to see the exact path for your current platform.

### Structure:

-   [/Cookies/](#cookies) ¬
    -   /_service name_/ ¬ (e.g. `/Amazon/`)
        -   [_john_doe_.txt](#profiles)
        -   [_jane_doe_.txt](#profiles)
-   /WVDs/ ¬
    -   [_device_1_l3_.wvd](#widevine-device-wvd-files)
    -   [_device_2_l1_.wvd](#widevine-device-wvd-files)

## Profiles

A Profile is simply a filename moniker that will be used to identify a Cookie or Credential file per service.

Profile files are unique per service folder, i.e. two services can use the same `john.txt` cookie and/or credential
file.

You can specify which profile (or profiles) to use on each service in the main configuration file under `[profiles]`.
When defining multiple profiles per service like the Amazon example, you choose which one to use with `-z` or let it
ask you when running.

## Widevine Device (.wvd) files

This is the device key data in Struct format that is needed for the CDM (Content Decryption Module).

A good idea would be to name the file with respect to the device it's from as well as state its security level.
For example, `nexus_6_l3.wvd`. The files must be using `.wvd` (\_W_ide_V_ine_D_evice) as the file extension.

To make a WVD file is super simple! Use the available helper scripts at [/scripts/WVD/](/scripts/WVD), or take a look
by manually creating one by using the LocalDevice class object below.

```py
from pathlib import Path
from vinetrimmer.utils.Widevine.device import LocalDevice

device = LocalDevice(
    type=LocalDevice.Types.CHROME,
    security_level=3,
    flags={"send_key_control_none": False},  # example flags only, check struct in LocalDevice() to see flags
    private_key=b"...",
    client_id=b"...",
    vmp=b"..."  # or None if you don't have (or need) one
)
# print(device)  # print it out (as python Device object)
# print(device.dumps())  # print it out as bytes
device.dump(Path("C:/Users/john/Documents/chromecdm903_l3.wvd"))  # dump it to a file
```

## Cookies

Cookies must be in the standard Netscape cookies file format. 

Recommended extensions:

- "[Export Cookies](https://addons.mozilla.org/addon/export-cookies-txt)" by `Rotem Dan`
- "[Get cookies.txt](https://chrome.google.com/webstore/detail/bgaddhkoddajcdgocldbbfleckgcbcid)" by `Rahul Shaw`

Any other extension that exports to the standard Netscape format should theoretically work.

## Credentials

Credentials' sole purpose is to provide the service with a Username (or Email) and Password that would be used to
log in and obtain required cookies and/or tokens automatically. Ideally, Services should only ever need either a
Credential, or a Cookies file (not both). However, this isn't always the case.

Credentials are stored in the `vinetrimmer.toml` file of the config directory. See the `[credentials]` section
in `vinetrimmer.example.toml` for information on the format and usage.

Tip: The key/profile name used in the `[credentials]` section must match the filename used for cookies
     if you wish to provide both Credentials and Cookies to a service.


