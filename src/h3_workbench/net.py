"""Requests to our own loopback service must bypass system HTTP proxies."""
import json
import urllib.request


def local_json(url, body=None, timeout=10):
    headers = {}
    data = None
    if body is not None:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        headers = {"Origin": f"{parsed.scheme}://{parsed.netloc}", "Content-Type": "application/json"}
        data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)
