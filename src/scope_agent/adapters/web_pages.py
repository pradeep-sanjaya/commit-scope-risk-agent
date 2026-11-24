import re
from html.parser import HTMLParser

import httpx

_SKIP = {"script", "style"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip_depth += 1
        else:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


class WebPageFetcher:
    def __init__(self, http: httpx.Client):
        self._http = http

    def text(self, url: str) -> str | None:
        response = self._http.get(url)
        if response.status_code != 200:
            return None
        parser = _TextExtractor()
        parser.feed(response.text)
        parser.close()
        return re.sub(r"\s+", " ", "".join(parser.parts)).strip()
