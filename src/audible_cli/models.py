import string
import unicodedata
from typing import List, Optional, Union

import audible
import httpx
from audible.aescipher import decrypt_voucher_from_licenserequest
from click import secho

from .constants import CODEC_HIGH_QUALITY, CODEC_NORMAL_QUALITY
from .utils import LongestSubString


class BaseItem:
    def __init__(
            self,
            data: dict,
            api_client: audible.AsyncClient,
            response_groups: Optional[List] = None
    ) -> None:
        self._data = self._prepare_data(data)
        self._client = api_client
        self._response_groups = response_groups

    def __iter__(self):
        return iter(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def __getattr__(self, attr):
        try:
            return self.__getitem__(attr)
        except KeyError:
            return None

    def _prepare_data(self, data: dict) -> dict:
        return data

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
        if images is not None and res in images:
            return images[res]

    def get_pdf_url(self):
        if self.pdf_url is not None:
            domain = self._client.auth.locale.domain
            return f"https://www.audible.{domain}/companion-file/{self.asin}"


class LibraryItem(BaseItem):
    def _prepare_data(self, data: dict) -> dict:
        return data.get("item", data)

    def _get_codec(self, quality: str):
        """If quality is not ``best``, ensures the given quality is present in
        them codecs list. Otherwise, will find the best aax quality available
        """
        assert quality in ("best", "high", "normal",)

        verify = None
        if quality != "best":
            verify = CODEC_HIGH_QUALITY if quality == "high" else \
                CODEC_NORMAL_QUALITY

        best = (None, 0, 0, None)
        for codec in self.available_codecs:
            if verify is not None and verify == codec["name"]:
                return verify, codec["enhanced_codec"]

            if codec["name"].startswith("aax_"):
                name = codec["name"]
                try:
                    sample_rate, bitrate = name[4:].split("_")
                    sample_rate = int(sample_rate)
                    bitrate = int(bitrate)
                    if sample_rate > best[1] or bitrate > best[2]:
                        best = (
                            codec["name"],
                            sample_rate,
                            bitrate,
                            codec["enhanced_codec"]
                        )

                except ValueError:
                    secho(f"Unexpected codec name: {name}")
                    continue

        if verify is not None:
            secho(f"{verify} codec was not found, using {best[0]} instead")

        return best[0].upper(), best[3]

    @property
    def _is_downloadable(self):
        if self.content_delivery_type in ("Periodical", ):
            return False

        return True

    async def get_aax_url(self, quality: str = "high"):

        if not self._is_downloadable:
            secho(
                f"{self.full_title} is not downloadable. Skip item.",
                fg="red"
            )
            return

        codec, codec_name = self._get_codec(quality)
        if codec is None:
            secho(
                f"{self.full_title} is not downloadable. No AAX codec found.",
                fg="red"
            )
            return

        domain = self._client.auth.locale.domain
        url = f"https://www.audible.{domain}/library/download"
        params = {
            "asin": self.asin,
            "codec": codec
        }
        return httpx.URL(url, params=params), codec_name

    async def get_aaxc_url(self, quality: str = "high"):
        assert quality in ("best", "high", "normal",)

        body = {
            "supported_drm_types": ["Mpeg", "Adrm"],
            "quality": "Extreme" if quality in ("best", "high") else "Normal",
            "consumption_type": "Download",
            "response_groups": (
                "last_position_heard, pdf_url, content_reference, chapter_info"
            )
        }

        lr = await self._client.post(
            f"content/{self.asin}/licenserequest",
            body=body
        )

        content_metadata = lr["content_license"]["content_metadata"]
        url = httpx.URL(content_metadata["content_url"]["offline_url"])
        codec = content_metadata["content_reference"]["content_format"]

        voucher = decrypt_voucher_from_licenserequest(self._client.auth, lr)
        lr["content_license"]["license_response"] = voucher

        return url, codec, lr

    async def get_content_metadata(self, quality: str = "high"):
        assert quality in ("best", "high", "normal",)

        url = f"content/{self.asin}/metadata"
        params = {
            "response_groups": "last_position_heard, content_reference, "
                               "chapter_info",
            "quality": "Extreme" if quality in ("best", "high") else "Normal",
            "drm_type": "Adrm"
        }

        metadata = await self._client.get(url, params=params)

        return metadata


class WishlistItem(BaseItem):
    pass


class BaseList:
    def __init__(
            self,
            data: Union[dict, list],
            api_client: audible.AsyncClient
    ):
        self._client = api_client
        self._data = self._prepare_data(data)

    def __iter__(self):
        return iter(self._data)

    def _prepare_data(self, data: Union[dict, list]) -> Union[dict, list]:
        return data

    def get_item_by_asin(self, asin):
        try:
            return next(i for i in self._data if asin in i.asin)
        except StopIteration:
            return None

    def has_asin(self, asin):
        return True if self.get_item_by_asin(asin) else False

    def search_item_by_title(self, search_title, p=80):
        match = []
        for i in self._data:
            accuracy = i.substring_in_title_accuracy(search_title)
            match.append([i, accuracy]) if accuracy >= p else ""

        return match


class Library(BaseList):
    def _prepare_data(self, data: Union[dict, list]) -> list:
        if isinstance(data, dict):
            data = data.get("items", data)
        data = [
            LibraryItem(data=i, api_client=self._client) for i in data
        ]
        return data

    def __iter__(self):
        return iter(self._data)

    @classmethod
    async def get_from_api(
            cls,
            api_client: audible.AsyncClient,
            **request_params
    ):

        if "response_groups" not in request_params:
            request_params["response_groups"] = (
                "contributors, customer_rights, media, price, product_attrs, "
                "product_desc, product_extended_attrs, product_plan_details, "
                "product_plans, rating, sample, sku, series, reviews, ws4v, "
                "origin, relationships, review_attrs, categories, "
                "badge_types, category_ladders, claim_code_url, in_wishlist, "
                "is_archived, is_downloaded, is_finished, is_playable, "
                "is_removable, is_returnable, is_visible, listening_status, "
                "order_details, origin_asin, pdf_url, percent_complete, "
                "periodicals, provided_review, product_details"
            )

        async def fetch_library(params):
            entire_lib = False
            if "page" not in params and "num_results" not in params:
                entire_lib = True
                params["page"] = 1
                num_results = 1000
                params["num_results"] = num_results

            library = []
            while True:
                r = await api_client.get("library", params=params)
                items = r["items"]
                len_items = len(items)
                library.extend(items)
                if not entire_lib or len_items < num_results:
                    break
                params["page"] += 1
            return library

        resp = await fetch_library(request_params)

        return cls(resp, api_client=api_client)


class Wishlist(BaseList):
    def _prepare_data(self, data: Union[dict, list]) -> list:
        if isinstance(data, dict):
            data = data.get("products", data)
        data = [
            WishlistItem(data=i, api_client=self._client) for i in data
        ]
        return data

    @classmethod
    async def get_from_api(
            cls,
            api_client: audible.AsyncClient,
            **request_params
    ):

        async def fetch_wishlist(params):
            entire_lib = False
            if "page" not in params and "num_results" not in params:
                entire_lib = False
                params["page"] = 0
                num_results = 50
                params["num_results"] = num_results

            wishlist = []
            while True:
                r = await api_client.get("wishlist", params=params)
                items = r["products"]
                len_items = len(items)
                wishlist.extend(items)
                if not entire_lib or len_items < num_results:
                    break
                params["page"] += 1
            return wishlist

        resp = await fetch_wishlist(request_params)

        return cls(resp, api_client=api_client)

