"""Simple logging setup."""

import logging
import sys

_logger = logging.getLogger("codex-proxy")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    _logger.setLevel(level)
    _logger.addHandler(handler)


def get_logger() -> logging.Logger:
    return _logger
