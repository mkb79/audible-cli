import click

from . import (
    cmd_activation_bytes,
    cmd_download,
    cmd_library,
    cmd_manage,
    cmd_quickstart
)

cli_cmds = [
    cmd_activation_bytes.cli,
    cmd_download.cli,
    cmd_library.cli,
    cmd_manage.cli,
    cmd_quickstart.cli
]

def build_in_cmds():
    """
    A decorator to register build-in CLI commands to an instance of
    `click.Group()`.

    Returns
    -------
    click.Group()
    """
    def decorator(group):
        if not isinstance(group, click.Group):
            raise TypeError("Plugins can only be attached to an instance of "
                            "click.Group()")

        for cmd in cli_cmds:
            group.add_command(cmd)

        return group

    return decorator
