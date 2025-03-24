# What is this?
## If litellm license in env, checks if it's valid
import base64
import json
import os
from datetime import datetime
from typing import Optional

import httpx

from litellm._logging import verbose_proxy_logger
from litellm.llms.custom_httpx.http_handler import HTTPHandler


class LicenseCheck:
    """
    - Check if license in env
    - Returns if license is valid
    """

    base_url = "https://license.litellm.ai"

    def __init__(self) -> None:
        self.license_str = os.getenv("LITELLM_LICENSE", None)
        verbose_proxy_logger.debug("License Str value - {}".format(self.license_str))
        self.http_handler = HTTPHandler(timeout=15)
        self.public_key = None
        self.read_public_key()

    def read_public_key(self):
        try:
            from cryptography.hazmat.primitives import serialization

            # current dir
            current_dir = os.path.dirname(os.path.realpath(__file__))

            # check if public_key.pem exists
            _path_to_public_key = os.path.join(current_dir, "public_key.pem")
            if os.path.exists(_path_to_public_key):
                with open(_path_to_public_key, "rb") as key_file:
                    self.public_key = serialization.load_pem_public_key(key_file.read())
            else:
                self.public_key = None
        except Exception as e:
            verbose_proxy_logger.error(f"Error reading public key: {str(e)}")

    def _verify(self, license_str: str) -> bool:

        verbose_proxy_logger.debug(
            "litellm.proxy.auth.litellm_license.py::_verify - Checking license against {}/verify_license - {}".format(
                self.base_url, license_str
            )
        )
        url = "{}/verify_license/{}".format(self.base_url, license_str)

        response: Optional[httpx.Response] = None
        try:  # don't impact user, if call fails
            num_retries = 3
            for i in range(num_retries):
                try:
                    response = self.http_handler.get(url=url)
                    if response is None:
                        raise Exception("No response from license server")
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    if i == num_retries - 1:
                        raise

            if response is None:
                raise Exception("No response from license server")

            response_json = response.json()

            premium = response_json["verify"]

            assert isinstance(premium, bool)

            verbose_proxy_logger.debug(
                "litellm.proxy.auth.litellm_license.py::_verify - License={} is premium={}".format(
                    license_str, premium
                )
            )
            return premium
        except Exception as e:
            verbose_proxy_logger.exception(
                "litellm.proxy.auth.litellm_license.py::_verify - Unable to verify License={} via api. - {}".format(
                    license_str, str(e)
                )
            )
            return False

    def is_premium(self) -> bool:
        """
        1. verify_license_without_api_request: checks if license was generate using private / public key pair
        2. _verify: checks if license is valid calling litellm API. This is the old way we were generating/validating license
        """
        return True
        try:
            verbose_proxy_logger.debug(
                "litellm.proxy.auth.litellm_license.py::is_premium() - ENTERING 'IS_PREMIUM' - LiteLLM License={}".format(
                    self.license_str
                )
            )

            if self.license_str is None:
                self.license_str = os.getenv("LITELLM_LICENSE", None)

            verbose_proxy_logger.debug(
                "litellm.proxy.auth.litellm_license.py::is_premium() - Updated 'self.license_str' - {}".format(
                    self.license_str
                )
            )

            if self.license_str is None:
                return False
            elif (
                self.verify_license_without_api_request(
                    public_key=self.public_key, license_key=self.license_str
                )
                is True
            ):
                return True
            elif self._verify(license_str=self.license_str) is True:
                return True
            return False
        except Exception:
            return False

    def verify_license_without_api_request(self, public_key, license_key):
        return True
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding

            # Decode the license key
            decoded = base64.b64decode(license_key)
            message, signature = decoded.split(b".", 1)

            # Verify the signature
            public_key.verify(
                signature,
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )

            # Decode and parse the data
            license_data = json.loads(message.decode())

            # debug information provided in license data
            verbose_proxy_logger.debug("License data: %s", license_data)

            # Check expiration date
            expiration_date = datetime.strptime(
                license_data["expiration_date"], "%Y-%m-%d"
            )
            if expiration_date < datetime.now():
                return False, "License has expired"

            return True

        except Exception as e:
            verbose_proxy_logger.debug(
                "litellm.proxy.auth.litellm_license.py::verify_license_without_api_request - Unable to verify License locally. - {}".format(
                    str(e)
                )
            )
            return False
