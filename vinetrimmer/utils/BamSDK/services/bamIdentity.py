from requests import Request

from vinetrimmer.utils.BamSDK.services import Service


# noinspection PyPep8Naming
class bamIdentity(Service):

    def identityLogin(self, email: str, password: str, access_token: str) -> dict:
        endpoint = self.client.endpoints["identityLogin"]
        req = Request(
            method=endpoint.method,
            url=endpoint.href,
            headers=endpoint.get_headers(accessToken=access_token),
            json={
                "email": email,
                "password": password
            }
        ).prepare()
        res = self.session.send(req)
        return res.json()
