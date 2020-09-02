# -*- coding: utf-8 -*-

import logging
from logging import NullHandler

from ._logging import AudibleLogHelper
from ._version import (
    __title__, __description__, __url__, __version__, __author__,
    __author_email__, __license__, __status__
)
from .cli import main, quickstart

__all__ = [
    "__version__", "log_helper", "cli", "quickstart"]

logging.getLogger("audible-cli").addHandler(NullHandler())
log_helper = AudibleLogHelper()
