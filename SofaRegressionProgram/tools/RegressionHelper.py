import sys
from typing import TextIO
import platform

class TermColor:
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    ITALIC = "\033[3m"
    RESET = "\033[0m"

class TermTypeStrings:
    ERROR = "[Regression-Error]"
    WARNING = "[Regression-Warning]"
    SUCCESS = "[Regression-Success]"
    LOG = "[Regression-Log]"


def writeMessage(message, stream: TextIO = sys.stdout):
    print(f"{message}", file=stream, flush=True)

def writeError(message, verbose, prefix='', err_log_stream = None):
    complete_message = f"{prefix}{TermColor.RED}{TermTypeStrings.ERROR} {TermColor.RESET}{message}"
    if(verbose >= 0):
        writeMessage(complete_message, stream = sys.stderr)
    if(err_log_stream is not None):
        writeMessage(complete_message, stream = err_log_stream)

def writeWarning(message, verbose, prefix=''):
    if(verbose >= 1):
        writeMessage(f"{prefix}{TermColor.YELLOW}{TermTypeStrings.WARNING} {TermColor.RESET}{message}")

def writeSuccess(message, verbose, prefix=''):
    if(verbose >= 0):
        writeMessage(f"{prefix}{TermColor.GREEN}{TermTypeStrings.SUCCESS} {TermColor.RESET}{message}")

def writeLog(message, verbose, prefix=''):
    if(verbose >= 2):
        writeMessage(f"{prefix}{TermColor.CYAN}{TermTypeStrings.LOG} {TermColor.ITALIC}{message}{TermColor.RESET}")



def get_os() -> str:
    """Return 'ubuntu', 'fedora', 'macos' or 'windows' for the current OS.

    Raises RuntimeError on any other system.
    """
    system = platform.system()

    if system == "Windows":
        return "windows"
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        info = _read_os_release()
        ids = {info.get("ID", "")} | set(info.get("ID_LIKE", "").split())
        if "ubuntu" in ids:
            return "ubuntu"
        if "fedora" in ids:
            return "fedora"

    raise RuntimeError(f"Unsupported OS: {system}")


def _read_os_release() -> dict:
    """Parse /etc/os-release into a dict (empty if unavailable)."""
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    info[key] = value.strip('"\'')
    except OSError:
        pass
    return info
