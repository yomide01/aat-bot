import logging
import re
from rich.logging import RichHandler

_SECRET_RE = re.compile(
    r"(bot\d+:[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9_]{20,}|AQ\.[A-Za-z0-9_-]{20,}|"
    r"AIza[A-Za-z0-9_-]{20,}|GOCSPX-[A-Za-z0-9_-]{15,}|1//[A-Za-z0-9_-]{20,})"
)


class _SecretRedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _SECRET_RE.sub("[REDACTED]", record.msg)
        if record.args:
            record.args = tuple(
                _SECRET_RE.sub("[REDACTED]", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


_handler = RichHandler(rich_tracebacks=True, markup=True)
_handler.addFilter(_SecretRedactingFilter())

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[_handler],
)

# Silence httpx/httpcore URL logging so Telegram bot URLs are never printed in CI logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("tiktok_bot")
logger.addFilter(_SecretRedactingFilter())
