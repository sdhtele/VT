from __future__ import annotations

import json
from http.cookiejar import MozillaCookieJar
from typing import Any

import click
from click import Context
from langcodes import Language

from vinetrimmer.config import directories
from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class Filmio(BaseService):
    """
    Service code for Filmio (https://www.filmio.hu/).

    \b
    Authorization: Credentials or Cookies
    Security: UHD@-- FHD@L3

    Note: The service currently uses a static key to encrypt all content.
    """

    ALIASES = ["FMIO", "filmio"]
    GEOFENCE = ["hu"]

    @staticmethod
    @click.command(name="Filmio", short_help="https://filmio.hu")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Filmio:
        return Filmio(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        self.profile = ctx.obj.profile

        self.configure()

    def get_titles(self) -> Title:
        r = self.session.get(
            self.config["endpoints"]["metadata"].format(title_id=self.title)
        )
        res = r.json()

        if res.get("status") == 401:
            self.log.warning(" - Cookies expired, logging in again...")
            self.cookie_file.unlink()
            self.configure()
            return self.get_titles()

        if "error" in res:
            self.log.exit(f" - Failed to get manifest: {res['message']} [{res['status']}]")
            raise

        if res["visibilityDetails"] != "OK":
            self.log.exit(f" - This title is not available. [{res['visibilityDetails']}]")
            raise

        return Title(
            id_=self.title,
            type_=Title.Types.MOVIE,
            name=res["name"],
            year=res["title"]["year"],
            source=self.ALIASES[0],
            service_data=r
        )

    def get_tracks(self, title: Title) -> Tracks:
        res = title.service_data.json()
        mpd_url = res["movie"]["contentUrl"]

        tracks = Tracks.from_mpds(
            data=self.session.get(mpd_url).text,
            url=mpd_url,
            lang=Language.get(next(iter(res["originalLanguages"][0]))),
            source=self.ALIASES[0]
        )

        for track in tracks:
            track.needs_proxy = True
            track.get_kid()
            if track.kid == "6761374a7eb04b59a595a943f4dbcdbe":
                track.key = "ed38695f26825877db9b0335f2212bb9"

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, *, challenge: bytes, title: Title, **_: Any) -> bytes:
        r = self.session.post(self.config["endpoints"]["license"], params={
            "drmToken": title.service_data.headers["drmtoken"]
        }, data=challenge)

        try:
            res = r.json()
        except json.decoder.JSONDecodeError:
            # Not valid JSON, so probably an actual license
            return r.content
        else:
            self.log.exit(f" - Failed to get license: {res['message']} [{res['status']}]")

    # Service-specific functions

    def configure(self) -> None:
        self.cookie_file = directories.cookies / self.__class__.__name__ / f"{self.profile}.txt"
        cookie_jar = MozillaCookieJar(self.cookie_file)

        if self.cookie_file.is_file():
            cookie_jar.load()
            self.session.cookies.update(cookie_jar)
            self.log.info(" + Using saved cookies")
            return

        self.log.info(" + Logging in")
        if not (self.credentials and self.credentials.username and self.credentials.password):
            self.log.exit(" - No credentials provided, unable to log in.")
            raise

        res = self.session.post(self.config["endpoints"]["login"], data={
            "username": self.credentials.username,
            "password": self.credentials.password
        }).json()

        if "error" in res:
            self.log.exit(f" - Failed to log in: {res['message']} [{res['status']}]")
            raise

        for cookie in self.session.cookies:
            cookie_jar.set_cookie(cookie)
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_jar.save(ignore_discard=True)
