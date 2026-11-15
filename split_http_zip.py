"""Bounded reads of a concatenated public release ZIP without fetching image archives."""

from bisect import bisect_right
from collections import OrderedDict
import io
import time

import requests


class SplitRangeReader(io.RawIOBase):
    def __init__(self, assets, maximum_transfer=300 * 1024 ** 2, fetch=None):
        self.assets = assets
        self.edges = [0]
        for asset in assets:
            if asset["size"] <= 0:
                raise ValueError("Invalid original release asset size")
            self.edges.append(self.edges[-1] + asset["size"])
        if not assets:
            raise ValueError("Missing split ZIP assets")
        self.size, self.position = self.edges[-1], 0
        self.transferred_bytes, self.request_count = 0, 0
        self.maximum_transfer, self.cache = maximum_transfer, OrderedDict()
        self.session = requests.Session()
        self.network_attempts, self.network_failures = 0, []
        self.fetch = fetch or self._request

    def _request(self, asset, first, last):
        for attempt in range(3):
            self.network_attempts += 1
            try:
                return self._single_request(asset, first, last)
            except (requests.ConnectionError, requests.Timeout) as error:
                self.network_failures.append(dict(first=first, last=last, error=type(error).__name__, attempt=attempt + 1))
                if attempt == 2:
                    raise
                self.session.close()
                self.session = requests.Session()
                time.sleep(.5 * (attempt + 1))

    def _single_request(self, asset, first, last):
        with self.session.get(asset["browser_download_url"], headers={"Range": f"bytes={first}-{last}",
                              "Accept-Encoding": "identity"}, timeout=(20, 90), stream=True) as response:
            response.raise_for_status()
            if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {first}-{last}/{asset['size']}":
                raise OSError("Release asset does not support the exact bounded range; whole-asset download refused")
            data = response.raw.read(last - first + 2)
            if len(data) != last - first + 1:
                raise OSError("Release returned a truncated or oversized range")
            return data

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_CUR:
            offset += self.position
        elif whence == io.SEEK_END:
            offset += self.size
        elif whence != io.SEEK_SET:
            raise ValueError("Invalid split ZIP seek mode")
        if offset < 0:
            raise ValueError("Negative split ZIP byte offset")
        self.position = offset
        return offset

    def read(self, size=-1):
        size = self.size - self.position if size < 0 else size
        if size > 4 * 1024 ** 2:
            raise ValueError("Stream ZIP members in bounded chunks, not complete large archives")
        stop = min(self.size, self.position + size)
        parts = []
        while self.position < stop:
            index = bisect_right(self.edges, self.position) - 1
            end = min(stop, self.edges[index + 1])
            first, last = self.position - self.edges[index], end - self.edges[index] - 1
            key = index, first, last
            if key in self.cache:
                data = self.cache[key]
                self.cache.move_to_end(key)
            else:
                if self.transferred_bytes + last - first + 1 > self.maximum_transfer:
                    raise OSError("Declared selective source-transfer budget exhausted")
                data = self.fetch(self.assets[index], first, last)
                if len(data) != last - first + 1:
                    raise OSError("Original asset range extent mismatch")
                self.transferred_bytes += len(data)
                self.request_count += 1
                if len(data) <= 1024 ** 2:
                    self.cache[key] = data
                    while sum(len(value) for value in self.cache.values()) > 4 * 1024 ** 2:
                        self.cache.popitem(last=False)
            parts.append(data)
            self.position = end
        return b"".join(parts)

    def close(self):
        self.session.close()
        super().close()
