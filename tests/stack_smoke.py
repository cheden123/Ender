"""Black-box check of the running Compose stack, isolated from real hardware."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def until(fn, seconds=100):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            result = fn()
            if result:
                return result
        except (HTTPError, URLError, OSError):
            pass
        time.sleep(1)
    raise AssertionError("Timed out waiting for " + fn.__name__)


def api(path, body=None, content_type="application/json"):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = Request(
        "http://127.0.0.1:18080" + path,
        data=data,
        headers={
            "Authorization": "Bearer " + os.environ["APP_TOKEN"],
            "Content-Type": content_type,
        },
    )
    with urlopen(req, timeout=10) as response:
        if response.headers.get_content_type() == "application/json":
            return json.load(response)
        return response.read()


def main():
    # Fixed loopback ports plus an explicit simulator identity prevent test controls
    # reaching a printer accidentally. CI passes only the disposable mock service.
    assert os.environ.get("MOONRAKER_URL") in (
        "http://mock:17255",
        "http://127.0.0.1:17255",
    )
    probe = lambda: json.load(urlopen("http://127.0.0.1:17255/fixture", timeout=2))[
        "result"
    ]
    assert until(probe) == "ender-watch-simulator"
    until(lambda: api("/api/status")["detector"] == "ready")
    status = api("/api/status")
    assert status["connected"] and status["state"] == "printing"
    job = status["job_id"]
    assert job and api("/api/camera.jpg").startswith(b"\xff\xd8")
    assert api("/api/temperatures")["items"]
    upload = "/api/upload?name=qa.gcode"
    api(upload, b"; test file\n", "application/octet-stream")
    assert any(f["name"] == "qa.gcode" for f in api("/api/files")["items"])
    for cmd in ("pause", "resume", "cancel"):
        api("/api/control/" + cmd, {"job_id": job})

    def finished():
        jobs = api("/api/history")["items"]
        return next(
            (item for item in jobs if item["id"] == job and item["timelapse"]), None
        )

    entry = until(finished)
    assert entry["ended"] and api(entry["snapshot"]).startswith(b"\xff\xd8")
    assert b"ftyp" in api(entry["timelapse"])[:24]
    api("/api/control/start", {"filename": "qa.gcode"})
    until(lambda: api("/api/status")["filename"] == "qa.gcode")
    try:
        api("/api/control/pause", {"job_id": job})
    except HTTPError as exc:
        assert exc.code == 409
    else:
        raise AssertionError("Stale pause accepted")
    print(
        "Stack smoke passed: inference, camera, upload, manual controls, history, MP4, stale action."
    )


if __name__ == "__main__":
    main()
