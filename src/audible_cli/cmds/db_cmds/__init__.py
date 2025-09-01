from __future__ import annotations

import click

# Subcommand-Gruppen importieren
from .cmd_library import library as _library_group
from .cmd_assets import assets as _assets_group


@click.group(name="db", help="Manage local SQLite databases (library, wishlist, user, ...)")
def cli() -> None:
    """Root group for DB-related commands."""
    # Intentionally empty


# db library …
cli.add_command(_library_group, name="library")
cli.add_command(_assets_group, name="assets")
