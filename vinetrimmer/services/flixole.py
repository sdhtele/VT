from __future__ import annotations

import json
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class FlixOle(BaseService):
    """
    Service code for FlixOlé streaming service (https://ver.flixole.com/).

    \b
    Authorization: Credentials
    Security: HD@L3, doesn't care about releases.
    """

    ALIASES = ["FO", "flixole"]

    @staticmethod
    @click.command(name="FlixOle", short_help="https://ver.flixole.com")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> FlixOle:
        return FlixOle(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        self.login_data: dict[str, str] = {}
        self.entitlement_id = None
        self.license_headers = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.post(
            self.config["endpoints"]["title"],
            json={
                "variables": {
                    "viewableId": f"{self.title}",
                    "broadcastId": "",
                },
                "query": """
                    query viewable($viewableId: ID!) {
                        viewer {
                            id: magineId
                            viewable(magineId: $viewableId) {
                              __typename
                              id: magineId
                              title
                              description
                              ...MovieFragment
                            }
                          }
                        }

                        fragment MovieFragment on Movie {
                          title
                          banner: image(type: \"sixteen-nine\")
                          poster: image(type: \"poster\")
                          metaImage: image(type: \"poster\")
                          description
                          duration
                          durationHuman
                          genres
                          productionYear
                          inMyList
                          trailer
                          entitlement {
                            ...EntitlementFragment
                          }
                          defaultPlayable {
                            ...PlayableFragment
                          }
                          providedBy {
                            brand
                          }
                          webview
                        }

                        fragment EntitlementFragment on EntitlementInterfaceType {
                          __typename
                          offer {
                            ...OfferFragment
                          }
                          purchasedAt
                          ... on EntitlementRentType {
                            entitledUntil
                          }
                          ... on EntitlementPassType {
                            entitledUntil
                          }
                        }

                        fragment OfferFragment on OfferInterfaceType {
                          __typename
                          id
                          title

                          ... on BuyType {
                            priceInCents
                            currency
                            buttonText
                          }

                          ... on RentType {
                            priceInCents
                            currency
                            buttonText
                            entitlementDurationSec
                          }

                          ... on SubscribeType {
                            priceInCents
                            currency
                            buttonText
                            trialPeriod {
                              length
                              unit
                            }
                            recurringPeriod {
                              length
                              unit
                            }
                          }

                          ... on PassType {
                            priceInCents
                            currency
                            buttonText
                          }
                        }

                        fragment PlayableFragment on Playable {
                          ...ChannelPlayableFragment
                          ...BroadcastPlayableFragment
                          ...VodPlayableFragment
                          ...LiveEventPlayableFragment
                        }

                        fragment ChannelPlayableFragment on ChannelPlayable {
                          id
                          kind
                          mms
                          mmsOrigCode
                          rights {
                            fastForward
                            pause
                            rewind
                          }
                        }

                        fragment BroadcastPlayableFragment on BroadcastPlayable {
                          id
                          kind
                          channel {
                            title
                            logoDark: image(type: \"logo-dark\")
                          }
                          startTimeUtc
                          duration
                          catchup {
                            from
                            to
                          }
                          watchOffset
                        }

                        fragment VodPlayableFragment on VodPlayable {
                          id
                          kind
                          duration
                          watchOffset
                        }

                        fragment LiveEventPlayableFragment on LiveEventPlayable {
                          id
                          kind
                          startTimeUtc
                        }
                        """
            })
        try:
            data = res.json()["data"]["viewer"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load title data: {res.text}")
        return Title(
            id_=self.title,
            type_=Title.Types.MOVIE,
            name=data["viewable"]["title"],
            year=data["viewable"]["productionYear"],
            original_lang="es",  # TODO: Don't assume
            source=self.ALIASES[0],
            service_data=data
        )

    def get_tracks(self, title: Title) -> Tracks:
        res = self.session.post(
            self.config["endpoints"]["entitlement"].format(id=title.service_data["viewable"]["defaultPlayable"]["id"])
        )
        try:
            self.entitlement_id = res.json()["token"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load entitlement ID: {res.text}")
        res = self.session.post(
            self.config["endpoints"]["manifest"].format(id=title.service_data["viewable"]["defaultPlayable"]["id"]),
            headers={
                "Magine-Play-DeviceId": self.config["device"]["id"],
                "Magine-Play-DeviceModel": self.config["device"]["model"],
                "Magine-Play-DevicePlatform": self.config["device"]["platform"],
                "Magine-Play-DeviceType": self.config["device"]["type"],
                "Magine-Play-DRM": self.config["device"]["drm"],
                "Magine-Play-Protocol": self.config["device"]["protocol"],
                "Magine-Play-EntitlementId": self.entitlement_id
            }
        )
        try:
            manifest_data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load entitlement ID: {res.text}")
        self.license_headers = manifest_data["headers"]

        tracks = Tracks.from_mpds(
            data=self.session.get(manifest_data["playlist"]).text,
            url=manifest_data["playlist"],
            lang=title.original_lang,
            source=self.ALIASES[0]
        )

        tracks.subtitles = [x for x in tracks.subtitles if x.codec == "vtt"]

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, **_: Any) -> bytes:
        lic = self.session.post(
            self.config["endpoints"]["license"],
            headers=self.license_headers,
            data=challenge  # expects bytes
        )
        return lic.content  # bytes

    # Service specific functions

    def configure(self) -> None:
        self.log.info(" + Logging in")
        self.session.headers.update({
            "Magine-AccessToken": f"{self.config['device']['access_token']}"
        })
        self.login_data = self.login()
        self.session.headers.update({
            "authorization": f"Bearer {self.login_data['token']}"
        })

    def login(self) -> dict:
        if not self.credentials:
            self.log.exit(" - No credentials provided, unable to log in.")
            raise
        res = self.session.post(
            self.config["endpoints"]["login"],
            json={
                "identity": self.credentials.username,
                "accessKey": self.credentials.password
            }
        )
        try:
            return res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to log in: {res.text}")
