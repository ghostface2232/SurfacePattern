#! python3
# Shared error logging: full tracebacks go to a log file, the command line gets one short line.

import os
import time
import traceback

import Rhino

LOG_DIR = os.path.join(os.path.expanduser("~"), ".surfacepattern")
LOG_PATH = os.path.join(LOG_DIR, "log.txt")
PREV_LOG_PATH = os.path.join(LOG_DIR, "log.prev.txt")
MAX_LOG_BYTES = 512 * 1024
NOTICE_COOLDOWN = 5.0  # seconds; identical consecutive errors stay silent on the command line

_last_notice = {"signature": None, "time": 0.0}


def _params_summary():
    """Current session params as one line; never raises."""
    try:
        from surfacepattern.core.session import get_session

        return repr(get_session().params)
    except Exception:
        return "(session unavailable)"


def _rollover():
    """Keep the log bounded: rotate to log.prev.txt past MAX_LOG_BYTES."""
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
            if os.path.exists(PREV_LOG_PATH):
                os.remove(PREV_LOG_PATH)
            os.rename(LOG_PATH, PREV_LOG_PATH)
    except OSError:
        pass


def log_error(context=""):
    """Append the active exception to the log file; returns the log path.

    Prints a single command-line notice with the path, suppressing repeats of the
    same error within NOTICE_COOLDOWN seconds so a failing draft loop cannot flood
    the command line. Call from inside an except block.
    """
    text = traceback.format_exc()
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        _rollover()
        with open(LOG_PATH, "a", encoding="utf-8") as stream:
            stream.write(
                "---- {} | {}\nparams: {}\n{}\n".format(
                    time.strftime("%Y-%m-%d %H:%M:%S"), context, _params_summary(), text
                )
            )
    except OSError:
        # Logging must never raise; fall back to the command line only.
        Rhino.RhinoApp.WriteLine("SurfacePattern error (log file unavailable):\n" + text)
        return LOG_PATH

    signature = context + text.strip().splitlines()[-1] if text.strip() else context
    now = time.time()
    if (
        signature != _last_notice["signature"]
        or now - _last_notice["time"] > NOTICE_COOLDOWN
    ):
        Rhino.RhinoApp.WriteLine("SurfacePattern: error logged — {}".format(LOG_PATH))
    _last_notice["signature"] = signature
    _last_notice["time"] = now
    return LOG_PATH
