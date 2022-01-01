# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.7] - 2021-08-09

### Added
- New Service: Showtime (<https://www.showtime.com>).
- New dependency: `lxml`.
- New Dev dependencies: `types-protobuf`, and `types-PyMySQL`.
- Tracks.from_m3u8: Detect HDR10 using SupplementalProperty values if available.
- Tracks.from_mpds: Detect Dolby Vision by checking for `dvhe` at the start of the codec.
- Tracks.get_pssh: Add support for v4.1.0.0 PlayReady PSSH boxes.
- BaseTrack: Create function `move` allowing one to move the file at `_location` (if available) to another location
  then set the new location as the new `_location`.
- BaseTrack: Create function `swap` allowing one to delete the current file at `_location` (if available) then move
  a different file to `_location`.
- AMZN: When using a non-chrome CDM device, Warn if no device information was available.

### Changed
- Tracks.from_mpds: Replace xmltodict with lxml for a very noticeable speed boost. Anything from Tracks.from_mpds
  that expected a xmltodict result has been updated to expect and work with lxml.
- Tracks.get_pssh: Replace xmltodict with lxml.
- Credential: Username and Password arguments can no longer be None. A string value must be set.
- Credential: Due to the above change, Credential.load will now raise a ValueError instead of returning an empty
  initialized class with no arguments.
- mypy: Incomplete and/or Untyped Definitions are no longer allowed.
- Title: Renamed Title.title_id to Title.id (`id_` in the args list as to not shadow built-in `id`).
- Title: Renamed Title.title_type to Title.type (`type_` in the args list as to not shadow built-in `type`).
- PMTP: Replace xmltodict with lxml.
- AMZN: Replace requests with aria2c when downloading the MPD manifest due to the sheer size of manifests from Amazon.
  They have thousands upon thousands of segment index information making the file size up to 60 MB in some cases.
  Using aria2c's multithreading to download these manifests gives a moderate speed boost.
- NF: Service privacy certificate has been moved to its config.

### Removed
- Removed `xmltodict` dependency.
- Removed unused `cdmapi.py` file in the utils folder. It had its core code reworked as RemoteDevice in `Cdm.py`
  but forgot to be removed once finished.
- NF: Removed unused `android_login()` function that did not work when it was put in, and has no plans to be fixed
  or updated as it isn't needed.

### Fixed
- Tracks.get_pssh: Heavily improve (and outright fix in most scenarios) PlayReady PSSH parsing.
- Tracks.mux: Fix IndexError on the default track check when there's no audio tracks to mux, e.g., with `--subs-only`.
- Tracks.mux: Raise ValueError if the track being muxed is not yet downloaded.
- Tracks.mux: Remove unnecessary mkdir on all available tracks. mkvmerge will create the parent directories of the
  output muxed file if necessary.
- Tracks.`__str__`: Fix count of MenuTrack's after the change in v0.0.3 where MenuTracks were removed from the Tracks
  `__iter__`.
- VideoTrack & AudioTrack `__str__`: Fix bitrate print when bitrate is not specified, or 0.
- Tracks.sort_subtitles: Sort forced subtitles to be at the top of the list, but top of each language group, not top
  of the entire subtitles list.
- Tracks.from_mpds: Prefer the frameRate in the Representation over the AdaptationSet for more accuracy.
- TextTrack.download: Return the save path on the override method to match the signature of the super method.
- TextTrack.get_track_name: Fix type error with flag if flag is `false`.
- Title.Types: Create as a numeric Enum for all type-hinting and mypy checks to correctly work in relation to the
  title.Types class.
- config: Fix type error if no `tag` was specified by the user in the config, or it was a false-y value.
- AMZN: Fix casing of the HDR10 range check for the bitrate mode preparation check.
- AMZN: Tag descriptive audio based on audioTrackSubtype instead of filtering it out entirely.
- HULU: Fix type error of the original_lang value of the Title objects in get_titles.
- CORE: Fix Title id used in get_playlist.

## [0.0.6] - 2021-08-08

### Added
- LocalVault: Support expanding user (`~`) on the vault path.
- VUDU: Cache session key data per-profile. They only last for one hour, but caching can still help.
- VUDU: Get original title language.
- ALL4: Add Android API Key IV token protection values.
- ALL4: Add a list of all(?) available Client name values. These were returned from the API response if you were to
  provide an invalid client name value. (lol)
- Title: Add `original_lang` class attribute. Expects a langcodes Language object. This should be set to the titles
  original language, just like BaseTrack.original_lang.
- constants: Added `none` -> `und` language mapping to LANGUAGE_MUX_MAP.

### Changed
- README: Update information for the latest changes with configs, credentials, data, and commands.
- Set and use `Title.original_lang` instead of `BaseTrack.original_lang` on ALL4, AMZN, ATVP, CRAV, CTV, CORE, DSNP,
  FO, HMAX, HS, HULU, iT, NF, PLAY.
- BaseTrack: Rename `original_lang` attribute to `is_original_lang`. This is to reduce confusion between Title's
  `original_lang`, and BaseTrack's `original_lang` (which is a bool, not a language object).
- ATVP: Use v3 title API to get originalSpeakingLanguages value for titles.
- VUDU: Use browser login for consistency since the rest of the code is browser-based calls.
- VUDU: Endpoints and service privacy certificate has been moved to its config.
- BaseService: All services now only need to provide the click Context (ctx) variable in a super call, instead of
  cookies, credentials, and so on.
- Track get_track_name: Display territory name in English.
- Track `__str__`: Display bitrate in kb/s. However, It still stores bitrate as bytes/sec.

### Fixed
- AMZN, HMAX, HULU, NF, PLAY: Use is_close_match over `startswith()` and `==` when checking language equality.
- ALL4: Using the Android API token protection values over the Browser values, only non-upscaled streams will be
  available.
- ALL4: Strip whitespace from the decrypted token and license API values.
- ALL4: Mark `alternative` role audio tracks as descriptive.
- VUDU: Fix runtime error on titles that do not have a `bestDashVideoQuality` value.
- HMAX: Use Android Device values for `[client]` config as Browser values now have recaptcha on login. It still uses
  Browser values for `[device]` as HEVC fails to return otherwise.
- HMAX: Fix possible KeyError on get_tracks manifest call error handling.
- PMTP: Fix License call `content_id` KeyError.
- PMTP: Fix get_auth_bearer request url join.
- PMTP: Fix missing episode titles from get_titles by adding support for `Promo Full Episode` titles.
- PMTP: Make sure TV show title ID is a number, not a slug.
- DSNP: Fix token expiration check. The file creation time was incorrectly using as nanoseconds in int form.
- DSNP: Fix token refreshing request. It used an invalid grant_type.
- BaseService: Service Log now correctly honors vt root option `--debug` when deciding the log level.
- dl: Vault Caching logs are now more verbose, and more accurate when an error occurs. It no longer always states it
  cached when it might not have.
- Track.from_mpds & Tracks.from_m3u8: Support `lang` argument to be a langcodes Language object.
- Track.from_mpds & Tracks.from_m3u8: Use is_close_match over startswith() when checking language equality.
- Tracks.from_m3u8: Allow `lang` argument to be optional.
- Tracks.from_m3u8: Assuming Video tracks as original_lang=True (if lang is provided) as there is no language
  information provided to videos in the M3U spec.

## [0.0.5] - 2021-08-08

### Added
- Credential: A SHA1 is now generated on the `username:password` as a HEX string. Like Basic Authorization format
  but as HEX instead of Base64.
- Tracks.from_m3u8: Check for `descriptive` and `sdh` using `describes-video` and `describes-music-and-sound`
  accessibility characteristics respectively. Removes the duplicate manual checks from ATVP and iT.
- BamSDK: Implement the `session` service endpoint. All (2) known functions/endpoints have been implemented.
- DSNP: Add token caching and refreshing capability. Cached Tokens are per-region and per-account. The per-region
  is necessary as Disney+ ties the initial IP region to the tokens, and ignores any further region changes since.
- DSNP: Print information about the Access Token using the new BamSDK session functions.
- DSNP: Get chapters based on milestone data.
- HULU: Add new device id and keys. Specifically ones for: PC (159), Chrome (190), FireTV (188), and Shield (109).

### Changed
- Tracks.repackage: When repackaging, no extra metadata on the stream will be kept, and no additional metadata will
  be added by FFmpeg, allowing each repackage to be bit-identical to previous or next repacks.
- AudioTrack & TextTrack get_track_name: Remove the language name from the track title, only have the territory and
  flag information, e.g. `Latin America (SDH)` not `Spanish (Latin America) (SDH)`.
- DSNP: The service privacy certificate has been moved to its config.
- HULU: Moved the device id and key configuration from the user service config to the app service config. The device
  ID and Key used should not need to be changed often by the user as it isn't unique to a device or account.
- HULU: Change the device configuration structure to a device key name approach. Use FireTV4K.
- VUDU: Now uses Tracks.from_mpd directly instead of Tracks.from_mpds.
- NF: Optimize get_manifest profiles list code.

### Removed
- Track.set_language has been removed. All uses of it has been appropriately changed to directly set the `language`
  attribute.
- scripts/GetItunesManifestFree.py: Already implemented (differently) within the iTunes service.

### Fixed
- dl: Keys obtained from vaults, are now cached to all available vaults, not just local vaults. Let's say you have
  a cached key in your 2nd local vault only. Then it will be saved to your 1st local vault, as well as your remote
  vault too.
- Tracks.mux: Set first video track as default.
- Tracks.mux: Set extra compression to None on ALL tracks, not just subtitle tracks. Even though mkvmerge help docs
  state it only does anything on subtitle tracks, I've confirmed this is not true as the latest MKVToolNix GUI allows
  you to set zlib for audio tracks. To play it safe, I've disabled it on all track types.
- Tracks.mux: Use is_close_match on default subtitle check instead of a direct equality comparison.
- BaseTrack & TextTrack \_\_str__: Filter out `None` objects.
- BamSDK: The internal python-requests session within the BamSDK class has been privatized. This is due to the
  attribute name conflict between the python-requests session, and the BamSDK `session` service endpoint.
- DSNP: Return error if the account has not yet subscribed to Disney+.
- utils.click: Fix accuracy of syntax regex on SeasonRange. You can no longer do e.g. `-w S01_any_text_after_sxx`.
  It now strictly requires the entire -w string (or rather entire parts of the -w string) to be correct and valid.
- scripts/UpdateLocalKeyVault: Now creates tables with the correct `TEXT NULL` constraint for `title` column.

## [0.0.4] - 2021-08-07

### Added
- New Service: Vudu (<https://www.vudu.com>).
- Tracks.from_mpds: Check for content type using mimeType and contentType per-representation if available.
- Tracks.from_mpds: Pass the URL HTTP query string (if available) from the MPD Manifest URL to the final track URLs.
- config: File allocation in aria2c is now configurable using `aria2c.file_allocation = ...`. The value must be a
  string, and `'none'` can be used to disable file allocation. It defaults to `'falloc'` if not set.

### Changed
- Tracks.sort_subtitles: Sort forced subtitles above non-forced subtitles instead of after non-forced subtitles.
  This is to follow the newer P2P release recommendations. The idea is that the less text it's going to have, the
  first it comes in the order. Previously it would do Normal (Medium), SDH (Large), Forced (Small).
- Tracks.mux: Determine default subtitle track based on matching language with the first audio tracks language
  instead of using its `original_lang` flag. It must still be a forced track to be considered for default.
- Tracks.from_m3u8: Use 7 characters in the video track `id_` MD5 HEX string rather than 6. This is to fix potential
  hash collision. It was encountered in Apple TV at least once.

### Removed
- utils.url utilities have been removed and replaced with more ample `urllib.parse.urljoin` and `posixpath.join`
  calls. `posixpath.join` may be necessary in some scenarios over `urllib.parse.urljoin`.

### Fixed
- Tracks.from_mpds: Raise ValueError if XML document is not an MPD document.
- AMZN: Fix -q quality param check, a runtime error would occur if -q was not set.
- AMZN: Force CVBR+CBR Bitrate Mode for UHD HDR10 as it would otherwise return ISM.
- AMZN: Force CVBR Bitrate Mode for UHD CBR (that isn't HDR10) as it would otherwise return ISM.
- AMZN: Force H265 Codec, CVBR Bitrate Mode, and HD Manifest for the Audio streams if the wanted codec is not
  H265, or the wanted stream is UHD HDR10.
- NF: Fix the cache file mkdir. It tried to make a directory named the file itself, not the directory of the file.
- ATVP, CORE, HULU, iT: Revert changes made to the license functions regarding the position of the `_` and `__`
  arguments.
- mypy: Mark `CTV` (Service class) and `FPS` (Frames-per-second Parser class) as classes. Mypy assumed they were
  constants due to their casing.
- dl: Replace Python 3.9 only function `Path.is_relative_to` with `Path.relative_to`.
- config: Fix the default download directory in the example config. Add information about the default temp directory.

## [0.0.3] - 2021-08-04

### Added
- New Dev dependencies: `mypy-protobuf`, and `types-requests`.
- Print Temp Files directory on startup along with the other directories.
- Temp and Downloads directories are now configurable in the main configuration file.
- Added support for more than one video track when muxing. However, it is not yet possible to select multiple video
  tracks for download.
- Generated `.pyi` files for the widevine protobuf files for mypy.
- Created is_close_match helper function that will help with comparing language tags for close equality.
- You can now map which CDM to use by both the service and profile.

### Changed
- NF: Improved and clarified the Netflix MPL vs. HPL Security notes in doc-string.
- VideoTrack: The width and height fields are no longer optional and must be a valid integer.
- MenuTrack: No longer considered part of the BaseTrack family of classes. It is no longer directly related
  to VideoTrack, AudioTrack, and TextTrack classes. This is because most of the attributes of BaseTrack are
  unnecessary for MenuTrack objects.
- MenuTrack: No longer part of the `Tracks.__iter__` iterable. Any code that expected the MenuTracks to be
  in this iterable have been altered.
- mux: Moved the function to Tracks.mux. This allows a smaller function signature and easier access to the tracks.
- mux: Now directly calls Tracks.export_chapters instead of assuming that the caller has done it beforehand.
- mux: Moved the LANGUAGE_MUX_MAP dict from BaseService to constants.
- mux: Moved the CalledProcessError catch out of the muxing function. It is now done by the caller for greater control.
- config: A new Config class object initialised as `config` is now used instead of `main` as the main config variable.
- config: Moved the get_vaults function to the Config object as `load_vault(vault: dict)`.
- SERVICE_MAP and get_service_key has been moved from constants to `/services/__init__.py` and imported separately.
- Use an ast-based FPS parser that supports `num/den` format that returns as float.
- Moved the user_configs files from `/user_configs/*` to `/example_configs/*` and removed the `.example` from
  their filenames.

### Removed
- Removed `numpy` dependency.
- Removed `LICENSE` and `README.md` from poetry source and wheel builds.

### Fixed
- pyinstaller will no longer delete the entire `/dist` folder when executed.
- Naming of CEA and EIA Closed Caption typos and incorrect usages.
- AMZN: Corrected the Meaning of CVBR as Constrained Variable Bitrate, not Capped Variable Bitrate.
- AMZN: Fix an assignment to an incorrect variable in Amazon.get_chapters.
- NF: Fix possible runtime error during audio profile selection.
- PMTP: Use their Android Movies API for get_titles due to the web API being incredibly buggy to use outside of
  JavaScript. It would often out right fail to respond, or respond absolutely empty. Not a single byte response.
- PMTP: Use their Android TV API for get_titles as the Movies API change results in different response information
  compared to what's expected by get_tracks.
- PMTP: Skip M3U8 manifests and ClearKey manifests as they are very low bitrate, and seemingly a temporary test.

## [0.0.2] - 2021-07-19

### Future Warnings

#### Vaults now create new tables without the `NOT NULL` constraint on the title column.

Vaults no longer use `NOT NULL` constraint on the `title` column. The code might change in the future to take
advantage of this, so be sure to update your vaults SQL condition before then.

You may manually update your table column to remove the NOT NULL constraint by executing the following SQL:

```sql
ALTER TABLE servicename ADD COLUMN title_ TEXT NULL;
UPDATE servicename SET title_ = TITLE;
ALTER TABLE servicename DROP COLUMN title;
ALTER TABLE servicename RENAME COLUMN title_ TO title;
```

where `servicename` is the table you wish to alter, e.g. `amazon`.

### Added
- Temporary ICON `.ico` file.
- Python Script to create a pyinstaller build, `pyinstaller.py`. Can also be called via new `make` shell scripts.
- Inno Setup `.iss` script to create a Windows installer setup file, that installs the pyinstaller build.
- PowerShell `make.ps1` and Bash `make.sh` scripts to build various distribution files easily and quickly.
  They will build a wheel package, pyinstaller build, and an Inno Setup build (on Windows only).
- Switches `--audio-only`, `--subs-only`, and `--chapters-only` can be used together. However, it may be an error
  to use all three together.

### Changed
- Vaults now create new tables without the `NOT NULL` constraint on the `title` column.
- Amazon now strictly uses a Device DRM override of `CENC`, removing it from the user config. There doesn't
  seem to be any other useful value to use.
- Amazon's manifest quality option in the user config has been moved to `-q`/`--quality` service option with a
  default of `HD`. `UHD` will automatically be forced if `-q` (`vt dl` global argument) is greater than `1080`.
- `pathlib`'s `read_text` and `read_bytes` functions replaced `io`/`os` read/write functions where possible.

### Removed
- Chrome CDM dll's, so's, and Chrome CDM Key Extractor code have been removed from the codebase as it filled up the
  git size, yet none of it is particularly useful. The Key Extractor code was for old revoked Chrome CDM dlls.

### Fixed
- Downloads/Muxing output folder is now correctly pre-created.
- UTF-8 text-encoding is now specifically set when reading or writing text files. Windows used a default of CP1252.
- Amazon's get_original_language check on audioTrackMetadata is now correctly iterated.
- AddKeysToKeyVault now correctly checks by kid instead of accidentally by key.
- The correct version is now in the first log print in the root code. The retrieved version is from the
  `tool.poetry` section of `pyproject.toml`.

## [0.0.1] - 2021-07-18

### Added
- Initial versioned release.

[Unreleased]: https://github.com/rlaphoenix/vinetrimmer/compare/v0.0.7...HEAD
[0.0.7]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.7
[0.0.6]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.6
[0.0.5]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.5
[0.0.4]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.4
[0.0.3]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.3
[0.0.2]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.2
[0.0.1]: https://github.com/rlaphoenix/vinetrimmer/releases/tag/v0.0.1
