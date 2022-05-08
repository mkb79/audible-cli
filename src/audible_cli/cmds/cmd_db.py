from http.cookiejar import FileCookieJar
from sqlalchemy import Column, String, create_engine, Boolean
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from audible_cli.cmds.cmd_download import CLIENT_HEADERS
from audible_cli.cmds.cmd_library import _get_sorted_library
import logging

import click

import os

from ..decorators import (
    bunch_size_option,
    pass_client,
    pass_session,
)

# this feels like the wrong way to find the users home foler, but it works for now.
path = 'sqlite:///' + os.path.expanduser('~') + '/.audible/library.db'

logger = logging.getLogger("audible_cli.cmds.cmd_db")
Base = declarative_base()
engine = create_engine(path)
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine)()


def _update_book(asin, **kwargs):
    BOOKS = Base.metadata.tables['book']
    engine.execute(
        BOOKS.update().where(Book.asin == asin).values(kwargs)
    )


async def _rebuild(session, client):
    # remove all current entries
    num = 0
    entries = db.query(Book).all()
    for i in entries:
        db.delete(i)
        db.commit()
        num += 1
    # rebuild from scratch
    await ingest_library(session, client)

    logger.info(
        f"Removed and added {num} books from local library "
    )
    click.Abort()


# this function should be called before downloading to
# make sure there are no conflicts with existing downloads
def _is_downloaded(asin):
    book = db.query(Book).filter_by(asin=asin).one()
    return book.downloaded


async def ingest_library(session, client):
    freshLibrary = await _get_sorted_library(session, client)
    # Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    oldLibrary = db.query(Book).all()
    for book in freshLibrary:
        if book not in oldLibrary:
            db.add(Book(**(book)))
            db.commit()


@click.group("db")
def cli():
    """interact with local database"""


@cli.command("db")
@click.option(
    "--rebuild",
    is_flag=True,
    default=False,
    help="Rebuild local library from Audible API. Exclusive with all other options"
)
@click.option(
    "--list", "-l",
    is_flag=True,
    help="List all books in local library"
)
@click.option(
    "--remove", "-r",
    multiple=True,
    help="remove the specified ASIN from local library"
)
@click.option(
    "--tag", "-t",
    multiple=True,
    help="mark the specified ASIN as downloaded"
)
@click.option(
    "--status", "-s",
    help="Check the status of a book (either downlaoded or not)"
)
@click.option(
    "--update", "-u",
    is_flag=True,
    default=False,
    help="Update local library with latest purchases"
)
@bunch_size_option
@pass_session
@pass_client(headers=CLIENT_HEADERS)
async def cli(session, client, **params):
    """Manage local library (default in ~/.audible/library.db)"""

    rebuild = params.get("rebuild")
    update = params.get("update")
    _list = params.get("list")
    remove = params.get("remove")
    tags = params.get("tag")
    status = params.get("status")

    if _list:
        entries = db.query(Book).all()
        for i in entries:
            logger.info(
                f"{i.asin} "
                f"{i.title}"
            )
        click.Abort()

    if rebuild:
        await _rebuild(session, client)
        click.Abort()

    if update:
        await ingest_library(session, client)
        click.Abort()

    if status:
        logger.info(_is_downloaded(status))

    for asin in remove:
        _update_book(asin, downloaded=False)

    for asin in tags:
        _update_book(asin, downloaded=True)


class Book(Base):
  # these attributes need to stay in sync with the rows generated
  # by audible_cli.cmds.cmd_library._prepare_item()
    __tablename__ = "book"
    asin = Column(String, primary_key=True)
    title = Column(String)
    genres = Column(String)
    rating = Column(String)
    authors = Column(String)
    subtitle = Column(String)
    narrators = Column(String)
    cover_url = Column(String)
    date_added = Column(String)
    downloaded = Column(Boolean, default=False)
    description = Column(String)
    num_ratings = Column(String)
    is_finished = Column(String)
    release_date = Column(String)
    series_title = Column(String)
    series_sequence = Column(String)
    percent_complete = Column(String)
    runtime_length_min = Column(String)
