import asyncio
import logging
import secrets
import string
import unicodedata
from datetime import datetime
from math import ceil
from typing import List, Optional, Union

import audible
import httpx
from audible.aescipher import decrypt_voucher_from_licenserequest
from audible.client import convert_response_content

from .constants import CODEC_HIGH_QUALITY, CODEC_NORMAL_QUALITY
from .exceptions import (
    AudibleCliException,
    LicenseDenied,
    NoDownloadUrl,
    NotDownloadableAsAAX,
    ItemNotPublished
)
from .utils import full_response_callback, LongestSubString


logger = logging.getLogger("audible_cli.models")


class BaseItem:
    def __init__(
            self,
            data: dict,
            api_client: audible.AsyncClient,
            parent: Optional["BaseItem"] = None,
            response_groups: Optional[List] = None
    ) -> None:
        self._data = self._prepare_data(data)
        self._client = api_client
        self._parent = parent
        self._response_groups = response_groups
        self._children: Optional[BaseList] = None

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

        if self._parent is not None:
            title = f"{self._parent.title}: {title}"

        return title

    @property
    def full_title_slugify(self):
        valid_chars = "-_.() " + string.ascii_letters + string.digits
        cleaned_title = unicodedata.normalize("NFKD", self.full_title or "")
        cleaned_title = cleaned_title.encode("ASCII", "ignore")
        cleaned_title = cleaned_title.replace(b" ", b"_")
        slug_title = "".join(
            chr(c) for c in cleaned_title if chr(c) in valid_chars
        )

        if len(slug_title) < 2:
            return self.asin

        return slug_title

    def create_base_filename(self, mode: str):
        supported_modes = ("ascii", "asin_ascii", "unicode", "asin_unicode")
        if mode not in supported_modes:
            raise AudibleCliException(
                f"Unsupported mode {mode} for name creation"
            )

        if "ascii" in mode:
            base_filename = self.full_title_slugify

        elif "unicode" in mode:
            base_filename = unicodedata.normalize("NFKD", self.full_title or "")

        else:
            base_filename = self.asin

        if "asin" in mode:
            base_filename = self.asin + "_" + base_filename

        return base_filename

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
        if not self.is_published():
            raise ItemNotPublished(self.asin, self.publication_datetime)

        if self.pdf_url is not None:
            domain = self._client.auth.locale.domain
            return f"https://www.audible.{domain}/companion-file/{self.asin}"

    def is_parent_podcast(self):
        if "content_delivery_type" in self and "content_type" in self:
            if (self.content_delivery_type in ("Periodical", "PodcastParent")
                    or self.content_type == "Podcast") and self.has_children:
                return True

    def is_published(self):
        if self.publication_datetime is not None:
            pub_date = datetime.strptime(
                self.publication_datetime, "%Y-%m-%dT%H:%M:%SZ"
            )
            now = datetime.utcnow()
            return now > pub_date


class LibraryItem(BaseItem):
    def _prepare_data(self, data: dict) -> dict:
        return data.get("item", data)

    def _get_codec(self, quality: str):
        """If quality is not ``best``, ensures the given quality is present in
        them codecs list. Otherwise, will find the best aax quality available
        """
        assert quality in ("best", "high", "normal",)

        # if available_codecs is None the item can't be downloaded as aax
        if self.available_codecs is None:
            return None, None

        verify = None
        if quality != "best":
            verify = CODEC_HIGH_QUALITY if quality == "high" else \
                CODEC_NORMAL_QUALITY

        best = (None, 0, 0, None)
        for codec in self.available_codecs:
            if verify is not None and verify == codec["name"].upper():
                return verify, codec["enhanced_codec"]

            if codec["name"].startswith("aax_"):
                name = codec["name"]
                try:
                    sample_rate, bitrate = name[4:].split("_")
                    sample_rate = int(sample_rate)
                    bitrate = int(bitrate)
                    if sample_rate > best[1] or bitrate > best[2]:
                        best = (
                            codec["name"].upper(),
                            sample_rate,
                            bitrate,
                            codec["enhanced_codec"]
                        )

                except ValueError:
                    logger.warning(f"Unexpected codec name: {name}")
                    continue

        if verify is not None:
            logger.info(f"{verify} codec was not found, using {best[0]} instead")

        return best[0], best[3]

    async def get_child_items(self, **request_params) -> Optional["Library"]:
        """Get child elements of MultiPartBooks and Podcasts
        
        With these all parts of a MultiPartBook or all episodes of a Podcasts
        can be shown.
        """

        # Only items with content_delivery_type 
        # MultiPartBook or Periodical have child elements
        if not self.has_children:
            return

        if "response_groups" not in request_params and \
                self._response_groups is not None:
            response_groups = ", ".join(self._response_groups)
            request_params["response_groups"] = response_groups

        request_params["parent_asin"] = self.asin
        children = await Library.from_api_full_sync(
            api_client=self._client,
            **request_params
        )

        if self.is_parent_podcast() and "episode_count" in self and \
                self.episode_count is not None:
            if int(self.episode_count) != len(children):

                if "response_groups" in request_params:
                    request_params.pop("response_groups")
                children = await Catalog.from_api(
                    api_client=self._client,
                    **request_params
                )

        for child in children:
            child._parent = self

        self._children = children

        return children

    def is_downloadable(self):
        # customer_rights must be in response_groups
        if self.customer_rights is not None:
            if self.customer_rights["is_consumable_offline"]:
                return True
            return False

    async def get_aax_url_old(self, quality: str = "high"):
        if not self.is_published():
            raise ItemNotPublished(self.asin, self.publication_datetime)

        if not self.is_downloadable():
            raise AudibleCliException(
                f"{self.full_title} is not downloadable."
            )

        codec, codec_name = self._get_codec(quality)
        if codec is None or self.is_ayce:
            raise NotDownloadableAsAAX(
                f"{self.full_title} is not downloadable in AAX format"
            )

        url = (
            "https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/"
            "FSDownloadContent"
        )
        params = {
           "type": "AUDI",
           "currentTransportMethod": "WIFI",
           "key": self.asin,
           "codec": codec_name
        }
        r = await self._client.session.head(url, params=params)

        try:
            link = r.headers["location"]
            api_url = self._client._api_url
            domain = str(api_url)[20:]
            link = link.replace("cds.audible.com", f"cds.audible.{domain}")
        except Exception as e:
            raise AudibleCliException(
                f"Can not get download url for asin {self.asin} with message {e}"
            )

        return httpx.URL(link), codec_name

    async def get_aax_url(self, quality: str = "high"):
        if not self.is_published():
            raise ItemNotPublished(self.asin, self.publication_datetime)

        if not self.is_downloadable():
            raise AudibleCliException(
                f"{self.full_title} is not downloadable. Skip item."
            )

        codec, codec_name = self._get_codec(quality)
        if codec is None or self.is_ayce:
            raise NotDownloadableAsAAX(
                f"{self.full_title} is not downloadable in AAX format"
            )

        domain = self._client.auth.locale.domain
        url = f"https://www.audible.{domain}/library/download"
        params = {
            "asin": self.asin,
            "codec": codec
        }
        return httpx.URL(url, params=params), codec_name

    async def get_aaxc_url(
            self,
            quality: str = "high",
            license_response_groups: Optional[str] = None
    ):
        if not self.is_published():
            raise ItemNotPublished(self.asin, self.publication_datetime)

        if not self.is_downloadable():
            raise AudibleCliException(
                f"{self.full_title} is not downloadable."
            )

        lr = await self.get_license(quality, license_response_groups)

        content_metadata = lr["content_license"]["content_metadata"]
        url = httpx.URL(content_metadata["content_url"]["offline_url"])
        codec = content_metadata["content_reference"]["content_format"]

        return url, codec, lr

    async def get_license(
            self,
            quality: str = "high",
            response_groups: Optional[str] = None
    ):
        assert quality in ("best", "high", "normal",)

        if response_groups is None:
            response_groups = "last_position_heard, pdf_url, content_reference"

        body = {
            "supported_drm_types": ["Mpeg", "Adrm"],
            "quality": "High" if quality in ("best", "high") else "Normal",
            "consumption_type": "Download",
            "response_groups": response_groups
        }

        headers = {
            "X-Amzn-RequestId": secrets.token_hex(20).upper(),
            "X-ADP-SW": "37801821",
            "X-ADP-Transport": "WIFI",
            "X-ADP-LTO": "120",
            "X-Device-Type-Id": "A2CZJZGLK2JJVM",
            "device_idiom": "phone"
        }
        lr = await self._client.post(
            f"content/{self.asin}/licenserequest",
            body=body,
            headers=headers
        )
        content_license = lr["content_license"]

        if content_license["status_code"] == "Denied":
            if "license_denial_reasons" in content_license:
                for reason in content_license["license_denial_reasons"]:
                    message = reason.get("message", "UNKNOWN")
                    rejection_reason = reason.get("rejectionReason", "UNKNOWN")
                    validation_type = reason.get("validationType", "UNKNOWN")
                    logger.debug(
                        f"License denied message for {self.asin}: {message}."
                        f"Reason: {rejection_reason}."
                        f"Type: {validation_type}"
                    )

            msg = content_license["message"]
            raise LicenseDenied(msg)

        content_url = content_license["content_metadata"]\
            .get("content_url", {}).get("offline_url")
        if content_url is None:
            raise NoDownloadUrl(self.asin)

        if "license_response" in content_license:
            try:
                voucher = decrypt_voucher_from_licenserequest(
                    self._client.auth, lr
                )
            except Exception:
                logger.error(f"Decrypting voucher for  {self.asin} failed")
            else:
                content_license["license_response"] = voucher
        else:
            logger.error(f"No voucher for {self.asin} found")

        return lr

    async def get_content_metadata(self, quality: str = "high"):
        assert quality in ("best", "high", "normal",)

        url = f"content/{self.asin}/metadata"
        params = {
            "response_groups": "last_position_heard, content_reference, "
                               "chapter_info",
            "quality": "High" if quality in ("best", "high") else "Normal",
            "drm_type": "Adrm"
        }

        metadata = await self._client.get(url, params=params)

        return metadata

    async def get_annotations(self):
        url = f"https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/sidecar"
        params = {
            "type": "AUDI",
            "key": self.asin
        }

        annotations = await self._client.get(url, params=params)

        return annotations


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

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def _prepare_data(self, data: Union[dict, list]) -> Union[dict, list]:
        return data

    @property
    def data(self):
        return self._data

    def get_item_by_asin(self, asin):
        try:
            return next(i for i in self._data if asin == i.asin)
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
        response_groups = None
        if isinstance(data, dict):
            response_groups = data.get("response_groups")
            if isinstance(response_groups, str):
                response_groups = response_groups.replace(" ", "").split(",")
            data = data.get("items", data)
        data = [
            LibraryItem(
                data=i,
                api_client=self._client,
                response_groups=response_groups
            ) for i in data
        ]
        return data

    @classmethod
    async def from_api(
            cls,
            api_client: audible.AsyncClient,
            include_total_count_header: bool = False,
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

        resp: httpx.Response = await api_client.get(
            "library",
            response_callback=full_response_callback,
            **request_params
        )
        resp_content = convert_response_content(resp)
        total_count_header = resp.headers.get("total-count")
        cls_instance = cls(resp_content, api_client=api_client)

        if include_total_count_header:
            return cls_instance, total_count_header
        return cls_instance

    @classmethod
    async def from_api_full_sync(
            cls,
            api_client: audible.AsyncClient,
            bunch_size: int = 1000,
            **request_params
    ) -> "Library":
        request_params.pop("page", None)
        request_params["num_results"] = bunch_size

        library, total_count = await cls.from_api(
            api_client,
            page=1,
            include_total_count_header=True,
            **request_params
        )
        pages = ceil(int(total_count) / bunch_size)
        if pages == 1:
            return library

        additional_pages = []
        for page in range(2, pages+1):
            additional_pages.append(
                cls.from_api(
                    api_client,
                    page=page,
                    **request_params
                )
            )

        additional_pages = await asyncio.gather(*additional_pages)

        for p in additional_pages:
            library.data.extend(p.data)

        return library

    async def resolve_podcats(self):
        podcast_items = await asyncio.gather(
            *[i.get_child_items() for i in self if i.is_parent_podcast()]
        )
        for i in podcast_items:
            self.data.extend(i.data)


class Catalog(BaseList):
    def _prepare_data(self, data: Union[dict, list]) -> list:
        response_groups = None
        if isinstance(data, dict):
            response_groups = data.get("response_groups")
            response_groups = response_groups.replace(" ", "").split(",")
            data = data.get("products", data)
        data = [
            LibraryItem(
                data=i,
                api_client=self._client,
                response_groups=response_groups
            ) for i in data
        ]
        return data

    @classmethod
    async def from_api(
            cls,
            api_client: audible.AsyncClient,
            **request_params
    ):

        if "response_groups" not in request_params:
            request_params["response_groups"] = (
                "contributors, customer_rights, media, price, product_attrs, "
                "product_desc, product_extended_attrs, product_plan_details, "
                "product_plans, rating, sample, sku, series, reviews, ws4v, "
                "relationships, review_attrs, categories, category_ladders, "
                "claim_code_url, in_wishlist, listening_status, periodicals, "
                "provided_review, product_details"
            )

        async def fetch_catalog(params):
            entire_lib = False
            if "page" not in params and "num_results" not in params:
                entire_lib = True
                params["page"] = 0
                num_results = 50
                params["num_results"] = num_results

            catalog = []
            while True:
                r = await api_client.get("catalog/products", **params)
                items = r["products"]
                len_items = len(items)
                catalog.extend(items)
                if not entire_lib or len_items < num_results:
                    break
                params["page"] += 1
            return catalog

        resp = await fetch_catalog(request_params)

        return cls(resp, api_client=api_client)

    async def resolve_podcats(self):
        podcast_items = await asyncio.gather(
            *[i.get_child_items() for i in self if i.is_parent_podcast()]
        )
        for i in podcast_items:
            self.data.extend(i.data)


class Wishlist(BaseList):
    def _prepare_data(self, data: Union[dict, list]) -> list:
        response_groups = None
        if isinstance(data, dict):
            response_groups = data.get("response_groups")
            response_groups = response_groups.replace(" ", "").split(",")
            data = data.get("products", data)
        data = [
            WishlistItem(
                data=i,
                api_client=self._client,
                response_groups=response_groups
            ) for i in data
        ]
        return data

    @classmethod
    async def from_api(
            cls,
            api_client: audible.AsyncClient,
            **request_params
    ):

        async def fetch_wishlist(params):
            entire_lib = False
            if "page" not in params and "num_results" not in params:
                entire_lib = True
                params["page"] = 0
                num_results = 50
                params["num_results"] = num_results

            wishlist = []
            while True:
                r = await api_client.get("wishlist", **params)
                items = r["products"]
                len_items = len(items)
                wishlist.extend(items)
                if not entire_lib or len_items < num_results:
                    break
                params["page"] += 1
            return wishlist

        resp = await fetch_wishlist(request_params)

        return cls(resp, api_client=api_client)
