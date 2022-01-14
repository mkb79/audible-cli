import string
import unicodedata
from typing import Dict, Optional, Union

import audible
import httpx
from audible import Authenticator
from audible.aescipher import decrypt_voucher_from_licenserequest
from audible.localization import Locale
from click import secho

from .constants import CODEC_HIGH_QUALITY, CODEC_NORMAL_QUALITY
from .utils import LongestSubString


class LibraryItem:
    def __init__(self,
                 data: dict,
                 locale: Optional[Locale] = None,
                 country_code: Optional[str] = None,
                 auth: Optional[Authenticator] = None):

        if locale is None and country_code is None and auth is None:
            raise ValueError("No locale, country_code or auth provided.")
        if locale is not None and country_code is not None:
            raise ValueError("Locale and country_code provided. Expected only "
                             "one of them.")

        if country_code is not None:
            locale = Locale(country_code)

        self._data = data.get("item", data)
        self._locale = locale or auth.locale
        self._auth = auth

    def __iter__(self):
        return iter(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def __getattr__(self, attr):
        try:
            return self.__getitem__(attr)
        except KeyError:
            return None

    @property
    def full_title(self):
        title: str = self.title
        if self.subtitle is not None:
            title = f"{title}: {self.subtitle}"
        return title

    @property
    def full_title_slugify(self):
        valid_chars = "-_.() " + string.ascii_letters + string.digits
        cleaned_title = unicodedata.normalize("NFKD", self.full_title)
        cleaned_title = cleaned_title.encode("ASCII", "ignore")
        cleaned_title = cleaned_title.replace(b" ", b"_")
        slug_title = "".join(
            chr(c) for c in cleaned_title if chr(c) in valid_chars
        )

        if len(slug_title) < 2:
            return self.asin

        return slug_title

    def substring_in_title_accuracy(self, substring):
        match = LongestSubString(substring, self.full_title)
        return round(match.percentage, 2)

    def substring_in_title(self, substring, p=100):
        accuracy = self.substring_in_title_accuracy(substring)
        return accuracy >= p

    def get_cover_url(self, res: Union[str, int] = 500):
        images = self.product_images
        res = str(res)
        if images is None or res not in images:
            return
        return images[res]

    def get_pdf_url(self):
        if self.pdf_url is not None:
            domain = self._locale.domain
            return f"https://www.audible.{domain}/companion-file/{self.asin}"

    def _build_aax_request_url(self, codec: str):
        url = ("https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/"
               "FSDownloadContent")
        params = {
            "type": "AUDI",
            "currentTransportMethod": "WIFI",
            "key": self.asin,
            "codec": codec
        }
        return httpx.URL(url, params=params)

    def _extract_link_from_response(self, r: httpx.Response):
        # prepare link
        # see https://github.com/mkb79/Audible/issues/3#issuecomment-518099852
        try:
            link = r.headers["Location"]
            domain = self._locale.domain
            return link.replace("cds.audible.com", f"cds.audible.{domain}")
        except Exception as e:
            secho(f"Error: {e} occured. Can't get download link. "
                  f"Skip asin {self.asin}.")

    def _get_codec(self, quality: str):
        """If quality is not ``best``, ensures the given quality is present in
        them codecs list. Otherwise, will find the best aax quality available
        """
        assert quality in ("best", "high", "normal",)

        verify = None
        if quality != "best":
            verify = CODEC_HIGH_QUALITY if quality == "high" else \
                CODEC_NORMAL_QUALITY

        best = (None, 0, 0)
        for codec in self.available_codecs:
            if verify is not None and verify == codec["enhanced_codec"]:
                return verify

            if codec["name"].startswith("aax_"):
                name = codec["name"]
                try:
                    sample_rate, bitrate = name[4:].split("_")
                    sample_rate = int(sample_rate)
                    bitrate = int(bitrate)
                    if sample_rate > best[1] or bitrate > best[2]:
                        best = (
                            codec["enhanced_codec"],
                            sample_rate,
                            bitrate
                        )

                except ValueError:
                    secho(f"Unexpected codec name: {name}")
                    continue

        if verify is not None:
            secho(f"{verify} codec was not found, using {best[0]} instead")

        return best[0]

    @property
    def _is_downloadable(self):
        if self.content_delivery_type in ("Periodical",):
            return False

        return True

    def get_aax_url(self,
                    quality: str = "high",
                    client: Optional[httpx.Client] = None):

        if not self._is_downloadable:
            secho(f"{self.full_title} is not downloadable. Skip item.",
                  fg="red")
            return

        codec = self._get_codec(quality)
        if codec is None:
            secho(f"{self.full_title} is not downloadable. No AAX codec found.",
                  fg="red")
            return
        url = self._build_aax_request_url(codec)
        if client is None:
            assert self._auth is not None
            with httpx.Client(auth=self._auth) as client:
                resp = client.head(url=url, follow_redirects=False)
        else:
            resp = client.head(url=url, follow_redirects=False)

        return self._extract_link_from_response(resp), codec

    async def aget_aax_url(self,
                           quality: str = "high",
                           client: Optional[httpx.AsyncClient] = None):

        if not self._is_downloadable:
            secho(f"{self.full_title} is not downloadable. Skip item.",
                  fg="red")
            return

        codec = self._get_codec(quality)
        if codec is None:
            secho(f"{self.full_title} is not downloadable. No AAX codec found.",
                  fg="red")
            return
        url = self._build_aax_request_url(codec)
        if client is None:
            assert self._auth is not None
            async with httpx.AsyncClient(auth=self._auth) as client:
                resp = await client.head(url=url, follow_redirects=False)
        else:
            resp = await client.head(url=url, follow_redirects=False)

        return self._extract_link_from_response(resp), codec

    @staticmethod
    def _build_aaxc_request_body(quality: str):
        assert quality in ("best", "high", "normal",)
        return {
            "supported_drm_types": ["Mpeg", "Adrm"],
            "quality": "Extreme" if quality in ("best", "high") else "Normal",
            "consumption_type": "Download",
            "response_groups": ("last_position_heard, pdf_url, "
                                "content_reference, chapter_info")
        }

    @staticmethod
    def _extract_url_from_aaxc_response(r: Dict):
        return r["content_license"]["content_metadata"]["content_url"][
            "offline_url"]

    @staticmethod
    def _extract_codec_from_aaxc_response(r: Dict):
        return r["content_license"]["content_metadata"]["content_reference"][
            "content_format"]

    @staticmethod
    def _decrypt_voucher_from_aaxc_response(r: Dict, auth: Authenticator):
        voucher = decrypt_voucher_from_licenserequest(auth, r)
        r["content_license"]["license_response"] = voucher
        return r

    def get_aaxc_url(self,
                     quality: str = "high",
                     api_client: Optional[audible.Client] = None):

        body = self._build_aaxc_request_body(quality)
        if api_client is None:
            assert self._auth is not None
            cc = self._locale.country_code
            with audible.Client(auth=self._auth,
                                country_code=cc) as api_client:
                lr = api_client.post(
                    f"content/{self.asin}/licenserequest", body=body)
        else:
            lr = api_client.post(f"content/{self.asin}/licenserequest",
                                 body=body)

        url = self._extract_url_from_aaxc_response(lr)
        codec = self._extract_codec_from_aaxc_response(lr)
        dlr = self._decrypt_voucher_from_aaxc_response(lr, api_client.auth)

        return url, codec, dlr

    async def aget_aaxc_url(self,
                            quality: str = "high",
                            api_client: Optional[audible.AsyncClient] = None):

        body = self._build_aaxc_request_body(quality)
        if api_client is None:
            assert self._auth is not None
            cc = self._locale.country_code
            async with audible.AsyncClient(auth=self._auth,
                                           country_code=cc) as api_client:
                lr = await api_client.post(
                    f"content/{self.asin}/licenserequest", body=body)
        else:
            lr = await api_client.post(f"content/{self.asin}/licenserequest",
                                       body=body)

        url = self._extract_url_from_aaxc_response(lr)
        codec = self._extract_codec_from_aaxc_response(lr)
        dlr = self._decrypt_voucher_from_aaxc_response(lr, api_client.auth)

        return url, codec, dlr

    def _build_metadata_request_url(self, quality: str):
        assert quality in ("best", "high", "normal",)
        url = f"content/{self.asin}/metadata"
        params = {
            "response_groups": "last_position_heard, content_reference, "
                               "chapter_info",
            "quality": "Extreme" if quality in ("best", "high") else "Normal",
            "drm_type": "Adrm"
        }
        return url, params

    def get_content_metadata(self,
                             quality: str = "high",
                             api_client: Optional[audible.Client] = None):

        url, params = self._build_metadata_request_url(quality)
        if api_client is None:
            assert self._auth is not None
            cc = self._locale.country_code
            with audible.Client(auth=self._auth,
                                country_code=cc) as api_client:
                metadata = api_client.get(url, params=params)
        else:
            metadata = api_client.get(url, params=params)

        return metadata

    async def aget_content_metadata(self,
                                    quality: str = "high",
                                    api_client: Optional[
                                        audible.AsyncClient] = None):

        url, params = self._build_metadata_request_url(quality)
        if api_client is None:
            assert self._auth is not None
            cc = self._locale.country_code
            async with audible.AsyncClient(auth=self._auth,
                                           country_code=cc) as api_client:
                metadata = await api_client.get(url, params=params)
        else:
            metadata = await api_client.get(url, params=params)

        return metadata


class Library:
    def __init__(self,
                 data: Union[dict, list],
                 locale: Optional[Locale] = None,
                 country_code: Optional[str] = None,
                 auth: Optional[Authenticator] = None):

        if locale is None and country_code is None and auth is None:
            raise ValueError("No locale, country_code or auth provided.")
        if locale is not None and country_code is not None:
            raise ValueError("Locale and country_code provided. Expected only "
                             "one of them.")

        locale = Locale(country_code) if country_code else locale
        self._locale = locale or auth.locale
        self._auth = auth

        if isinstance(data, dict):
            data = data.get("items", data)
        self._data = [LibraryItem(i, locale=self._locale, auth=self._auth)
                      for i in data]

    def __iter__(self):
        return iter(self._data)

    @classmethod
    def get_from_api(cls,
                     api_client: audible.Client,
                     locale: Optional[Locale] = None,
                     country_code: Optional[str] = None,
                     close_session: bool = False,
                     **request_params):

        def fetch_library(params):
            entire_lib = False
            if "page" not in params and "num_results" not in params:
                entire_lib = True
                params["page"] = 1
                num_results = 1000
                params["num_results"] = num_results

            library = []
            while True:
                r = api_client.get(
                    "library", params=params)
                items = r["items"]
                len_items = len(items)
                library.extend(items)
                if not entire_lib or len_items < num_results:
                    break
                params["page"] += 1
            return library

        if locale is not None and country_code is not None:
            raise ValueError("Locale and country_code provided. Expected only "
                             "one of them.")

        locale = Locale(country_code) if country_code else locale
        if locale:
            api_client.locale = locale

        if close_session:
            with api_client:
                resp = fetch_library(request_params)
        else:
            resp = fetch_library(request_params)

        return cls(resp, auth=api_client.auth)

    @classmethod
    async def aget_from_api(cls,
                            api_client: audible.AsyncClient,
                            locale: Optional[Locale] = None,
                            country_code: Optional[str] = None,
                            close_session: bool = False,
                            **request_params):

        async def fetch_library(params):
            entire_lib = False
            if "page" not in params and "num_results" not in params:
                entire_lib = True
                params["page"] = 1
                num_results = 1000
                params["num_results"] = num_results

            library = []
            while True:
                r = await api_client.get(
                    "library", params=params)
                items = r["items"]
                len_items = len(items)
                library.extend(items)
                if not entire_lib or len_items < num_results:
                    break
                params["page"] += 1
            return library

        if locale is not None and country_code is not None:
            raise ValueError("Locale and country_code provided. Expected only "
                             "one of them.")

        locale = Locale(country_code) if country_code else locale
        if locale:
            api_client.locale = locale

        if close_session:
            async with api_client:
                resp = await fetch_library(request_params)
        else:
            resp = await fetch_library(request_params)

        return cls(resp, auth=api_client.auth)

    def get_item_by_asin(self, asin):
        try:
            return next(i for i in self._data if asin in i.asin)
        except StopIteration:
            return None

    def asin_in_library(self, asin):
        return True if self.get_item_by_asin(asin) else False

    def search_item_by_title(self, search_title, p=80):
        match = []
        for i in self._data:
            accuracy = i.substring_in_title_accuracy(search_title)
            match.append([i, accuracy]) if accuracy >= p else ""

        return match
