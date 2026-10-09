"""Cronitor lifecycle telemetry, with bounded retries and redacted errors."""
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from .errors import BackupError


def notify(url, state, series, message="", *, opener=urlopen, sleep=time.sleep):
    if not url:
        return
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in {"state", "series", "message"}]
    if message:
        query.append(("message", message[:2000]))
    target = urlunsplit(parts._replace(query=urlencode(query + [("state", state), ("series", series)])))
    for attempt in range(4):
        try:
            with opener(target, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise OSError("HTTP status")
            return
        except OSError:
            if attempt == 3:
                # Never log the credential-bearing telemetry URL.
                raise BackupError("Falha na notificação Cronitor após 4 tentativas") from None
            sleep(2)
