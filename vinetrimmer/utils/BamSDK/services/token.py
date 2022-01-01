from typing import Optional

import requests
from requests import Request

from vinetrimmer.utils.BamSDK.services import Service


# noinspection PyPep8Naming
class token(Service):

    def __init__(self, cfg: dict, session: Optional[requests.Session] = None):
        super().__init__(cfg, session)
        self.subject_tokens = self.extras["subjectTokenTypes"]

    def exchange(self, data: dict, api_key: str) -> dict:
        endpoint = self.client.endpoints["exchange"]
        req = Request(
            method=endpoint.method,
            url=endpoint.href,
            headers=endpoint.get_headers(apiKey=api_key),
            data=data
        ).prepare()
        res = self.session.send(req)
        return res.json()
