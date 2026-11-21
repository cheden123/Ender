import asyncio
import io
import os

os.environ.setdefault("APP_TOKEN", "test-token-at-least-24-bytes-long")

import httpx
from main import Monitor, create_app, valid_name
from PIL import Image


def test_monitor_controls_and_history(tmp_path, monkeypatch):
    async def skip_encoding(self, jid, attempt=0):
        return None

    monkeypatch.setattr(Monitor, "encode", skip_encoding)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAMERA_SNAPSHOT_URL", "http://camera/snapshot")
    picture = io.BytesIO()
    Image.new("RGB", (24, 24)).save(picture, "JPEG")
    state = {"state": "printing", "filename": "part.gcode", "total_duration": 30}
    source = {"id": "A"}
    calls = []

    def remote(request):
        calls.append((request.method, request.url.path))
        if request.url.host == "camera":
            assert "x-api-key" not in request.headers
            assert "authorization" not in request.headers
            return httpx.Response(200, content=picture.getvalue())
        if request.url.path == "/server/files/list":
            return httpx.Response(
                200, json={"result": [{"path": "part.gcode", "size": 1}]}
            )
        if request.url.path == "/server/history/list":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "jobs": [
                            {
                                "filename": "part.gcode",
                                "status": "in_progress",
                                "job_id": source["id"],
                            }
                        ]
                    }
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "result": {
                        "status": {
                            "print_stats": state,
                            "virtual_sdcard": {"progress": 0.3},
                            "extruder": {"temperature": 200},
                            "heater_bed": {"temperature": 60},
                        }
                    }
                },
            )
        if request.url.path.endswith("/pause"):
            state["state"] = "paused"
        return httpx.Response(200, json={"result": "ok"})

    class Detector:
        def score(self, data):
            return 0.99

    async def scenario():
        client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
        monitor = Monitor(client, Detector())
        await monitor.poll()
        first = monitor.status["job_id"]
        await monitor.poll()
        await monitor.poll()
        assert len(monitor.rows("SELECT * FROM alerts")) == 1
        assert not any(method == "POST" for method, path in calls), (
            "Detection must never control the printer"
        )
        alert = monitor.rows("SELECT * FROM alerts")[0]
        evidence = (
            tmp_path / alert["snapshot"].removeprefix("/api/media/")
        ).read_bytes()
        app = create_app(monitor)
        app.state.monitor = monitor
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://app"
        ) as api:
            assert (await api.get("/api/status")).status_code == 401
            headers = {"Authorization": "Bearer " + os.environ["APP_TOKEN"]}
            assert (
                await api.get(alert["snapshot"], headers=headers)
            ).content == evidence
            assert (
                await api.get("/api/media/../history.sqlite3", headers=headers)
            ).status_code == 404
            assert (
                await api.post(
                    "/api/control/pause", headers=headers, json={"job_id": first + 1}
                )
            ).status_code == 409
            assert (
                await api.post(
                    "/api/control/pause", headers=headers, json={"job_id": first}
                )
            ).status_code == 200
            assert (
                await api.post(
                    "/api/control/pause", headers=headers, json={"job_id": first}
                )
            ).status_code == 409
            # A new print of the same file must invalidate the old action even before the next poll.
            state["state"] = "printing"
            source["id"] = "B"
            assert (
                await api.post(
                    "/api/control/cancel", headers=headers, json={"job_id": first}
                )
            ).status_code == 409
            await monitor.poll()
            assert monitor.status["job_id"] != first
            assert monitor.rows("SELECT * FROM jobs WHERE id=?", (first,))[0]["ended"]
            assert (
                await api.post(
                    "/api/upload?name=../bad.gcode", headers=headers, content=b"x"
                )
            ).status_code == 400
            assert (
                await api.post(
                    "/api/upload",
                    params={"name": "part.gcode"},
                    headers=headers,
                    content=b"G28",
                )
            ).status_code == 409
            assert (
                await api.post(
                    "/api/upload",
                    params={"name": "Корпус.gcode"},
                    headers=headers,
                    content=b"G28",
                )
            ).status_code == 200
            assert (
                await api.post("/api/control/gcode", headers=headers, json={})
            ).status_code == 404
        if monitor.encoder_tasks:
            await asyncio.gather(*monitor.encoder_tasks)
        # Restart reuses the durable active job instead of duplicating it.
        current = monitor.status["job_id"]
        monitor.db.close()
        restarted = Monitor(client, Detector())
        await restarted.poll()
        assert restarted.status["job_id"] == current
        restarted.db.close()
        await client.aclose()

    asyncio.run(scenario())


def test_short_token_rejected(monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "short")
    try:
        create_app()
    except RuntimeError:
        pass
    else:
        assert False


def test_unicode_filenames():
    assert valid_name("Корпус детали.gcode", basename=True)
    assert not valid_name("../Корпус.gcode", basename=True)
    assert not valid_name("Корпус\r\n.gcode", basename=True)
    assert not valid_name("dir/Корпус.gcode", basename=True)


def test_timelapse_retry_and_atomic_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monitor = Monitor()
    monitor.db.execute(
        "INSERT INTO jobs(id,filename,state,started,ended) VALUES(1,'part.gcode','complete',1,2)"
    )
    folder = tmp_path / "frames" / "1"
    folder.mkdir(parents=True)
    (folder / "000000.jpg").write_bytes(b"frame")
    calls = []

    async def launch(*args, **kwargs):
        output = args[-1]
        calls.append(output)

        class Process:
            returncode = 1 if len(calls) == 1 else 0

            async def wait(self):
                if self.returncode == 0:
                    from pathlib import Path

                    Path(output).write_bytes(b"mp4")
                return self.returncode

        return Process()

    async def no_delay(_):
        return None

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    monkeypatch.setattr(asyncio, "sleep", no_delay)

    async def run():
        await monitor.encode(1)
        assert len(calls) == 2
        assert all(str(p).endswith(".partial.mp4") for p in calls)
        assert (tmp_path / "timelapses" / "1.mp4").read_bytes() == b"mp4"
        assert not folder.exists()
        assert (
            monitor.rows("SELECT timelapse FROM jobs")[0]["timelapse"]
            == "/api/media/timelapses/1.mp4"
        )
        await monitor.client.aclose()
        monitor.db.close()

    asyncio.run(run())
