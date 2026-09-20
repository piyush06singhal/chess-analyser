"""Probe the invalid-FEN response body of the live API."""
import json
import urllib.request
import urllib.error

req = urllib.request.Request(
    "http://127.0.0.1:8001/api/analysis/position",
    data=json.dumps({"fen": "not a fen"}).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    urllib.request.urlopen(req)
    print("unexpected 200")
except urllib.error.HTTPError as exc:
    print("status", exc.code)
    print(exc.read().decode()[:400])
