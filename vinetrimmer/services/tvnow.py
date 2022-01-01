from __future__ import annotations

import base64
import json
import re
from typing import Any, Optional, Union

import click
from bs4 import BeautifulSoup
from click import Context
from langcodes import Language

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class TVNOW(BaseService):
    """
    Service code for RTL Germany's TVNOW (https://www.tvnow.de/).

    \b
    Authorization: Cookies
    Security: UHD@-- FHD@L3

    Requires an EU IP, and they block servers/VPNs for license requests.
    """

    ALIASES = ["TVNOW"]

    @staticmethod
    @click.command(name="TVNOW", short_help="https://tvnow.de")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> TVNOW:
        return TVNOW(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        assert ctx.parent is not None

        self.profile = ctx.obj.profile

        self.configure()

    def configure(self) -> None:
        self.session.headers.update({
            "origin": "https://www.tvnow.de",
            "referer": "https://www.tvnow.de/",
            "X-Auth-Token": self.session.cookies["jwt"],
            "X-Now-Logged-In": "1"
        })

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(self.config["endpoints"]["title_info_web"].format(title_id=self.title)).json()
        self.log.debug(res)

        content_type = next(iter(res["seo"]["jsonLd"]))

        # TODO: Find a way to replace HTML parsing for original language
        soup = BeautifulSoup(res["seo"]["text"], "lxml-html")

        try:
            original_lang: Optional[Language] = Language.find((
                soup.find(string="Originalsprache (OV)")
                or soup.find(string="Originalsprache")
            ).find_next("li").text)
        except AttributeError:
            self.log.warning(" - Unable to obtain the title's original language...")
            original_lang = None

        if content_type == "movie":
            user_info = json.loads(base64.b64decode(self.session.cookies["jwt"].split(".")[1]))

            res2 = self.session.get(self.config["endpoints"]["title_info_firetv"].format(title_id=self.title), headers={
                "X-Pay-Type": "premium" if user_info["permissions"]["vodPremium"] else "free",
                "X-GOOGLE-ID": "231080e6-9b21-4ee8-9ca4-5ca7d395d962",
                "X-CLIENT-VERSION": "400000",
                "X-TRANSFORMSCOPE": "fire",
                "transformscope": "fire",
                "X-DEVICE-TYPE": "tv",
                "X-Bff-Api-Version": "1",
                "User-Agent": "okhttp/4.9.0"
            }).json()
            self.log.debug(res2)

            return Title(
                id_=next(x["id"] for x in res["modules"] if x["type"] == "player"),
                type_=Title.Types.MOVIE,
                name=res["seo"]["jsonLd"]["movie"]["name"],
                year=res2["teaser"]["video"]["productionYear"],
                original_lang=original_lang,
                source=self.ALIASES[0]
            )
        elif content_type == "series":
            res = self.session.get(self.config["endpoints"]["navigation"].format(title_id=self.title)).json()
            self.log.debug(res)

            annual = res["moduleLayout"] == "format_annual_navigation"

            if annual:
                seasons = []
                for x in res["items"]:
                    for y in x["months"]:
                        seasons.append((x["year"], y["month"]))
            else:
                seasons = [x["season"] for x in res.get("items") or []]

            titles = []

            for season in seasons:
                if annual:
                    year, month = season
                    res = self.session.get(
                        self.config["endpoints"]["episodes_annual"].format(title_id=self.title, year=year, month=month)
                    ).json()
                else:
                    res = self.session.get(
                        self.config["endpoints"]["episodes"].format(title_id=self.title, season=season)
                    ).json()
                self.log.debug(json.dumps(res, indent=4))

                titles += [Title(
                    id_=ep["videoId"],
                    type_=Title.Types.TV,
                    name=ep["ecommerce"]["teaserFormatName"],
                    season=next(iter(re.findall(r"^Staffel (\d+)$", ep["ecommerce"].get("teaserSeason", ""))), None),
                    episode=next(iter(
                        re.findall(r"^Folge (\d+)$", ep["ecommerce"].get("teaserEpisodeNumber", ""))), None
                    ),
                    episode_name=ep["ecommerce"].get("teaserEpisodeName") or ep["headline"],
                    original_lang=original_lang,
                    source=self.ALIASES[0]
                ) for ep in res["items"]]

            return titles
        else:
            self.log.exit(f" - Unsupported content type: {content_type}")
            raise

    def get_tracks(self, title: Title) -> Tracks:
        res = self.session.get(self.config["endpoints"]["player"].format(title_id=title.id)).json()
        self.log.debug(json.dumps(res, indent=4))

        return Tracks.from_mpd(
            uri=res["videoConfig"]["videoSource"]["streams"]["dashHdUrl"],
            lang=title.original_lang,
            source=self.ALIASES[0]
        )

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, **_: Any) -> bytes:
        r = self.session.post(self.config["endpoints"]["license"], data=challenge)

        if not r.content:
            self.log.exit(" - No license returned!")
            raise

        try:
            res = r.json()
        except json.decoder.JSONDecodeError:
            # Not valid JSON, so probably an actual license
            return r.content
        else:
            self.log.debug(res)
            self.log.exit(f" - Failed to get license: {res['error']}")
            raise
