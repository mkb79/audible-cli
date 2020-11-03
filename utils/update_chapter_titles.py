"""
This script replaces the chapter titles from a ffmetadata file with the one
extracted from a api metadata file

Example:

.. code-block:: shell

   # download a book and the chapter file
   audible download -a {ASIN_OF_BOOK} --chapter

   # extract the metadata from aax file
   ffmpeg -i {NAME_OF_DOWNLOADED_AAX_FILE} -f ffmetadata {FFMETADATAFILE}

   # replace chapter titles from extracted metadatafile
   python update_chapter_titles.py -f {FFMETADATAFILE} \
                                   -a {NAME_OF_DOWNLOADED_CHAPTER_FILE} \
                                   -o {NEW_FFMETADATAFILE}

   # insert new metadata in file and convert it to m4b
   # ffmpeg 4.1 and above support copying album art with `-c copy`
   ffmpeg -activation_bytes {ACTIVATION_BYTES} \
          -i {NAME_OF_DOWNLOADED_AAX_FILE} \
          -i {NEW_FFMETADATAFILE} \
          -map_metadata 1 \
          -c copy \
          NAME_OF_TARGET_M4B_FILE.m4b

"""

import json
import pathlib
import re
import sys

import click
from click import echo


class ApiMeta:
    def __init__(self, api_meta_file):
        self._meta_raw = pathlib.Path(api_meta_file).read_text("utf-8")
        self._meta_parsed = self._parse_meta()

    def _parse_meta(self):
        return json.loads(self._meta_raw)

    def count_chapters(self):
        return len(self.get_chapters())

    def get_chapters(self):
        return self._meta_parsed["content_metadata"]["chapter_info"]["chapters"]


class FFMeta:

    SECTION = re.compile(r"\[(?P<header>[^]]+)\]")
    OPTION = re.compile(r"(?P<option>.*?)\s*(?:(?P<vi>=)\s*(?P<value>.*))?$")

    def __init__(self, ffmeta_file):
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
                sec_name = mo.group('header')
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
                    self._write_section(fp, section, self._ffmeta_parsed[section][chapter], d)
            else:
                self._write_section(fp, section, self._ffmeta_parsed[section], d)

    def _write_section(self, fp, section_name, section_items, delimiter):
        """Write a single section to the specified `fp'."""
        if section_name is not None:
            fp.write(f"[{section_name}]\n")

        for key, value in section_items.items():
            if value is None:
                fp.write(f"{key}\n")
            else:
                fp.write(f"{key}{delimiter}{value}\n")

    def update_title_from_api_meta(self, api_meta):
        if not isinstance(api_meta, ApiMeta):
            api_meta = ApiMeta(api_meta)

        assert api_meta.count_chapters() == self.count_chapters()

        echo(f"Found {self.count_chapters()} chapters to prepare.")

        num_chap = 0
        for chapter in api_meta.get_chapters():
            num_chap += 1
            for key, value in chapter.items():
                if key == "title":
                    self.set_chapter_option(num_chap, "title", value)


CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])
        
@click.command(context_settings=CONTEXT_SETTINGS)
@click.option(
   "--ffmeta", "-f",
   type=click.Path(
       exists=True, file_okay=True, readable=True
   ),
   required=True,
   help="ffmetadata file extracted from file with ffmpeg"
)
@click.option(
   "--apimeta", "-a",
   type=click.Path(
       exists=True, file_okay=True, readable=True
   ),
   required=True,
   help="metadata from api"
)
@click.option(
   "--outfile", "-o",
   type=click.Path(exists=False, file_okay=True),
   required=True,
   help="filename to store prepared ffmeta"
)
def cli(ffmeta, apimeta, outfile):
    ffmeta_class = FFMeta(ffmeta)
    ffmeta_class.update_title_from_api_meta(apimeta)
    ffmeta_class.write(outfile)
    click.echo(f"Replaced all titles. Save file to {outfile}")


def main(*args, **kwargs):
    try:
        cli(*args, **kwargs)
    except KeyboardInterrupt:
        sys.exit('\nERROR: Interrupted by user')

if __name__ == "__main__":
    sys.exit(main())
