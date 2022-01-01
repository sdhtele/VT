from __future__ import annotations

import json
import urllib.parse
from typing import Any, Optional, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, TextTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class Crave(BaseService):
    """
    Service code for Bell Media's Crave streaming service (https://crave.ca).

    \b
    Authorization: Credentials
    Security: UHD@-- HD@L3, doesn't care about releases.

    TODO: Movies are not yet supported
    """

    ALIASES = ["CRAV", "crave"]  # CRAV is unconfirmed but likely candidate, been in use for a few months
    GEOFENCE = ["ca"]

    @staticmethod
    @click.command(name="Crave", short_help="https://crave.ca")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Crave:
        return Crave(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        self.access_token: str

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        title_information = self.session.post(
            url="https://www.crave.ca/space-graphql/graphql",
            json={
                "operationName": "axisMedia",
                "variables": {
                    "axisMediaId": self.title
                },
                "query": """
                query axisMedia($axisMediaId: ID!) {
                    contentData: axisMedia(id: $axisMediaId) {
                        id
                        axisId
                        title
                        originalSpokenLanguage
                        firstPlayableContent {
                            id
                            title
                            axisId
                            path
                            seasonNumber
                            episodeNumber
                        }
                        mediaType
                        firstAirYear
                        seasons {
                            title
                            id
                            seasonNumber
                        }
                    }
                }
                """
            }
        ).json()["data"]["contentData"]
        titles = []
        if title_information["mediaType"] == "SPECIAL":  # e.g. "tv-show" titles that are 1 episode "movies"
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=title_information["title"],
                year=title_information.get("firstAirYear"),
                original_lang=title_information["originalSpokenLanguage"],
                source=self.ALIASES[0],
                service_data=title_information["firstPlayableContent"]
            )
        for season in title_information["seasons"]:
            titles.extend(self.session.post(
                url="https://www.crave.ca/space-graphql/graphql",
                json={
                    "operationName": "season",
                    "variables": {
                        "seasonId": season["id"]
                    },
                    "query": """
                    query season($seasonId: ID!) {
                        axisSeason(id: $seasonId) {
                            episodes {
                                axisId
                                title
                                contentType
                                seasonNumber
                                episodeNumber
                                axisPlaybackLanguages {
                                    language
                                }
                            }
                        }
                    }
                    """
                }
            ).json()["data"]["axisSeason"]["episodes"])
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=title_information["title"],
            year=title_information.get("firstAirYear"),
            season=x.get("seasonNumber"),
            episode=x.get("episodeNumber"),
            episode_name=x.get("title"),
            original_lang=title_information["originalSpokenLanguage"],
            source=self.ALIASES[0],
            service_data=x
        ) for x in titles]

    def get_tracks(self, title: Title) -> Tracks:
        package_id = self.session.get(
            url=self.config["endpoints"]["content_packages"].format(title_id=title.service_data["axisId"]),
            params={"$lang": "en"}  # TODO: --lang/--alang? or maybe title.original_lang?
        ).json()["Items"][0]["Id"]

        mpd_url = self.config["endpoints"]["manifest"].format(
            title_id=title.service_data["axisId"],
            package_id=package_id
        )
        res = self.session.get(mpd_url, params={"jwt": self.access_token})
        try:
            mpd_data = res.json()
        except json.JSONDecodeError:
            mpd_data = res.text
        else:
            raise Exception(
                "Crave reported an error when obtaining the MPD Manifest.\n" +
                f"{mpd_data['Message']} ({mpd_data['ErrorCode']})"
            )

        tracks = Tracks.from_mpds(
            data=mpd_data,
            url=mpd_url,
            lang=title.original_lang,
            source=self.ALIASES[0]
        )

        tracks.add(TextTrack(
            id_="{}_{}_sub".format(title.service_data["axisId"], package_id),
            source=self.ALIASES[0],
            url=self.config["endpoints"]["srt"].format(
                title_id=title.service_data["axisId"],
                package_id=package_id
            ) + "?" + urllib.parse.urlencode({"jwt": urllib.parse.quote_plus(self.access_token)}),
            # metadata
            codec="srt",
            language=title.original_lang,  # TODO: Get language properly from the subtitle itself
            is_original_lang=True,  # TODO: Once above TODO is done, properly check this
            sdh=True,
            # switches/options
            needs_proxy=True
        ))

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, **_: Any) -> bytes:
        return self.session.post(
            url=self.config["endpoints"]["license"],
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.log.info(" + Logging in")
        self.access_token = self.login()
        self.log.info(f"Fetching Axis title ID based on provided path: {self.title}")
        axis_id = self.get_axis_id(f"/tv-shows/{self.title}") or self.get_axis_id(f"/movies/{self.title}")
        if not axis_id:
            self.log.error(f" - Could not obtain the Axis ID for {self.title!r}, are you sure it's right?")
            raise
        self.title = axis_id
        self.log.info(f" + Obtained: {self.title}")

    def login(self) -> str:
        if not self.credentials:
            self.log.exit(" - No credentials provided, unable to log in.")
            raise
        res = self.session.post(
            self.config["endpoints"]["login"],
            headers={
                "Authorization": self.config["headers"]["authorization"]
            },
            params={
                "grant_type": "password"
            },
            data={
                "username": self.credentials.username,
                "password": self.credentials.password
            }
        )
        try:
            return res.json()["access_token"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to log in: {res.text}")

    def get_axis_id(self, path: str) -> Optional[str]:
        res = self.session.post(
            url="https://www.crave.ca/space-graphql/graphql",
            json={
                "operationName": "resolvePath",
                "variables": {
                    "path": path
                },
                "query": """
                query resolvePath($path: String!) {
                    resolvedPath(path: $path) {
                        lastSegment {
                            content {
                                id
                            }
                        }
                    }
                }
                """
            }
        ).json()
        if "errors" in res:
            if res["errors"][0]["extensions"]["code"] == "NOT_FOUND":
                return None
            raise ValueError("Unknown error has occurred when trying to obtain the Axis ID for: " + path)
        return res["data"]["resolvedPath"]["lastSegment"]["content"]["id"]
