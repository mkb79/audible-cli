import json
import pathlib
import string
import unicodedata

import aiofiles
import click
import httpx
import tqdm
from audible.aescipher import decrypt_voucher_from_licenserequest
from audible.client import AsyncClient
from click import secho

from .utils import LongestSubString


CLIENT_TIMEOUT = 15
CODEC_HIGH_QUALITY = "LC_128_44100_stereo"
CODEC_NORMAL_QUALITY = "LC_64_44100_stereo"


async def download_content(client, url, output_dir, filename,
                           overwrite_existing=False):
    output_dir = pathlib.Path(output_dir)

    if not output_dir.is_dir():
        raise Exception("Output dir doesn't exists")

    file = output_dir / filename
    tmp_file = file.with_suffix(".tmp")        

    if file.exists() and not overwrite_existing:
        secho(f"File {file} already exists. Skip download.", fg="red")
        return True

    try:
        async with client.stream("GET", url) as r:
            length = int(r.headers["Content-Length"])

            progressbar = tqdm.tqdm(
                desc=filename, total=length, unit='B', unit_scale=True,
                unit_divisor=1024
            )

            try:
                with progressbar:
                    async with aiofiles.open(tmp_file, mode="wb") as f:
                    #with progressbar, tmp_file.open("wb") as f:
                        async for chunk in r.aiter_bytes():
                            await f.write(chunk)
                            progressbar.update(len(chunk))

                if file.exists() and overwrite_existing:
                    i = 0
                    while file.with_suffix(f"{file.suffix}.old.{i}").exists():
                        i += 1
                    file.rename(file.with_suffix(f"{file.suffix}.old.{i}"))
                tmp_file.rename(file)
                tqdm.tqdm.write(f"File {file} downloaded to {output_dir} in {r.elapsed}")
                return True
            finally:
                # remove tmp_file if download breaks
                tmp_file.unlink() if tmp_file.exists() else ""
    except KeyError as e:
        secho(f"An error occured during downloading {file}", fg="red")
        return False


class LibraryItem:
    def __init__(self, item, api_client, client):
        self._data = item
        self._api_client = api_client
        self._client = client

    def __getitem__(self, key):
        return self._data[key]

    def __getattr__(self, attr):
        try:
            return self.__getitem__(attr)
        except KeyError:
            return None

    def __iter__(self):
        return iter(self._data)

    @property
    def full_title(self):
        return self.title + (f": {self.subtitle}" if self.subtitle else "")

    @property
    def full_title_slugify(self):
        valid_chars = f"-_.() " + string.ascii_letters + string.digits
        cleaned_title = unicodedata.normalize('NFKD', self.full_title)\
                        .encode('ASCII', 'ignore').replace(b" ", b"_")
        return "".join(chr(c) for c in cleaned_title if chr(c) in valid_chars)

    def substring_in_title_accuracy(self, substring):
        match = LongestSubString(substring, self.full_title)
        return round(match.percentage, 2)

    def substring_in_title(self, substring, p=100):
        accuracy = self.substring_in_title_accuracy(substring)
        return accuracy >= p

    async def get_cover(self, output_dir, overwrite_existing=False):
        url = self.product_images.get("500")
        if url is None:
            # TODO: no cover
            return

        filename = self.full_title_slugify + ".jpg"
        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)

    @property
    def has_pdf(self):
        return self.pdf_url is not None

    async def get_pdf_url(self):
        # something is broken getting with pdf url getting from api response
        # missing credentials in pdf url link
        # this working for me
        tld = self._client.auth.locale.domain
        r = await self._client.head(
            f"https://www.audible.{tld}/companion-file/{self.asin}")
        return r.url

    async def get_pdf(self, output_dir, overwrite_existing=False):
        if not self.has_pdf:
        # TODO: no pdf
            return

        #url = self.pdf_url
        url = await self.get_pdf_url()

        filename = self.full_title_slugify + ".pdf"
        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)

    async def get_download_link(self, codec):
        if self._client.auth.adp_token is None:
            ctx = click.get_current_context()
            ctx.fail("No adp token present. Can't get download link.")
    
        try:
            content_url = ("https://cde-ta-g7g.amazon.com/FionaCDEServiceEngine/"
                           "FSDownloadContent")
            params = {
                'type': 'AUDI',
                'currentTransportMethod': 'WIFI',
                'key': self.asin,
                'codec': codec
            }
            r = await self._client.head(
                url=content_url,
                params=params,
                allow_redirects=False)
    
            # prepare link
            # see https://github.com/mkb79/Audible/issues/3#issuecomment-518099852
            link = r.headers['Location']
            tld = self._client.auth.locale.domain
            new_link = link.replace("cds.audible.com", f"cds.audible.{tld}")
            return new_link
        except Exception as e:
            secho(f"Error: {e} occured. Can't get download link. Skip asin {self.asin}")
            return None

    def get_quality(self, verify=None):
        """If verify is set, ensures the given quality is present in the
        codecs list. Otherwise, will find the best aax quality available
        """
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
    def is_downloadable(self):
        if self.content_delivery_type in ("Periodical",):
            return False

        return True        

    async def get_audiobook(self, output_dir, quality="high",
                            overwrite_existing=False):
        if not self.is_downloadable:
            secho(f"{self.full_title} is not downloadable. Skip item.", fg="red")
            return

        assert quality in ("best", "high", "normal",)
        if quality == "best":
            codec = self.get_quality()
        else:
            codec = self.get_quality(
                CODEC_HIGH_QUALITY if quality == "high" else CODEC_NORMAL_QUALITY
            )

        url = await self.get_download_link(codec)
        if not url:
        # TODO: no link
            return

        filename = self.full_title_slugify + f"-{codec}.aax"
        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)

    async def get_audiobook_aaxc(self, output_dir, quality="high",
                                 overwrite_existing=False):

        assert quality in ("best", "high", "normal",)
        body = {
            "supported_drm_types" : ["Mpeg", "Adrm"],
            "quality" : "Extreme" if quality in ("best", "high") else "Normal",
            "consumption_type" : "Download",
            "response_groups" : "last_position_heard, pdf_url, content_reference, chapter_info"
        }
        try:
            license_response = await self._api_client.post(
                f"content/{self.asin}/licenserequest",
                body=body
            )
        except Exception as e:
            raise e

        url = license_response["content_license"]["content_metadata"]["content_url"]["offline_url"]
        codec = license_response["content_license"]["content_metadata"]["content_reference"]["content_format"]
        voucher = decrypt_voucher_from_licenserequest(self._api_client.auth, license_response)

        filename = self.full_title_slugify + f"-{codec}.aaxc"
        voucher_file = (pathlib.Path(output_dir) / filename).with_suffix(".voucher")
        voucher_file.write_text(json.dumps(voucher, indent=4))
        tqdm.tqdm.write(f"Voucher file saved to {voucher_file}.")

        await download_content(client=self._client, url=url,
                               output_dir=output_dir, filename=filename,
                               overwrite_existing=overwrite_existing)

    async def get_chapter_informations(self, output_dir, quality="high",
                                       overwrite_existing=False):
        assert quality in ("best", "high", "normal",)

        try:
            chapter_informations = await self._api_client.get(
                f"content/{self.asin}/metadata",
                response_groups="chapter_info",
                quality="Extreme" if quality in ("best", "high") else "Normal",
                drm_type="Adrm"
            )
        except Exception as e:
            raise e

        filename = self.full_title_slugify + "-chapters.json"
        output_dir = pathlib.Path(output_dir)

        if not output_dir.is_dir():
            raise Exception("Output dir doesn't exists")

        file = output_dir / filename
        if file.exists() and not overwrite_existing:
            secho(f"File {file} already exists. Skip saving chapters.", fg="red")
            return True
        file.write_text(json.dumps(chapter_informations, indent=4))
        tqdm.tqdm.write(f"Chapter file saved to {file}.")


class Library:
    def __init__(self, library, api_client):
        self._api_client = api_client
        self._client = httpx.AsyncClient(timeout=CLIENT_TIMEOUT,
                                         auth=api_client.auth)

        self._data = [LibraryItem(i, self._api_client, self._client) \
                      for i in library.get("items") or library]

    def __iter__(self):
        return iter(self._data)

    @classmethod
    async def get_from_api(cls, auth, **params):
        api_client = AsyncClient(auth, timeout=CLIENT_TIMEOUT)
        async with api_client as client:
            library = await client.get("library", params=params)

        return cls(library, api_client)

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
