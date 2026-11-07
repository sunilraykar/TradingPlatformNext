import logging


class _OptionChainLiveQuotesAccessFilter(logging.Filter):
    """Hide the large instrument_tokens query string from Uvicorn access logs."""

    _PATH = "/api/v1/apiplatform/option-chain/live-quotes"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            request_target = args[2]
            if isinstance(request_target, str):
                path = request_target.split("?", 1)[0]
                if path == self._PATH:
                    sanitized = list(args)
                    sanitized[2] = path
                    record.args = tuple(sanitized)
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Keep normal access logging, but don't emit hundreds of instrument tokens
    # on every option-chain quote poll.
    logging.getLogger("uvicorn.access").addFilter(_OptionChainLiveQuotesAccessFilter())
