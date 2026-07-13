"""Removes encryption of aax and aaxc files.

This is a proof-of-concept and for testing purposes only.

No error handling.
Need further work. Some options do not work or options are missing.

Needs at least ffmpeg 4.4
"""


import base64
import concurrent.futures
import io
import json
import logging
import operator
import os
import pathlib
import re
import struct
import subprocess  # noqa: S404
import sys
import tempfile
import traceback
import typing as t
import unicodedata
from enum import Enum
from functools import reduce
from glob import glob
from shutil import which

import click
from click import echo, secho
from PIL import Image

from audible_cli.decorators import pass_session
from audible_cli.exceptions import AudibleCliException

logger = logging.getLogger()

class ChapterError(AudibleCliException):
    """Base class for all chapter errors."""


class SupportedFiles(Enum):
    AAX = ".aax"
    AAXC = ".aaxc"

    @classmethod
    def get_supported_list(cls):
        return list(set(item.value for item in cls))

    @classmethod
    def is_supported_suffix(cls, value):
        return value in cls.get_supported_list()

    @classmethod
    def is_supported_file(cls, value):
        return pathlib.PurePath(value).suffix in cls.get_supported_list()


def _get_input_files(
    files: t.Union[t.Tuple[str], t.List[str]],
    recursive: bool = True
) -> t.List[pathlib.Path]:
    filenames = []
    for filename in files:
        # if the shell does not do filename globbing
        expanded = list(glob(filename, recursive=recursive))

        if (
            len(expanded) == 0
            and '*' not in filename
            and not SupportedFiles.is_supported_file(filename)
        ):
            raise click.BadParameter("{filename}: file not found or supported.")

        expanded_filter = filter(
            lambda x: SupportedFiles.is_supported_file(x), expanded
        )
        expanded = list(map(lambda x: pathlib.Path(x).resolve(), expanded_filter))
        filenames.extend(expanded)

    return filenames


def recursive_lookup_dict(key: str, dictionary: t.Dict[str, t.Any]) -> t.Any:
    if key in dictionary:
        return dictionary[key]
    for value in dictionary.values():
        if isinstance(value, dict):
            try:
                item = recursive_lookup_dict(key, value)
            except KeyError:
                continue
            else:
                return item
            
    raise KeyError


def get_aaxc_credentials(voucher_file: pathlib.Path):
    if not voucher_file.exists() or not voucher_file.is_file():
        raise AudibleCliException(f"Voucher file {voucher_file} not found.")

    voucher_dict = json.loads(voucher_file.read_text())
    try:
        key = recursive_lookup_dict("key", voucher_dict)
        iv = recursive_lookup_dict("iv", voucher_dict)
    except KeyError:
        raise AudibleCliException(f"No key/iv found in file {voucher_file}.") from None

    return key, iv


class ApiChapterInfo:
    def __init__(self, content_metadata: t.Dict[str, t.Any]) -> None:
        chapter_info = self._parse(content_metadata)
        self._chapter_info = chapter_info

    @classmethod
    def from_file(cls, file: t.Union[pathlib.Path, str]) -> "ApiChapterInfo":
        file = pathlib.Path(file)
        if not file.exists() or not file.is_file():
            raise ChapterError(f"Chapter file {file} not found.")
        content_string = pathlib.Path(file).read_text("utf-8")
        content_json = json.loads(content_string)
        return cls(content_json)

    @staticmethod
    def _parse(content_metadata: t.Dict[str, t.Any]) -> t.Dict[str, t.Any]:
        if "chapters" in content_metadata:
            return content_metadata

        try:
            return recursive_lookup_dict("chapter_info", content_metadata)
        except KeyError:
            raise ChapterError("No chapter info found.") from None

    def count_chapters(self):
        return len(self.get_chapters())

    def get_chapters(self, separate_intro_outro=False, remove_intro_outro=False):
        def extract_chapters(initial, current):
            if "chapters" in current:
                return initial + [current] + current["chapters"]
            else:
                return initial + [current]

        chapters = list(
            reduce(
                extract_chapters,
                self._chapter_info["chapters"],
                [],
            )
        )

        if separate_intro_outro:
            return self._separate_intro_outro(chapters)
        elif remove_intro_outro:
            return self._remove_intro_outro(chapters)

        return chapters

    def get_intro_duration_ms(self):
        return self._chapter_info["brandIntroDurationMs"]

    def get_outro_duration_ms(self):
        return self._chapter_info["brandOutroDurationMs"]

    def get_runtime_length_ms(self):
        return self._chapter_info["runtime_length_ms"]

    def is_accurate(self):
        return self._chapter_info["is_accurate"]

    def _separate_intro_outro(self, chapters):
        echo("Separate Audible Brand Intro and Outro to own Chapter.")
        chapters.sort(key=operator.itemgetter("start_offset_ms"))
    
        first = chapters[0]
        intro_dur_ms = self.get_intro_duration_ms()
        first["start_offset_ms"] = intro_dur_ms
        first["start_offset_sec"] = round(first["start_offset_ms"] / 1000)
        first["length_ms"] -= intro_dur_ms
    
        last = chapters[-1]
        outro_dur_ms = self.get_outro_duration_ms()
        last["length_ms"] -= outro_dur_ms
    
        chapters.append(
            {
                "length_ms": intro_dur_ms,
                "start_offset_ms": 0,
                "start_offset_sec": 0,
                "title": "Intro",
            }
        )
        chapters.append(
            {
                "length_ms": outro_dur_ms,
                "start_offset_ms": self.get_runtime_length_ms() - outro_dur_ms,
                "start_offset_sec": round(
                    (self.get_runtime_length_ms() - outro_dur_ms) / 1000
                ),
                "title": "Outro",
            }
        )
        chapters.sort(key=operator.itemgetter("start_offset_ms"))
    
        return chapters

    def _remove_intro_outro(self, chapters):
        echo("Delete Audible Brand Intro and Outro.")
        chapters.sort(key=operator.itemgetter("start_offset_ms"))
    
        intro_dur_ms = self.get_intro_duration_ms()
        outro_dur_ms = self.get_outro_duration_ms()

        first = chapters[0]
        first["length_ms"] -= intro_dur_ms

        for chapter in chapters[1:]:
            chapter["start_offset_ms"] -= intro_dur_ms
            chapter["start_offset_sec"] -= round(chapter["start_offset_ms"] / 1000)

        last = chapters[-1]
        last["length_ms"] -= outro_dur_ms
    
        return chapters

class FFMeta:
    SECTION = re.compile(r"\[(?P<header>[^]]+)\]")
    OPTION = re.compile(r"(?P<option>.*?)\s*(?:(?P<vi>=)\s*(?P<value>.*))?$")

    def __init__(self, ffmeta_file: t.Union[str, pathlib.Path]) -> None:
        self._ffmeta_raw = pathlib.Path(ffmeta_file).read_text("utf-8")
        self._ffmeta_parsed = self._parse_ffmeta()

    def _parse_ffmeta(self):
        parsed_dict = {}
        start_section = "_"
        cursec = parsed_dict[start_section] = {}
        num_chap = 0

        for line in iter(self._ffmeta_raw.splitlines()):
            mo = self.SECTION.match(line)
            if mo:
                sec_name = mo.group("header")
                if sec_name == "CHAPTER":
                    num_chap += 1
                    if sec_name not in parsed_dict:
                        parsed_dict[sec_name] = {}
                    cursec = parsed_dict[sec_name][num_chap] = {}
                else:
                    cursec = parsed_dict[sec_name] = {}
            else:
                match = self.OPTION.match(line)
                cursec.update({match.group("option"): match.group("value")})

        return parsed_dict

    def _clean(self, text):
        text = re.sub(r'\([^)]*\)', ' ', text)
        text = re.sub(r':', ' - ', text)
        # Keep only Unicode letters, numbers, hyphen, underscore, space
        text = ''.join(
            c for c in text
            if unicodedata.category(c)[0] in ('L', 'N') or c in ('-', '_', ' ')
        )
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def get_output_path(self):
        """
        Return the <author>/<title>/ folder path for this audiobook
        """
        lines = self._ffmeta_raw.splitlines()
        title = ""
        artist = ""
        album = ""
        for line in lines:
            if not title and line.startswith("title="):
                title = line.split("=", 1)[-1]
            elif not artist and line.startswith("artist="):
                artist = line.split("=", 1)[-1]
            elif not album and line.startswith("album="):
                album = line.split("=", 1)[-1]
        title = self._clean(title)
        artist = self._clean(artist)
        album = self._clean(album)
        return pathlib.Path(f"{artist}/{album if album else title}")

    def count_chapters(self):
        return len(self._ffmeta_parsed["CHAPTER"])

    def set_chapter_option(self, num, option, value):
        chapter = self._ffmeta_parsed["CHAPTER"][num]
        for chapter_option in chapter:
            if chapter_option == option:
                chapter[chapter_option] = value

    def write(self, filename):
        fp = pathlib.Path(filename).open("w", encoding="utf-8")
        d = "="

        for section in self._ffmeta_parsed:
            if section == "_":
                self._write_section(fp, None, self._ffmeta_parsed[section], d)
            elif section == "CHAPTER":
                # TODO: Tue etwas
                for chapter in self._ffmeta_parsed[section]:
                    self._write_section(
                        fp, section, self._ffmeta_parsed[section][chapter], d
                    )
            else:
                self._write_section(fp, section, self._ffmeta_parsed[section], d)

    @staticmethod
    def _write_section(fp, section_name, section_items, delimiter):
        """Write a single section to the specified `fp`."""
        if section_name is not None:
            fp.write(f"[{section_name}]\n")

        for key, value in section_items.items():
            if value is None:
                fp.write(f"{key}\n")
            else:
                fp.write(f"{key}{delimiter}{value}\n")

    def update_chapters_from_chapter_info(
        self,
        chapter_info: ApiChapterInfo,
        force_rebuild_chapters: bool = False,
        separate_intro_outro: bool = False,
        remove_intro_outro: bool = False
    ) -> None:
        if not chapter_info.is_accurate():
            echo("Metadata from API is not accurate. Skip.")
            return

        if chapter_info.count_chapters() != self.count_chapters():
            if force_rebuild_chapters:
                echo("Force rebuild chapters due to chapter mismatch.")
            else:
                raise ChapterError("Chapter mismatch")

        echo(f"Found {chapter_info.count_chapters()} chapters to prepare.")

        api_chapters = chapter_info.get_chapters(separate_intro_outro, remove_intro_outro)

        num_chap = 0
        new_chapters = {}
        for chapter in api_chapters:
            chap_start = chapter["start_offset_ms"]
            chap_end = chap_start + chapter["length_ms"]
            num_chap += 1
            new_chapters[num_chap] = {
                "TIMEBASE": "1/1000",
                "START": chap_start,
                "END": chap_end,
                "title": chapter["title"],
            }
        self._ffmeta_parsed["CHAPTER"] = new_chapters
    
    def get_start_end_without_intro_outro(
        self,
        chapter_info: ApiChapterInfo,
    ):
        intro_dur_ms = chapter_info.get_intro_duration_ms()
        outro_dur_ms = chapter_info.get_outro_duration_ms()
        total_runtime_ms = chapter_info.get_runtime_length_ms()

        start_new = intro_dur_ms
        duration_new = total_runtime_ms - intro_dur_ms - outro_dur_ms

        return start_new, duration_new



def _get_voucher_filename(file: pathlib.Path) -> pathlib.Path:
    return file.with_suffix(".voucher")


def _get_chapter_filename(file: pathlib.Path) -> pathlib.Path:
    base_filename = file.stem.rsplit("-", 1)[0]
    return file.with_name(base_filename + "-chapters.json")


def _get_ffmeta_file(file: pathlib.Path, tempdir: pathlib.Path) -> pathlib.Path:
    metaname = file.with_suffix(".meta").name
    metafile = tempdir / metaname
    return metafile


class FfmpegFileDecrypter:
    def __init__(
        self,
        file: pathlib.Path,
        target_dir: pathlib.Path,
        tempdir: pathlib.Path,
        activation_bytes: t.Optional[str],
        overwrite: bool,
        rebuild_chapters: bool,
        force_rebuild_chapters: bool,
        skip_rebuild_chapters: bool,
        separate_intro_outro: bool,
        remove_intro_outro: bool,
        output_opus_format: bool,
        output_folders: bool,
        bitrate: str,
    ) -> None:
        file_type = SupportedFiles(file.suffix)

        credentials = None
        if file_type == SupportedFiles.AAX:
            if activation_bytes is None:
                raise AudibleCliException(
                    "No activation bytes found. Do you ever run "
                    "`audible activation-bytes`?"
                )
            credentials = activation_bytes
        elif file_type == SupportedFiles.AAXC:
            voucher_filename = _get_voucher_filename(file)
            credentials = get_aaxc_credentials(voucher_filename)

        self._source = file
        self._credentials: t.Optional[t.Union[str, t.Tuple[str]]] = credentials
        self._target_dir = target_dir
        self._tempdir = tempdir
        self._overwrite = overwrite
        self._rebuild_chapters = rebuild_chapters
        self._force_rebuild_chapters = force_rebuild_chapters
        self._skip_rebuild_chapters = skip_rebuild_chapters
        self._separate_intro_outro = separate_intro_outro
        self._remove_intro_outro = remove_intro_outro
        self._api_chapter: t.Optional[ApiChapterInfo] = None
        self._ffmeta: t.Optional[FFMeta] = None
        self._is_rebuilded: bool = False
        self._output_opus_format = output_opus_format
        self._output_folders = output_folders
        self._bitrate = bitrate
        self._title = ""

    @property
    def api_chapter(self) -> ApiChapterInfo:
        if self._api_chapter is None:
            try:
                voucher_filename = _get_voucher_filename(self._source)
                self._api_chapter = ApiChapterInfo.from_file(voucher_filename)
            except ChapterError:
                voucher_filename = _get_chapter_filename(self._source)
                self._api_chapter = ApiChapterInfo.from_file(voucher_filename)
            echo(f"Using chapters from {voucher_filename}")
        return self._api_chapter

    @property
    def ffmeta(self) -> FFMeta:
        if self._ffmeta is None:
            metafile = _get_ffmeta_file(self._source, self._tempdir)

            base_cmd = [
                "ffmpeg",
                "-v",
                "quiet",
            ]
            if isinstance(self._credentials, tuple):
                key, iv = self._credentials
                credentials_cmd = [
                    "-audible_key",
                    key,
                    "-audible_iv",
                    iv,
                ]
            else:
                credentials_cmd = [
                    "-activation_bytes",
                    self._credentials,
                ]    
            base_cmd.extend(credentials_cmd)
    
            extract_cmd = [
                "-i",
                str(self._source),
                "-f",
                "ffmetadata",
                str(metafile),
            ]
            base_cmd.extend(extract_cmd)

            subprocess.check_output(base_cmd, text=True)  # noqa: S603
            self._ffmeta = FFMeta(metafile)

        return self._ffmeta

    def rebuild_chapters(self) -> None:
        if not self._is_rebuilded:
            self.ffmeta.update_chapters_from_chapter_info(
                self.api_chapter, self._force_rebuild_chapters, self._separate_intro_outro, self._remove_intro_outro
            )
            self._is_rebuilded = True

    def get_downloaded_cover(self, filename: str) -> str:
        """
        Return the pathname to a cover already downloaded, if one exists
        """
        filename = pathlib.Path(filename)
        base_filename = filename.stem.rsplit("-", 1)[0]

        # Look for jpg files in the same directory as the input file
        parent_dir = filename.parent
        potential_covers = list(parent_dir.glob(f"{base_filename}*.jpg"))

        # If we found any matches, return the first one
        if potential_covers:
            return potential_covers[0]

    def get_cover_image(self, m4b_file_path: str):
        """
        Extract the cover image from an M4B audiobook file using ffmpeg.
        
        Args:
            m4b_file_path (str): Path to the M4B audiobook file.
        
        Returns:
            str: Path to the temporary file containing the cover image, or None if extraction failed.
        """
        # First see if there is an already downloaded cover
        try:
            cover_filename = self.get_downloaded_cover(m4b_file_path)
            if cover_filename:
                return cover_filename
        
            # Create a temporary file for the cover image
            temp_file = tempfile.NamedTemporaryFile(prefix="deleteme_", suffix='.jpg', delete=False)
            temp_file.close()
            output_image_path = temp_file.name
            
            # Run ffmpeg command to extract the cover image
            cmd = [
                'ffmpeg',
                '-v',
                'quiet',
                '-nostats',
                '-i', m4b_file_path,
                '-an',  # Disable audio
                '-vcodec', 'copy',
                '-y',  # Overwrite output file if it exists
                output_image_path
            ]
            
            # Run the command
            subprocess.run(cmd, capture_output=True, text=True)
            
            if os.path.exists(output_image_path) and os.path.getsize(output_image_path) > 0:
                return output_image_path
            else:
                # Alternative approach if the first one doesn't work
                cmd = [
                    'ffmpeg',
                    '-v',
                    'quiet',
                    '-stats',
                    '-i', m4b_file_path,
                    '-an',
                    '-vf', 'scale=500:-1',  # Resize the image (optional)
                    '-f', 'image2',
                    '-y',
                    output_image_path
                ]
                subprocess.run(cmd, capture_output=True, text=True)
                
                if os.path.exists(output_image_path) and os.path.getsize(output_image_path) > 0:
                    return output_image_path
                else:
                    # Clean up the empty temporary file
                    os.unlink(output_image_path)
                    return None
        
        except Exception:
            # Clean up in case of error
            if 'output_image_path' in locals() and os.path.exists(output_image_path):
                os.unlink(output_image_path)
            return None

    def create_picture_block_header(self, mime_type, description, width, height, color_depth, img_data_size):
        """
        Create a METADATA_BLOCK_PICTURE header according to the Xiph.org specification.

        Args:
        - mime_type: string like 'image/jpeg', 'image/png'
        - description: text description of the picture
        - width: image width in pixels
        - height: image height in pixels
        - color_depth: bits per pixel (typically 24 for RGB, 32 for RGBA)
        - img_data_size: size of the raw image data in bytes

        Returns:
        - binary header data
        """
        # Picture type: 3 means "Cover (front)"
        picture_type = 3

        # Fixed format string issues
        mime_bytes = mime_type.encode()
        desc_bytes = description.encode()

        # Pack each part separately to avoid format string errors
        header = (
            struct.pack(">I", picture_type) +                # Picture type (4 bytes, big-endian)
            struct.pack(">I", len(mime_bytes)) +             # MIME type length (4 bytes)
            mime_bytes +                                     # MIME type string
            struct.pack(">I", len(desc_bytes)) +             # Description length (4 bytes)
            desc_bytes +                                     # Description string
            struct.pack(">I", width) +                       # Width (4 bytes)
            struct.pack(">I", height) +                      # Height (4 bytes)
            struct.pack(">I", color_depth) +                 # Color depth (4 bytes)
            struct.pack(">I", 0) +                           # Number of colors (0 for non-indexed)
            struct.pack(">I", img_data_size)                 # Image data size (4 bytes)
        )

        return header

    def get_cover_metadata(self, cover_art_file):
        """
        Return metadata for embedding cover art

        Args:
          cover_art_file: path to the cover art image
        """
        # Open and analyze the image
        with Image.open(cover_art_file) as img:
            width, height = img.size
            # Determine color depth based on image mode
            if img.mode == 'RGB':
                color_depth = 24
            elif img.mode == 'RGBA':
                color_depth = 32
            else:
                # Convert to RGB for other modes
                img = img.convert('RGB')
                color_depth = 24

            # Get the image data in bytes
            img_byte_arr = io.BytesIO()
            img_format = os.path.splitext(cover_art_file)[1][1:].upper()
            if img_format.lower() == 'jpg':
                img_format = 'JPEG'
            img.save(img_byte_arr, format=img_format)
            img_data = img_byte_arr.getvalue()
            img_data_size = len(img_data)

            # Determine MIME type
            mime_type = f'image/{img_format.lower()}'

            # Create the header
            description = os.path.basename(cover_art_file)
            header = self.create_picture_block_header(
                mime_type, description, width, height, color_depth, img_data_size
            )

            # Combine header and image data
            metadata_block = header + img_data

            # Base64 encode the combined data
            encoded_data = base64.b64encode(metadata_block).decode('ascii')

            return ['-metadata:s:a', f'METADATA_BLOCK_PICTURE={encoded_data}']

    def _get_opus_filename(self, input_filename):
        input_filename = pathlib.Path(input_filename)
        base_filename = pathlib.Path(input_filename).stem.rsplit("-", 1)[0]
        return pathlib.Path(base_filename + f"-{self._bitrate}bps.opus")

    def run_ffmpeg(self, ffmpeg_cmd):
        # Start the FFmpeg process with simple progress output
        ffmpeg_cmd.extend([
            "-progress",
            "-",
            "-nostats",
            "-stats_period",
            "10",
        ])
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # FFmpeg writes progress to stderr by default
            text=True,
            bufsize=1
        )

        speed_regex = re.compile(r"speed=([\d\.x]+)")
        time_regex = re.compile(r"time=(\d{2}):(\d{2}):(\d{2}\.\d{2})")
        duration_regex = re.compile(r"Duration: (\d{2}):(\d{2}):(\d{2}\.\d{2})")

        duration_seconds = None
        current_speed = ""

        # Process output line by line as it becomes available
        for line in iter(process.stdout.readline, ""):
            # Try to find duration
            if duration_seconds is None:
                duration_match = duration_regex.search(line)
                if duration_match:
                    h, m, s = map(float, duration_match.groups())
                    duration_seconds = h * 3600 + m * 60 + s

            speed_match = speed_regex.search(line)
            if speed_match:
                current_speed = f"({speed_match.group(1)} speed)"

            # Try to find current progress time
            time_match = time_regex.search(line)
            if time_match and duration_seconds:
                h, m, s = map(float, time_match.groups())
                current_seconds = h * 3600 + m * 60 + s
                progress = min(100, int(current_seconds / duration_seconds * 100))

                # Update progress bar
                sys.stdout.write(f"{progress}% complete {current_speed} {self._title}\n")
                sys.stdout.flush()

        # Wait for process to complete and get return code
        return_code = process.wait()
        return return_code

    def run(self):
        if self._output_opus_format:
            oname = self._get_opus_filename(self._source)
        else:
            oname = self._source.with_suffix(".m4b").name
        self._title = oname

        outdir = self._target_dir
        if self._output_folders:
            outdir = self._target_dir / self.ffmeta.get_output_path()
            os.makedirs(outdir, exist_ok=True)

        outfile = outdir / oname

        if outfile.exists():
            if self._overwrite:
                secho(f"Overwrite {outfile}: already exists", fg="blue")
            else:
                secho(f"Skip {outfile}: already exists", fg="blue")
                return

        base_cmd = [
            "ffmpeg",
        ]
        if self._overwrite:
            base_cmd.append("-y")
        if isinstance(self._credentials, tuple):
            key, iv = self._credentials
            credentials_cmd = [
                "-audible_key",
                key,
                "-audible_iv",
                iv,
            ]
        else:
            credentials_cmd = [
                "-activation_bytes",
                self._credentials,
            ]    
        base_cmd.extend(credentials_cmd)

        if self._rebuild_chapters:
            metafile = _get_ffmeta_file(self._source, self._tempdir)
            try:
                self.rebuild_chapters()
                self.ffmeta.write(metafile)
            except ChapterError:
                if self._skip_rebuild_chapters:
                    echo("Skip rebuild chapters due to chapter mismatch.")
                else:
                    raise
            else:
                if self._remove_intro_outro:
                    start_new, duration_new = self.ffmeta.get_start_end_without_intro_outro(self.api_chapter)

                    base_cmd.extend(
                        [
                            "-ss",
                            f"{start_new}ms",
                            "-t",
                            f"{duration_new}ms",
                            "-i",
                            str(self._source),
                            "-i",
                            str(metafile),
                            "-map_metadata",
                            "0",
                            "-map_chapters",
                            "1",
                        ]
                    )
                else:
                    base_cmd.extend(
                        [
                            "-i",
                            str(self._source),
                            "-i",
                            str(metafile),
                            "-map_metadata",
                            "0",
                            "-map_chapters",
                            "1",
                        ]
                    )
        else:
            base_cmd.extend(
                [
                    "-i",
                    str(self._source),
                ]
            )

        if self._output_opus_format:
            # Add cover
            cover_filename = self.get_cover_image(str(self._source))
            if cover_filename:
                try:
                    base_cmd.extend(self.get_cover_metadata(cover_filename))
                except Exception as ex:
                    logger.warning(f"Failed to process cover image for {self._source}: {ex}")
            else:
                logger.info(f"No cover image available for {self._source}")
                    
            # Clean up temp image if required
            if cover_filename and cover_filename.startswith("deleteme_"):
                os.unlink(cover_filename)
            
            # ffmpeg transcoding options to opus
            base_cmd.extend(
                [
                    "-c:a",
                    "libopus",
                    "-vn",
                    "-ac",
                    "1",
                    "-application",
                    "voip",
                    "-frame_duration",
                    "60",
                    "-b:a",
                    self._bitrate,
                    str(outfile),
                ]
            )
        else:
            base_cmd.extend(
                [
                    "-c",
                    "copy",
                    str(outfile),
                ]
            )

        self.run_ffmpeg(base_cmd)

        # Work out relative file size
        result_size = ""
        output_size = os.path.getsize(outfile)
        original_size = os.path.getsize(self._source)
        if original_size:
            delta = (output_size/original_size) * 100
            result_size = f"Output is {delta:.1f}% of the original size"
        echo(f"File decryption successful: {outfile}\n{result_size}")

@click.command("decrypt")
@click.argument("files", nargs=-1)
@click.option(
    "--dir",
    "-d",
    "directory",
    type=click.Path(exists=True, dir_okay=True),
    default=pathlib.Path.cwd(),
    help="Folder where the decrypted files should be saved.",
    show_default=True
)
@click.option(
    "--all",
    "-a",
    "all_",
    is_flag=True,
    help="Decrypt all aax and aaxc files in current folder."
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing files.")
@click.option(
    "--rebuild-chapters",
    "-r",
    is_flag=True,
    help="Rebuild chapters with chapters from voucher or chapter file."
)
@click.option(
    "--force-rebuild-chapters",
    "-f",
    is_flag=True,
    help=(
        "Force rebuild chapters with chapters from voucher or chapter file "
        "if the built-in chapters in the audio file mismatch. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--skip-rebuild-chapters",
    "-t",
    is_flag=True,
    help=(
        "Decrypt without rebuilding chapters when chapters mismatch. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--separate-intro-outro",
    "-s",
    is_flag=True,
    help=(
        "Separate Audible Brand Intro and Outro to own Chapter. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--remove-intro-outro",
    "-c",
    is_flag=True,
    help=(
        "Remove Audible Brand Intro and Outro. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--opus",
    is_flag=True,
    help=(
        "Output in Opus format at 32kbps for much smaller output files."
        "Includes cover art, chapters and other metadata."
    ),
)
@click.option(
    "--jobs", "-j",
    type=int,
    default=4,
    show_default=True,
    help="Number of simultaneous decryption jobs."
)
@click.option(
    "--folders",
    is_flag=True,
    help=(
        "Output decrypted audiobooks into separate sub-folders, <author>/<title>/ "
        "Required by some audiobook players."
    ),
)
@click.option(
    "--bitrate",
    "-b",
    "bitrate",
    type=str,
    default="16k",
    help="Bitrate for Opus encoding, in bits.",
    show_default=True
)
@pass_session
def cli(
    session,
    files: str,
    directory: t.Union[pathlib.Path, str],
    all_: bool,
    overwrite: bool,
    rebuild_chapters: bool,
    force_rebuild_chapters: bool,
    skip_rebuild_chapters: bool,
    separate_intro_outro: bool,
    remove_intro_outro: bool,
    opus: bool,
    folders: bool,
    bitrate: str,
    jobs: int,
):
    """Decrypt audiobooks downloaded with audible-cli.

    FILES are the names of the file to decrypt.
    Wildcards `*` and recursive lookup with `**` are supported.

    Only FILES with `aax` or `aaxc` suffix are processed. 
    Other files are skipped silently.
    """
    if not which("ffmpeg"):
        ctx = click.get_current_context()
        ctx.fail("ffmpeg not found")

    if (force_rebuild_chapters or skip_rebuild_chapters or separate_intro_outro or remove_intro_outro) and not rebuild_chapters:
        raise click.BadOptionUsage(
            "",
            "`--force-rebuild-chapters`, `--skip-rebuild-chapters`, `--separate-intro-outro` "
            "and `--remove-intro-outro` can only be used together with `--rebuild-chapters`"
        )

    if force_rebuild_chapters and skip_rebuild_chapters:
        raise click.BadOptionUsage(
            "",
            "`--force-rebuild-chapters` and `--skip-rebuild-chapters` can "
            "not be used together"
        )

    if separate_intro_outro and remove_intro_outro:
        raise click.BadOptionUsage(
            "",
            "`--separate-intro-outro` and `--remove-intro-outro` can not be used together"
        )

    if all_:
        if files:
            raise click.BadOptionUsage(
                "",
                "If using `--all`, no FILES arguments can be used."
            )
        files = [f"*{suffix}" for suffix in SupportedFiles.get_supported_list()]

    files = _get_input_files(files, recursive=True)
    with tempfile.TemporaryDirectory() as tempdir:

        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
            # Create a list of futures
            futures = []
            for file in files:
                future = executor.submit(
                    process_file,
                    file,
                    directory,
                    tempdir,
                    session.auth.activation_bytes,
                    overwrite,
                    rebuild_chapters,
                    force_rebuild_chapters,
                    skip_rebuild_chapters,
                    separate_intro_outro,
                    remove_intro_outro,
                    opus,
                    folders,
                    bitrate
                )
                futures.append(future)

            # Keep processing...
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error(f'Error processing file {file}: {exc} {traceback.format_exc()}')


def process_file(file, directory, tempdir, activation_bytes, overwrite, rebuild_chapters,
                force_rebuild_chapters, skip_rebuild_chapters, separate_intro_outro,
                remove_intro_outro, opus, folders, bitrate):
    decrypter = FfmpegFileDecrypter(
        file=file,
        target_dir=pathlib.Path(directory).resolve(),
        tempdir=pathlib.Path(tempdir).resolve(),
        activation_bytes=activation_bytes,
        overwrite=overwrite,
        rebuild_chapters=rebuild_chapters,
        force_rebuild_chapters=force_rebuild_chapters,
        skip_rebuild_chapters=skip_rebuild_chapters,
        separate_intro_outro=separate_intro_outro,
        remove_intro_outro=remove_intro_outro,
        output_opus_format=opus,
        output_folders=folders,
        bitrate=bitrate,
    )
    return decrypter.run()
