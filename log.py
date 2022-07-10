"""

    Author: Nimrod P.

    Simple pretty logger module. Avoids the mess that is the python logging module.

"""

import inspect
import os
import sys
from typing import Tuple


class LoggingLevel:
    """ A class for maintaining order between named logging levels """

    def __init__(self, name: str, ordinal: int):
        self.name = name
        self.ordinal = ordinal

    def __str__(self):
        return self.name

    def __lt__(self, other):
        return self.ordinal < other.ordinal

    def __le__(self, other):
        return self < other or self == other


DEBUG = LoggingLevel("DEBUG", 0)
INFO = LoggingLevel("INFO", 1)
WARNING = LoggingLevel("WARNING", 2)
ERROR = LoggingLevel("ERROR", 3)
CRITICAL = LoggingLevel("CRITICAL", 4)

# color formatting: background is set with 40 plus the number of the color, and the foreground with 30
BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)

# the characters that create colored output in the shell
RESET_SEQ = "\033[0m"
COLOR_SEQ = "\033[1;%dm"
BOLD_SEQ = "\033[1m"

COLORS = {  # mapping the levels to their color
    WARNING: YELLOW,
    INFO: GREEN,
    DEBUG: BLUE,
    CRITICAL: MAGENTA,
    ERROR: RED
}

logging_level = WARNING  # this variable sets the logging level


def color_text(text: str, color: int) -> str:
    return f"{COLOR_SEQ % (30 + color)}{text}{RESET_SEQ}"


def bold_text(text: str) -> str:
    return f"{BOLD_SEQ}{text}{RESET_SEQ}"


def format_message(message: str, level: LoggingLevel) -> str:
    return f"{color_text(level.name, COLORS[level]):20} | {bold_text('logger')} | {message}"


def log(args: Tuple, level: LoggingLevel) -> None:
    if level < logging_level:
        return
    filename, line_number, _, _, _ = inspect.getframeinfo(inspect.currentframe().f_back.f_back)
    message = [format_message(arg, level) for arg in " ".join(map(str, args)).split("\n")]

    message[-1] += " " + bold_text(f"({os.path.basename(filename)}:{line_number})")
    message = "\n".join(message)

    if level > WARNING:
        print(message, file=sys.stderr)
    else:
        print(message)


def is_debug_mode() -> bool:
    return logging_level == DEBUG


def debug(*s) -> None:
    log(s, DEBUG)


def info(*s) -> None:
    log(s, INFO)


def warn(*s) -> None:
    log(s, WARNING)


def error(*s) -> None:
    log(s, ERROR)


def critical(*s) -> None:
    log(s, CRITICAL)
