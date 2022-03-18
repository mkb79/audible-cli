import asyncio
import logging
import string
import unicodedata
from typing import List, Optional, Union

import audible
import httpx
from audible.aescipher import decrypt_voucher_from_licenserequest


from .constants import CODEC_HIGH_QUALITY, CODEC_NORMAL_QUALITY
from .exceptions import AudibleCliException
from .utils import LongestSubString


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

    def is_parent_podcast(self):
        if "content_delivery_type" in self and "content_type" in self:
            if (self.content_delivery_type in ("Periodical", "PodcastParent")
                    or self.content_type == "Podcast") and self.has_children:
                return True


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
        # MultiPartBook or Periodical have child elemts
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
            if not self.customer_rights["is_consumable_offline"]:
                return False
            else:
                return True

    async def get_aax_url_old(self, quality: str = "high"):
        if not self.is_downloadable():
            raise AudibleCliException(
                f"{self.full_title} is not downloadable. Skip item."
            )

        codec, codec_name = self._get_codec(quality)
        if codec is None:
            raise AudibleCliException(
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

        if not self.is_downloadable():
            raise AudibleCliException(
                f"{self.full_title} is not downloadable. Skip item."
            )

        codec, codec_name = self._get_codec(quality)
        if codec is None:
            raise AudibleCliException(
                f"{self.full_title} is not downloadable in AAX format"
            )

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

    def __len__(self):
        return len(self._data)

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

        resp = await api_client.get("library", **request_params)
        return cls(resp, api_client=api_client)

    @classmethod
    async def from_api_full_sync(
            cls,
            api_client: audible.AsyncClient,
            bunch_size: int = 1000,
            **request_params
    ) -> "Library":
        request_params["page"] = 1
        request_params["num_results"] = bunch_size

        library = []
        while True:
            resp = await cls.from_api(api_client, params=request_params)
            items = resp._data
            len_items = len(items)
            library.extend(items)
            if len_items < bunch_size:
                break
            request_params["page"] += 1

        resp._data = library
        return resp

    async def resolve_podcats(self):
        podcasts = []
        for i in self:
            if i.is_parent_podcast():
                podcasts.append(i)

        podcast_items = await asyncio.gather(
            *[i.get_child_items() for i in podcasts]
        )
        for i in podcast_items:
            self._data.extend(i._data)


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
        podcasts = []
        for i in self:
            if i.is_parent_podcast():
                podcasts.append(i)

        podcast_items = await asyncio.gather(
            *[i.get_child_items() for i in podcasts]
        )
        for i in podcast_items:
            self._data.extend(i._data)


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
