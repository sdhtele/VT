from __future__ import annotations

import base64
import json
import re
from http.cookiejar import MozillaCookieJar
from typing import Any, NoReturn, Union

import click
from click import Context

from vinetrimmer.config import directories
from vinetrimmer.objects import MenuTrack, Title, Track, Tracks
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils.collections import as_list


class Showtime(BaseService):
    """
    Service code for Showtime (https://www.showtime.com/).

    \b
    Authorization: Credentials or Cookies
    Security: UHD@L3
    """

    ALIASES = ["SHO", "showtime"]
    GEOFENCE = ["us"]

    VIDEO_RANGE_MAP = {
        "DV": "DOLBY_VISION"
    }

    AUDIO_CODEC_MAP = {
        "AAC": "mp4a",
        "AC3": "ac-3",
        "EC3": "ec-3"
    }

    @staticmethod
    @click.command(name="Showtime", short_help="https://showtime.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Showtime:
        return Showtime(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        assert ctx.parent is not None

        self.profile = ctx.obj.profile

        self.range = ctx.parent.params["range_"]
        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]

        if (ctx.parent.params.get("quality") or 0) > 1080 and self.vcodec != "H265":
            self.log.info(" + Switched video codec to H265 to be able to get 2160p video track")
            self.vcodec = "H265"

        if self.range in ("HDR10", "DV") and self.vcodec != "H265":
            self.log.info(f" + Switched video codec to H265 to be able to get {self.range} dynamic range")
            self.vcodec = "H265"

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(
            self.config["endpoints"]["metadata"]["movie" if self.movie else "series"].format(title_id=self.title)
        ).json()

        self.log.debug(json.dumps(res, indent=4))
        if "error" in res:
            self.log_exit(res)

        if self.movie:
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=res["name"],
                year=res["releaseYear"],
                source=self.ALIASES[0],
                service_data=res
            )
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=ep["series"]["seriesTitle"],
            season=ep["series"]["seasonNum"],
            episode=ep["series"]["episodeNum"],
            episode_name=ep["name"],
            source=self.ALIASES[0],
            service_data=ep
        ) for ep in res["episodesForSeries"]]

    def get_tracks(self, title: Title) -> Tracks:
        tracks = Tracks()

        # Both HDR10 and DV include SDR, but they don't include each other,
        # so if no range is explicitly specified, we need to request two manifests.
        ranges = [self.range] if self.range else (["HDR10", "DV"] if self.vcodec == "H265" else ["SDR"])

        for range_ in ranges:
            res = self.start_play(title, range_)

            new_tracks = Tracks.from_mpds(
                data=self.session.get(res["uri"]).text,
                url=res["uri"],
                lang="en",  # TODO: Don't assume
                source=self.ALIASES[0]
            )
            for track in new_tracks:
                track.extra = {
                    "manifest": res,
                    "range": range_,
                }
            tracks.add(new_tracks, warn_only=len(ranges) > 1)  # may be duplicate SDR if >1 ranges, HDR/DV include SDR

            # Needed to avoid 3 simultaneous video streams reached error
            # TODO: Only call this after license (but we need to call it even if we used cached keys)
            r = self.session.get(
                self.config["endpoints"]["endplay"].format(title_id=title.service_data["id"], at=res["at"])
            )
            if not r.ok:
                self.log.warning(
                    " - Failed to send endplay request, this may result in a too many concurrent streams error."
                )

        for track in tracks:
            track.needs_proxy = True

        if self.acodec:
            tracks.audio = [
                x for x in tracks.audio
                if x.codec and x.codec[:4] == self.AUDIO_CODEC_MAP[self.acodec]
            ]

        # Filter out false positives that actually seem to be video(?)
        tracks.subtitles = [x for x in tracks.subtitles if x.codec and "mp4" not in x.codec]

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(
        self, *, challenge: bytes, title: Title, track: Track, retrying: bool = False, **kwargs: Any
    ) -> bytes:
        range_ = track.extra["range"]

        r = self.session.post(self.config["endpoints"]["license"], params={
            "refid": track.extra["manifest"]["refid"],
            "authToken": base64.b64encode(track.extra["manifest"]["entitlement"].encode()),
        }, data=challenge, headers={
            "X-STAT-videoQuality": self.VIDEO_RANGE_MAP.get(range_, range_)
        })

        try:
            res = r.json()
        except json.decoder.JSONDecodeError:
            # Not valid JSON, so probably an actual license
            return r.content

        if res["error"]["code"] == "widevine.auth" and not retrying:
            self.log.warning(" - Auth token expired, refreshing...")
            track.extra["manifest"] = self.start_play(title, range_)
            return self.license(challenge=challenge, title=title, track=track, retrying=True, **kwargs)

        self.log_exit(res)
        raise

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 7.1.2; AFTMM Build/NS6271)",
            "X-STAT-model": "Sparrow",
            "X-STAT-displayType": "TV",
            "X-STAT-appVersion": "1.11",
            "X-STAT-contentVersion": "OTT"
        })

        if self.vcodec == "H265":
            self.session.headers.update({
                "X-STAT-resolution": "4K"
            })

        cookie_file = directories.cookies / self.__class__.__name__ / f"{self.profile}.txt"
        cookie_jar = MozillaCookieJar(cookie_file)

        if cookie_file.is_file():
            cookie_jar.load()
            if any(x.name == "JSESSIONID" for x in cookie_jar):
                self.session.cookies.update(cookie_jar)
                self.log.info(" + Using saved cookies")
                return
            self.log.warning(" - Cookies expired, logging in again")

        self.log.info(" + Logging in")
        if not (self.credentials and self.credentials.username and self.credentials.password):
            self.log.exit(" - No credentials provided, unable to log in.")
            raise

        r = self.session.post("https://www.showtime.com/api/user/login", json={
            "email": self.credentials.username,
            "password": self.credentials.password
        })

        if not r.ok:
            self.log.exit(f" - HTTP Error {r.status_code}: {r.reason}")
            raise

        res = r.json()
        self.log.debug(res)

        if "error" in res:
            if res["error"]["code"] == "error.invalid.email":
                self.log.exit(
                    " - Invalid email. "
                    "(If your email is valid, logins from your IP may have been blocked temporarily.)"
                )
                raise
            else:
                self.log_exit(res)

        for cookie in self.session.cookies:
            cookie_jar.set_cookie(cookie)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_jar.save()

    def start_play(self, title: Title, range_: str) -> dict:
        res = self.session.get(
            self.config["endpoints"]["startplay"].format(title_id=title.service_data["id"]),
            headers={
                "X-STAT-videoQuality": self.VIDEO_RANGE_MAP.get(range_, range_)
            }
        ).json()
        self.log.debug(json.dumps(res, indent=4))

        if "error" in res:
            self.log_exit(res)

        return res

    def log_exit(self, res: dict) -> NoReturn:
        self.log.exit(f" - {res['error']['title']} - {res['error']['body']} [{res['error']['code']}]")
        raise
