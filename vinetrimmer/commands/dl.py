from __future__ import annotations

import base64
import html
import logging
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any, Optional, Union

import click
from pymediainfo import MediaInfo
from pymp4.parser import Box

from vinetrimmer import services
from vinetrimmer.config import config, credentials, directories, filenames
from vinetrimmer.objects import Credential, TextTrack, Title, Titles, VideoTrack
from vinetrimmer.objects.vaults import Vault, Vaults
from vinetrimmer.utils import Cdm, Logger
from vinetrimmer.utils.click import LANGUAGE_RANGE, QUALITY, SEASON_RANGE, AliasedGroup, ContextData
from vinetrimmer.utils.collections import as_list, merge_dict
from vinetrimmer.utils.io import load_toml
from vinetrimmer.utils.subprocess import ffprobe
from vinetrimmer.utils.Widevine.device import LocalDevice, RemoteDevice


def get_logger(level: int = logging.INFO, log_path: Optional[Path] = None) -> Logger.Logger:
    """
    Setup logging for the Downloader.
    If log_path is not set or false-y, logs won't be stored.
    """
    log = Logger.getLogger("download", level=level)
    if log_path:
        try:
            log_path.relative_to(Path("."))  # file name only
        except ValueError:
            pass
        else:
            log_path = directories.logs / log_path
        assert log_path is not None
        log_path = log_path.parent / log_path.name.format_map(defaultdict(
            str,
            name="root",
            time=datetime.now().strftime("%Y%m%d-%H%M%S")
        ))
        if log_path.parent.exists():
            log_files = [x for x in log_path.parent.iterdir() if x.suffix == log_path.suffix]
            for log_file in log_files[::-1][19:]:
                # keep the 20 newest files and delete the rest
                log_file.unlink()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log.add_file_handler(log_path)
    return log


def get_cdm(service: str, profile: Optional[str] = None) -> Union[LocalDevice, RemoteDevice]:
    """
    Get CDM Device (either remote or local) for a specified service.
    Raises a ValueError if there's a problem getting a CDM.
    """
    cdm_name = config.cdm.get(service) or config.cdm.get("default")
    if not cdm_name:
        raise ValueError("A CDM to use wasn't listed in the vinetrimmer.toml config")
    if isinstance(cdm_name, dict):
        if not profile:
            raise ValueError("CDM config is mapped for profiles, but no profile was chosen")
        cdm_name = cdm_name.get(profile) or config.cdm.get("default")
        if not cdm_name:
            raise ValueError(f"A CDM to use was not mapped for the profile {profile}")
    cdm_api = next(iter(x for x in config.cdm_api if x["name"] == cdm_name), None)
    if cdm_api:
        return RemoteDevice(**cdm_api)
    cdm_path = directories.wvds / f"{cdm_name}.wvd"
    if not cdm_path.is_file():
        raise ValueError(f"{cdm_name} does not exist or is not a file")
    return LocalDevice.load(cdm_path)


def get_service_config(service: str) -> dict:
    """Get both Service Config and Service Secrets as one merged dictionary."""
    service_config = load_toml(str(filenames.config).format(service=service))
    user_config = load_toml(str(filenames.service_config).format(service=service))
    if user_config:
        merge_dict(user_config, service_config)
    return service_config


def get_profile(service: str, zone: Optional[str] = None) -> Optional[str]:
    """
    Get profile for Service from config.
    It also allows selection by zone if the profile config for the service is zoned.
    If it's zoned but no zone choice has been passed, then one will be gotten interactively.
    """
    profile = config.profiles.get(service)
    if profile is False:
        return None  # auth-less service if `false` in config
    if not profile:
        profile = config.profiles.get("default")
    if not profile:
        raise ValueError(f"No profile has been defined for '{service}' in the config.")

    if isinstance(profile, dict):
        profile = {k.lower(): v for k, v in profile.items()}
        print("Available Profile Zones: " + ", ".join(list(profile.keys())))
        while True:
            if zone:
                choice = zone
                zone = None  # use -z once, if -z doesn't exist, then ask interactively anyway
            else:
                choice = input("Which Zone would you like to use? ")
            chosen = profile.get(choice.lower())
            if chosen:
                return chosen
            print(" - Chosen Zone is an invalid option.")

    return profile


def get_cookie_jar(service: str, profile: str) -> Optional[MozillaCookieJar]:
    """Get Profile's Cookies as Mozilla Cookie Jar if available."""
    cookie_file = directories.cookies / service / f"{profile}.txt"
    if cookie_file.is_file():
        cookie_jar = MozillaCookieJar(cookie_file)
        cookie_file.write_text(html.unescape(cookie_file.read_text("utf8")), "utf8")
        cookie_jar.load(ignore_discard=True, ignore_expires=True)
        return cookie_jar
    return None


def get_credentials(service: str, profile: str) -> Optional[Credential]:
    """Get Profile's Credential if available."""
    cred = credentials.get(service, {}).get(profile)
    if cred:
        if isinstance(cred, list):
            return Credential(*cred)
        return Credential.loads(cred)
    return None


@click.group(name="dl", short_help="Download from a service.", cls=AliasedGroup, context_settings=dict(
    help_option_names=["-?", "-h", "--help"],
    max_content_width=116,  # max PEP8 line-width, -4 to adjust for initial indent
    default_map=config.arguments
))
@click.option("-z", "--zone", type=str, default=None,
              help="Profile zone to use when multiple profiles for a service is defined.")
@click.option("-q", "--quality", type=QUALITY, default=None,
              help="Download Resolution, defaults to best available.")
@click.option("-v", "--vcodec", type=click.Choice(["H264", "H265", "VP9", "AV1"], case_sensitive=False),
              default="H264",
              help="Video Codec, defaults to H264.")
@click.option("-a", "--acodec", type=click.Choice(["AAC", "AC3", "EC3", "VORB", "OPUS"], case_sensitive=False),
              default=None,
              help="Audio Codec")
@click.option("-r", "--range", "range_", type=click.Choice(["SDR", "HDR10", "HLG", "DV"], case_sensitive=False),
              default=None,
              help="Video Color Range, defaults to whatever is available and not strictly SDR.")
@click.option("-w", "--wanted", type=SEASON_RANGE, default=None,
              help="Wanted episodes, e.g. `S01-S05,S07`, `S01E01-S02E03`, `S02-S02E03`, e.t.c, defaults to all.")
@click.option("-l", "--lang", type=LANGUAGE_RANGE, default="en",
              help="Language wanted for Video and Audio.")
@click.option("-vl", "--v-lang", type=LANGUAGE_RANGE, default=[],
              help="Language wanted for Video, you would use this if the video language doesn't match the audio.")
@click.option("-sl", "--s-lang", type=LANGUAGE_RANGE, default=["all"],
              help="Language wanted for Subtitles.")
@click.option("--proxy", type=str, default=None,
              help="Proxy URI to use. If a 2-letter country is provided, it will try get a proxy from the config.")
@click.option("-A", "--audio-only", is_flag=True, default=False,
              help="Only download audio tracks.")
@click.option("-S", "--subs-only", is_flag=True, default=False,
              help="Only download subtitle tracks.")
@click.option("-C", "--chapters-only", is_flag=True, default=False,
              help="Only download chapters.")
@click.option("--list", "list_", is_flag=True, default=False,
              help="Skip downloading and list available tracks and what tracks would have been downloaded.")
@click.option("--keys", is_flag=True, default=False,
              help="Skip downloading, retrieve the decryption keys (via CDM or Key Vaults) and print them.")
@click.option("--cache", is_flag=True, default=False,
              help="Disable the use of the CDM and only retrieve decryption keys from Key Vaults. "
                   "If a needed key is unable to be retrieved from any Key Vaults, the title is skipped.")
@click.option("--no-cache", is_flag=True, default=False,
              help="Disable the use of Key Vaults and only retrieve decryption keys from the CDM.")
@click.option("--no-proxy", is_flag=True, default=False,
              help="Force disable all proxy use.")
@click.option("-nm", "--no-mux", is_flag=True, default=False,
              help="Do not mux the downloaded and decrypted tracks.")
@click.option("--log", "log_path", type=Path, default=filenames.log,
              help="Log path (or filename). Path can contain the following f-string args: {name} {time}.")
@click.pass_context
def dl(ctx: click.Context, zone: str, log_path: Path, *_: Any, **__: Any) -> None:
    if not ctx.invoked_subcommand:
        raise ValueError("A subcommand to invoke was not specified, the main code cannot continue.")
    service = services.get_service_key(ctx.invoked_subcommand)

    assert ctx.parent is not None

    log = get_logger(
        level=logging.DEBUG if ctx.parent.params["debug"] else logging.INFO,
        log_path=log_path
    )

    profile = get_profile(service, zone)
    service_config = get_service_config(service)
    vaults = Vaults(config.key_vaults, service=service)
    log.info(f" + {sum(v.vault_type == Vault.Types.LOCAL for v in vaults)} Local Vaults")
    log.info(f" + {sum(v.vault_type == Vault.Types.REMOTE for v in vaults)} Remote Vaults")

    try:
        device = get_cdm(service, profile)
    except ValueError as e:
        log.exit(f" - {e}")
        raise
    log.info(f" + Loaded {type(device).__name__}: {device.system_id} (L{device.security_level})")
    cdm = Cdm(device)

    if profile:
        cookies = get_cookie_jar(service, profile)
        credential = get_credentials(service, profile)
        if not cookies and not credential:
            log.exit(f" - Profile '{profile}' has no cookies nor credentials...")
            raise
    else:
        cookies = None
        credential = None

    ctx.obj = ContextData(
        config=service_config,
        vaults=vaults,
        cdm=cdm,
        profile=profile,
        cookies=cookies,
        credentials=credential
    )


@dl.result_callback()
@click.pass_context
def result(ctx: click.Context, service: services.BaseService, quality: Optional[int], range_: str, wanted: list[str],
           lang: list[str], v_lang: list[str], s_lang: list[str], audio_only: bool, subs_only: bool,
           chapters_only: bool, list_: bool, keys: bool, cache: bool, no_cache: bool, no_mux: bool,
           *_: Any, **__: Any) -> None:
    log = service.log

    service_name = type(service).__name__
    log.info(f" + Loaded [{service_name}] Class instance")

    log.info("Retrieving Titles")
    titles = Titles(as_list(service.get_titles()))
    if not titles:
        log.exit(" - No titles returned!")
        raise
    titles.order()
    titles.print()

    for title in titles.with_wanted(wanted):
        if title.type == Title.Types.TV:
            log.info("Getting tracks for {title} S{season:02}E{episode:02} - {name}".format(
                title=title.name,
                season=title.season or 0,
                episode=title.episode or 0,
                name=title.episode_name
            ))
        else:
            log.info("Getting tracks for {title} ({year})".format(title=title.name, year=title.year or "???"))

        title.tracks.add(service.get_tracks(title), warn_only=True)
        title.tracks.add(service.get_chapters(title))
        title.tracks.sort_videos(by_language=v_lang or lang)
        title.tracks.sort_audio(by_language=lang)
        title.tracks.sort_subtitles(by_language=s_lang)
        title.tracks.sort_chapters()

        log.info("> All Tracks:")
        title.tracks.print()

        try:
            title.tracks.select_videos(by_language=v_lang or lang, by_quality=quality, by_range=range_, one_only=True)
            title.tracks.select_audio(by_language=lang, with_descriptive=False)
            title.tracks.select_subtitles(by_language=s_lang, with_forced=lang)
        except ValueError as e:
            log.exit(f" - {e}")
            raise

        if audio_only or subs_only or chapters_only:
            title.tracks.videos.clear()
            if audio_only:
                if not subs_only:
                    title.tracks.subtitles.clear()
                if not chapters_only:
                    title.tracks.chapters.clear()
            elif subs_only:
                if not audio_only:
                    title.tracks.audio.clear()
                if not chapters_only:
                    title.tracks.chapters.clear()
            elif chapters_only:
                if not audio_only:
                    title.tracks.audio.clear()
                if not subs_only:
                    title.tracks.subtitles.clear()

        log.info("> Selected Tracks:")
        title.tracks.print()

        if list_:
            continue  # only wanted to see what tracks were available and chosen

        skip_title = False
        for track in title.tracks:
            log.info(f"Downloading: {track}")
            if track.encrypted:
                if not track.get_pssh(service.session):
                    log.exit(" - PSSH: Failed!")
                    raise
                log.info(f" + PSSH: {base64.b64encode(Box.build(track.pssh)).decode()}")
                if not track.get_kid(service.session):
                    log.exit(" - KID: Failed!")
                    raise
                log.info(f" + KID: {track.kid}")
            if keys:
                log.info(" + Skipping Download...")
            else:
                if track.needs_proxy:
                    proxy = next(iter(service.session.proxies.values()), None)
                else:
                    proxy = None
                track.download(directories.temp, headers=service.session.headers, proxy=proxy)
                log.info(" + Downloaded")
                if isinstance(track, VideoTrack) and not title.tracks.subtitles:
                    log.info("Checking for Apple CEA-608 Closed Captions in video stream")
                    cc = track.extract_c608()
                    if cc:
                        for c608 in cc:
                            title.tracks.add(c608)
                        log.info(f" + Found ({len(cc)}) CEA-608 Closed Caption")
            if track.encrypted:
                log.info("Decrypting...")
                if track.key:
                    log.info(f" + KEY: {track.key} (Static)")
                elif not no_cache:
                    track.key, vault_used = ctx.obj.vaults.get(track.kid, title.id)
                    if track.key:
                        log.info(f" + KEY: {track.key} (From {vault_used.name} {vault_used.vault_type} Key Vault)")
                        for vault in ctx.obj.vaults.vaults:
                            if vault == vault_used:
                                continue
                            if ctx.obj.vaults.insert_key(
                                vault, service_name.lower(), track.kid, track.key, title.id, commit=True
                            ):
                                log.info(f" + Cached to {vault.name} ({vault.vault_type}) vault")
                if not track.key:
                    if cache:
                        skip_title = True
                        break
                    session_id = ctx.obj.cdm.open(track.pssh)
                    ctx.obj.cdm.set_service_certificate(
                        session_id,
                        service.certificate(
                            challenge=ctx.obj.cdm.service_certificate_challenge,
                            title=title,
                            track=track,
                            session_id=session_id
                        ) or ctx.obj.cdm.common_privacy_cert
                    )
                    ctx.obj.cdm.parse_license(
                        session_id,
                        service.license(
                            challenge=ctx.obj.cdm.get_license_challenge(session_id),
                            title=title,
                            track=track,
                            session_id=session_id
                        )
                    )
                    content_keys = [
                        (x.kid.hex(), x.key.hex()) for x in ctx.obj.cdm.get_keys(session_id, content_only=True)
                    ]
                    if not content_keys:
                        log.exit(" - No content keys were returned by the CDM!")
                        raise
                    log.info(f" + Obtained content keys from the {type(ctx.obj.cdm.device).__name__} CDM")
                    for kid, key in content_keys:
                        log.info(f" + {kid}:{key}")
                    # cache keys into all key vaults
                    for vault in ctx.obj.vaults.vaults:
                        log.info(f"Caching to {vault.name} ({vault.vault_type}) vault")
                        cached = 0
                        for kid, key in content_keys:
                            if not ctx.obj.vaults.insert_key(vault, service_name.lower(), kid, key, title.id):
                                log.warning(f" - Failed, Table {service_name.lower()} doesn't exist in the vault.")
                            else:
                                cached += 1
                        ctx.obj.vaults.commit(vault)
                        log.info(f" + Cached {cached}/{len(content_keys)} keys")
                        if cached < len(content_keys):
                            log.warning(f"    Failed to cache {len(content_keys) - cached} keys...")
                    # use matching content key for the tracks key id
                    track.key = next((key for kid, key in content_keys if kid == track.kid), None)
                    if track.key:
                        log.info(f" + KEY: {track.key} (From CDM)")
                    else:
                        log.exit(f" - No content key with the key ID \"{track.kid}\" was returned")
                        raise
                if keys:
                    continue
                # move decryption code to Track
                platform = {"win32": "win", "darwin": "osx"}.get(sys.platform, sys.platform)
                executable = (shutil.which("shaka-packager")
                              or shutil.which("packager")
                              or shutil.which(f"packager-{platform}"))
                if not executable:
                    log.exit(f" - Unable to find shaka-packager or packager-{platform} binary")
                    raise
                dec = track.locate().with_suffix(".dec.mp4")
                try:
                    directories.temp.mkdir(parents=True, exist_ok=True)
                    subprocess.run([
                        executable,
                        "input={},stream={},output={}".format(
                            track.locate(),
                            track.__class__.__name__.lower().replace("track", ""),
                            dec
                        ),
                        "--enable_raw_key_decryption", "--keys",
                        ",".join([
                            "label=0:key_id={}:key={}".format(track.kid.lower(), track.key.lower()),
                            # Apple TV+ needs this as shaka pulls the incorrect KID, idk why
                            "label=1:key_id={}:key={}".format('00000000000000000000000000000000', track.key.lower()),
                        ]),
                        "--temp_dir", directories.temp
                    ], check=True)
                except subprocess.CalledProcessError:
                    log.exit(" - Failed!")
                    raise
                track.swap(dec)
                log.info(" + Decrypted")

            if keys:
                continue

            if track.needs_repack:
                log.info("Repackaging stream with FFMPEG (fix malformed streams)")
                track.repackage()
                log.info(" + Repackaged")

            if (isinstance(track, VideoTrack) and not title.tracks.subtitles and
                    any(x.get("codec_name", "").startswith("eia_")
                        for x in ffprobe(track.locate()).get("streams", []))):
                log.info("Extracting EIA-CC Captions from stream with CCExtractor")
                # TODO: Figure out the real language, it might be different
                #       EIA-CC tracks sadly don't carry language information :(
                # TODO: Figure out if the CC language is original lang or not.
                #       Will need to figure out above first to do so.
                track_id = f"ccextractor-{track.id}"
                cc_lang = track.language
                try:
                    cc = track.ccextractor(
                        track_id=track_id,
                        out_path=str(filenames.subtitles).format(
                            id=track_id,
                            language_code=cc_lang
                        ),
                        language=cc_lang,
                        original=False
                    )
                except EnvironmentError:
                    log.exit(" - Track needs to have CC extracted, but ccextractor wasn't found")
                    raise
                if cc:
                    title.tracks.add(cc)
                    log.info(" + Found and extracted an EIA-CC Caption from stream")
                log.info(" + Finished")
        if skip_title:
            continue
        if keys:
            continue
        if not list(title.tracks) and not title.tracks.chapters:
            continue
        # mux all final tracks to a single mkv file
        if no_mux:
            if title.tracks.chapters:
                final_file_path = directories.downloads
                if title.type == Title.Types.TV:
                    final_file_path = final_file_path / title.parse_filename(folder=True)
                final_file_path.mkdir(parents=True, exist_ok=True)
                chapters_loc = Path(str(filenames.chapters).format(filename=title.filename))
                title.tracks.export_chapters(chapters_loc)
                shutil.move(chapters_loc, final_file_path / chapters_loc.name)
            for track in title.tracks:
                media_info = MediaInfo.parse(track.locate())
                final_file_path = directories.downloads
                if title.type == Title.Types.TV:
                    final_file_path = final_file_path / title.parse_filename(folder=True) / track.__class__.__name__
                final_file_path.mkdir(parents=True, exist_ok=True)
                filename = title.parse_filename(media_info=media_info)
                extension = track.codec if isinstance(track, TextTrack) else track.locate().suffix[1:]
                track.move(final_file_path / ("{}.{}.{}".format(filename, track.id, extension)))
        else:
            log.info("Muxing tracks into a Matroska Container")
            muxed_location, returncode = title.tracks.mux(title.filename)
            if returncode == 1:
                log.warning("mkvmerge had at least one warning, will continue anyway...")
            elif returncode >= 2:
                log.exit(" - Failed to Mux video to MKV file")
                raise
            log.info(" + Muxed")
            for track in title.tracks:
                track.delete()
            if title.tracks.chapters:
                Path(str(filenames.chapters).format(filename=title.filename)).unlink(missing_ok=True)
            log.info(" + Deleted Pre-Mux Tracks")
            media_info = MediaInfo.parse(muxed_location)
            final_file_path = directories.downloads
            if title.type == Title.Types.TV:
                final_file_path = final_file_path / title.parse_filename(media_info=media_info, folder=True)
            final_file_path.mkdir(parents=True, exist_ok=True)
            # rename muxed mkv file with new data from mediainfo data of it
            if audio_only:
                extension = "mka"
            elif subs_only:
                extension = "mks"
            else:
                extension = "mkv"
            shutil.move(
                muxed_location,
                final_file_path / f"{title.parse_filename(media_info=media_info)}.{extension}"
            )

    log.info("Processed all titles!")


def load_services() -> None:
    for service in services.__dict__.values():
        if callable(getattr(service, "cli", None)):
            dl.add_command(service.cli)


load_services()
