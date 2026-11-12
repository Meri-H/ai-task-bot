import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Plain, grep-friendly logging to stdout (what `docker compose logs` expects)."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        numeric = logging.INFO

    root = logging.getLogger()
    root.setLevel(numeric)
    if not any(getattr(h, "_taskbot", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s"))
        handler._taskbot = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    # Third-party libraries stay at WARNING+ unless the app itself is in DEBUG.
    noisy_level = numeric if numeric <= logging.DEBUG else logging.WARNING
    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(noisy_level)
