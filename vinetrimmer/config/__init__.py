from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytomlpp
from appdirs import AppDirs
from requests.structures import CaseInsensitiveDict

from vinetrimmer.objects.vaults import Vault


class Config:
    def __init__(self, **kwargs: Any):
        self.arguments = CaseInsensitiveDict(kwargs.get("arguments") or {})
        self.aria2c: dict = kwargs.get("aria2c") or {}
        self.cdm: dict = kwargs.get("cdm") or {}
        self.cdm_api: list[dict] = kwargs.get("cdm_api") or []
        self.credentials: dict = kwargs.get("credentials") or {}
        self.directories: dict = kwargs.get("directories") or {}
        self.headers: dict = kwargs.get("headers") or {}
        self.key_vaults: list[Vault] = [self.load_vault(x) for x in kwargs.get("key_vaults") or []]
        self.nordvpn: dict = kwargs.get("nordvpn") or {}
        self.profiles: dict = kwargs.get("profiles") or {}
        self.proxies: dict = kwargs.get("proxies") or {}
        self.tag: str = kwargs.get("tag") or ""

    @staticmethod
    def load_vault(vault: dict) -> Vault:
        return Vault(**{
            "vault_type" if k == "type" else k: v for k, v in vault.items()
        })

    @classmethod
    def from_toml(cls, path: Path) -> Config:
        if not path.exists():
            raise FileNotFoundError(f"Config file path ({path}) was not found")
        if not path.is_file():
            raise FileNotFoundError(f"Config file path ({path}) is not to a file.")
        return cls(**pytomlpp.load(path))


class Directories:
    def __init__(self) -> None:
        self.app_dirs = AppDirs("vinetrimmer", False)
        self.package_root = Path(__file__).resolve().parent.parent
        self.configuration = self.package_root / "config"
        self.user_configs = Path(self.app_dirs.user_config_dir)
        self.service_configs = self.user_configs / "Services"
        self.data = Path(self.app_dirs.user_data_dir)
        self.downloads = Path.home() / "Downloads" / "vinetrimmer"
        self.temp = Path({
            "/tmp": "/var/tmp"
        }.get(tempfile.gettempdir(), tempfile.gettempdir())) / "vinetrimmer"
        self.cache = Path(self.app_dirs.user_cache_dir)
        self.cookies = self.data / "Cookies"
        self.logs = Path(self.app_dirs.user_log_dir)
        self.wvds = self.data / "WVDs"


class Filenames:
    def __init__(self) -> None:
        self.log = directories.logs / "vinetrimmer_{name}_{time}.log"
        self.config = directories.configuration / "{service}.toml"
        self.root_config: Path = directories.user_configs / "vinetrimmer.toml"
        self.service_config = directories.service_configs / "{service}.toml"
        self.subtitles: Path = directories.temp / "TextTrack_{id}_{language_code}.srt"
        self.chapters: Path = directories.temp / "{filename}_chapters.txt"


directories = Directories()
filenames = Filenames()
config = Config.from_toml(filenames.root_config)
credentials = config.credentials

# This serves two purposes:
# - Allow `range` to be used in the arguments section in the config rather than just `range_`
# - Allow sections like [arguments.Amazon] to work even if an alias (e.g. AMZN or amzn) is used.
#   CaseInsensitiveDict is used for `arguments` above to achieve case insensitivity.
# NOTE: The import cannot be moved to the top of the file, it will cause a circular import error.
from vinetrimmer.services import SERVICE_MAP  # noqa: E402

if "range_" not in config.arguments:
    config.arguments["range_"] = config.arguments.get("range_")
for service, aliases in SERVICE_MAP.items():
    for alias in aliases:
        config.arguments[alias] = config.arguments.get(service)

for directory in ("downloads", "temp"):
    if config.directories.get(directory):
        setattr(directories, directory, Path(config.directories[directory]).expanduser())
