from requests import Request

from vinetrimmer.utils.BamSDK.services import Service


# noinspection PyPep8Naming
class content(Service):

    def getDmcEpisodes(self, region: str, season_id: str, page: int, access_token: str) -> dict:
        endpoint = self.client.endpoints["getDmcEpisodes"]
        req = Request(
            method=endpoint.method,
            url=f"https://search-api-disney.svcs.dssott.com/svc/content/DmcEpisodes/version/3.3/region/{region}/audience/false/maturity/1850/language/en/seasonId/{season_id}/pageSize/15/page/{page}",  # noqa: E501
            headers=endpoint.get_headers(accessToken=access_token)
        ).prepare()
        res = self.session.send(req)
        return res.json()

    def getDmcSeriesBundle(self, region: str, media_id: str, access_token: str) -> dict:
        endpoint = self.client.endpoints["getDmcSeriesBundle"]
        req = Request(
            method=endpoint.method,
            url=f"https://search-api-disney.svcs.dssott.com/svc/content/DmcSeriesBundle/version/3.3/region/{region}/audience/false/maturity/1850/language/en/encodedSeriesId/{media_id}",  # noqa: E501
            headers=endpoint.get_headers(accessToken=access_token)
        ).prepare()
        res = self.session.send(req)
        return res.json()

    def getDmcVideoBundle(self, region: str, media_id: str, access_token: str) -> dict:
        endpoint = self.client.endpoints["getDmcVideoBundle"]
        req = Request(
            method=endpoint.method,
            url=f"https://search-api-disney.svcs.dssott.com/svc/content/DmcVideoBundle/version/3.3/region/{region}/audience/false/maturity/1850/language/en/encodedFamilyId/{media_id}",  # noqa: E501
            headers=endpoint.get_headers(accessToken=access_token)
        ).prepare()
        res = self.session.send(req)
        return res.json()
