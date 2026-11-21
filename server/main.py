"""Local-only Moonraker monitor. Run: uvicorn main:app --host 0.0.0.0."""

import asyncio
import contextlib
import hmac
import io
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from PIL import Image


class Monitor:
    def __init__(self, client=None, classifier=None):
        self.root = Path(os.getenv("DATA_DIR", "/data")).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(
            self.root / "history.sqlite3", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,filename TEXT,state TEXT,started REAL,ended REAL,snapshot TEXT,timelapse TEXT,elapsed REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,job_id INTEGER,ts REAL,message TEXT,snapshot TEXT);
        CREATE TABLE IF NOT EXISTS temperatures(ts REAL,bed REAL,nozzle REAL);""")
        if "source_id" not in [
            r[1] for r in self.db.execute("PRAGMA table_info(jobs)")
        ]:
            self.db.execute("ALTER TABLE jobs ADD COLUMN source_id TEXT")
        self.client = client or httpx.AsyncClient(timeout=15)
        self.api_key = os.getenv("MOONRAKER_API_KEY", "")
        self.base = os.getenv("MOONRAKER_URL", "http://moonraker:7125").rstrip("/")
        self.camera = os.getenv("CAMERA_SNAPSHOT_URL", "")
        self.classifier = classifier
        self.status = {
            "connected": False,
            "state": "offline",
            "filename": "",
            "progress": 0,
            "nozzle": 0,
            "bed": 0,
            "nozzle_target": 0,
            "bed_target": 0,
            "eta_seconds": None,
            "job_id": None,
            "detector": "waiting",
        }
        self.lock = asyncio.Lock()
        self.jpeg = None
        self.jpeg_at = 0
        self.hits = 0
        self.last_alert = 0
        self.last_frame = 0
        self.last_temp = 0
        self.encoder_tasks = set()

    async def moon(self, method, path, **kw):
        r = await self.client.request(
            method, self.base + path, headers={"X-Api-Key": self.api_key}, **kw
        )
        r.raise_for_status()
        return r.json().get("result", {})

    def rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    async def picture(self):
        if not self.camera:
            raise RuntimeError("CAMERA_SNAPSHOT_URL is not configured")
        data = bytearray()
        async with self.client.stream("GET", self.camera) as r:
            r.raise_for_status()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data) > 12 * 1024 * 1024:
                    raise RuntimeError("camera image exceeds limit")

        def normalize():
            with Image.open(io.BytesIO(data)) as im:
                if im.width * im.height > 24_000_000:
                    raise RuntimeError("camera dimensions exceed limit")
                im.thumbnail((1920, 1080))
                out = io.BytesIO()
                im.convert("RGB").save(out, "JPEG", quality=85)
                return out.getvalue()

        self.jpeg = await asyncio.to_thread(normalize)
        self.jpeg_at = time.time()
        return self.jpeg

    def save(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return "/api/media/" + relative

    async def poll(self):
        async with self.lock:
            raw = await self.moon(
                "GET",
                "/printer/objects/query?print_stats&display_status&virtual_sdcard&extruder&heater_bed",
            )
            o = raw["status"]
            p = o.get("print_stats", {})
            elapsed = float(p.get("total_duration", 0))
            state = p.get("state", "standby")
            name = p.get("filename", "")
            active = state in ("printing", "paused")
            source_id = None
            if active:
                try:
                    history = await self.moon(
                        "GET", "/server/history/list?limit=1&order=desc"
                    )
                    latest = history.get("jobs", [])
                    if (
                        latest
                        and latest[0].get("filename") == name
                        and latest[0].get("status") == "in_progress"
                    ):
                        source_id = str(latest[0]["job_id"])
                except (httpx.HTTPError, KeyError, TypeError):
                    pass
            job = self.db.execute(
                "SELECT * FROM jobs WHERE ended IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            # A duration reset distinguishes consecutive prints of the same filename.
            replaced = bool(
                job
                and active
                and (
                    job["filename"] != name
                    or elapsed + 2 < job["elapsed"]
                    or (
                        source_id and job["source_id"] and source_id != job["source_id"]
                    )
                )
            )
            if job and (not active or replaced):
                final = "interrupted" if replaced or state == "standby" else state
                self.db.execute(
                    "UPDATE jobs SET state=?,ended=? WHERE id=?",
                    (final, time.time(), job["id"]),
                )
                task = asyncio.create_task(self.encode(job["id"]))
                self.encoder_tasks.add(task)
                task.add_done_callback(self.encoder_tasks.discard)
                job = None
            if active and not job:
                cur = self.db.execute(
                    "INSERT INTO jobs(filename,state,started,elapsed,source_id) VALUES(?,?,?,?,?)",
                    (name, state, time.time() - elapsed, elapsed, source_id),
                )
                job = self.db.execute(
                    "SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)
                ).fetchone()
                self.hits = 0
                self.last_frame = 0
                self.last_alert = 0
            jid = job["id"] if job else None
            if jid:
                self.db.execute(
                    "UPDATE jobs SET state=?,elapsed=?,source_id=COALESCE(?,source_id) WHERE id=?",
                    (state, elapsed, source_id, jid),
                )
            progress = max(
                0, min(1, float(o.get("virtual_sdcard", {}).get("progress", 0)))
            )
            ex = o.get("extruder", {})
            bed = o.get("heater_bed", {})
            self.status.update(
                connected=True,
                state=state,
                filename=name,
                progress=progress,
                nozzle=ex.get("temperature", 0),
                bed=bed.get("temperature", 0),
                nozzle_target=ex.get("target", 0),
                bed_target=bed.get("target", 0),
                eta_seconds=(
                    elapsed * (1 - progress) / progress if progress > 0 else None
                ),
                job_id=jid,
            )
            now = time.time()
            if now - self.last_temp >= 10:
                self.db.execute(
                    "INSERT INTO temperatures VALUES(?,?,?)",
                    (now, self.status["bed"], self.status["nozzle"]),
                )
                self.last_temp = now
            self.db.execute("DELETE FROM temperatures WHERE ts<?", (now - 86400,))
            self.db.commit()
        if active:
            await self.detect(jid, state)
        self.prune()

    async def detect(self, jid, state):
        try:
            jpeg = await self.picture()
            if time.time() - self.last_frame >= 15:
                folder = self.root / "frames" / str(jid)
                folder.mkdir(parents=True, exist_ok=True)
                # Bound frame count to 2400 per print (~10h at 15-second intervals).
                count = len(list(folder.glob("*.jpg")))
                if count < 2400:
                    (folder / f"{count:06d}.jpg").write_bytes(jpeg)
                snap = self.save(f"jobs/{jid}.jpg", jpeg)
                self.db.execute("UPDATE jobs SET snapshot=? WHERE id=?", (snap, jid))
                self.db.commit()
                self.last_frame = time.time()
            if self.classifier is None:

                def load():
                    from vision import Classifier

                    return Classifier()

                self.classifier = await asyncio.to_thread(load)
            score = float(await asyncio.to_thread(self.classifier.score, jpeg))
            if not 0 <= score <= 1:
                raise RuntimeError("invalid detector score")
            self.status["detector"] = "ready"
            self.hits = (
                self.hits + 1
                if score >= float(os.getenv("DETECTION_THRESHOLD", "0.50"))
                and state == "printing"
                else 0
            )
            if self.hits >= 3 and time.time() - self.last_alert >= 120:
                now = time.time()
                cur = self.db.execute(
                    "INSERT INTO alerts(job_id,ts,message) VALUES(?,?,?)",
                    (jid, now, "Возможный сбой печати. Проверьте снимок перед паузой."),
                )
                snap = self.save(f"alerts/{cur.lastrowid}.jpg", jpeg)
                self.db.execute(
                    "UPDATE alerts SET snapshot=? WHERE id=?", (snap, cur.lastrowid)
                )
                self.db.commit()
                self.last_alert = now
                self.hits = 0
        except Exception:  # noqa: BLE001 - Third-party model failures must remain visible without stopping monitoring.
            self.hits = 0
            self.status["detector"] = "error: camera or detector unavailable"

    async def encode(self, jid, attempt=0):
        folder = self.root / "frames" / str(jid)
        if not folder.exists() or not any(folder.glob("*.jpg")):
            return
        target = self.root / "timelapses" / f"{jid}.mp4"
        target.parent.mkdir(exist_ok=True)
        temporary = target.with_suffix(".partial.mp4")
        success = False
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-y",
                "-loglevel",
                "error",
                "-framerate",
                "12",
                "-i",
                str(folder / "%06d.jpg"),
                "-vf",
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(temporary),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), 300)
            except asyncio.CancelledError:
                proc.kill()
                await proc.wait()
                raise
            except TimeoutError:
                proc.kill()
                await proc.wait()
            if proc.returncode == 0:
                temporary.replace(target)
                success = True
                self.db.execute(
                    "UPDATE jobs SET timelapse=? WHERE id=?",
                    ("/api/media/timelapses/" + target.name, jid),
                )
                self.db.commit()
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
            if success:
                for f in folder.glob("*.jpg"):
                    f.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    folder.rmdir()
        if not success and attempt < 2:
            await asyncio.sleep(5)
            await self.encode(jid, attempt + 1)

    def prune(self):
        for row in self.rows(
            "SELECT id FROM alerts ORDER BY id DESC LIMIT -1 OFFSET 500"
        ):
            (self.root / "alerts" / f"{row['id']}.jpg").unlink(missing_ok=True)
            self.db.execute("DELETE FROM alerts WHERE id=?", (row["id"],))
        old = self.rows(
            "SELECT id FROM jobs WHERE ended IS NOT NULL ORDER BY id DESC LIMIT -1 OFFSET 100"
        )
        for row in old:
            jid = row["id"]
            for alert in self.rows("SELECT id FROM alerts WHERE job_id=?", (jid,)):
                (self.root / "alerts" / f"{alert['id']}.jpg").unlink(missing_ok=True)
            (self.root / "jobs" / f"{jid}.jpg").unlink(missing_ok=True)
            (self.root / "timelapses" / f"{jid}.mp4").unlink(missing_ok=True)
            folder = self.root / "frames" / str(jid)
            if folder.exists():
                for f in folder.glob("*.jpg"):
                    f.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    folder.rmdir()
            self.db.execute("DELETE FROM alerts WHERE job_id=?", (jid,))
            self.db.execute("DELETE FROM jobs WHERE id=?", (jid,))
        self.db.commit()

    async def run(self):
        while True:
            try:
                await self.poll()
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                self.status.update(connected=False, state="offline")
            await asyncio.sleep(5)


def create_app(monitor=None):
    token = os.getenv("APP_TOKEN", "")
    if len(token.encode()) < 24:
        raise RuntimeError("APP_TOKEN must contain at least 24 bytes")

    @asynccontextmanager
    async def lifespan(app):
        m = monitor or Monitor()
        app.state.monitor = m
        task = asyncio.create_task(m.run())
        # Recover timelapses whose print ended before a process restart.
        for row in m.rows(
            "SELECT id FROM jobs WHERE ended IS NOT NULL AND timelapse IS NULL"
        ):
            t = asyncio.create_task(m.encode(row["id"]))
            m.encoder_tasks.add(t)
            t.add_done_callback(m.encoder_tasks.discard)
        yield
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for encoder in m.encoder_tasks:
            encoder.cancel()
        if m.encoder_tasks:
            await asyncio.gather(*m.encoder_tasks, return_exceptions=True)
        await m.client.aclose()
        m.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def auth(request: Request):
        if not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            raise HTTPException(
                401, "Bearer token required", headers={"WWW-Authenticate": "Bearer"}
            )

    def m():
        return app.state.monitor

    @app.get("/api/status", dependencies=[Depends(auth)])
    async def status():
        return m().status

    @app.get("/api/temperatures", dependencies=[Depends(auth)])
    async def temperatures():
        return {
            "items": m().rows("SELECT * FROM temperatures ORDER BY ts DESC LIMIT 360")[
                ::-1
            ]
        }

    @app.get("/api/history", dependencies=[Depends(auth)])
    async def history():
        return {
            "items": m().rows(
                "SELECT id,filename,state,started,ended,snapshot,timelapse FROM jobs ORDER BY id DESC LIMIT 100"
            )
        }

    @app.get("/api/alerts", dependencies=[Depends(auth)])
    async def alerts(after: int | None = None):
        return {
            "items": m().rows(
                "SELECT * FROM alerts WHERE id>? ORDER BY id ASC LIMIT 100", (after,)
            )
            if after is not None
            else m().rows("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")[::-1]
        }

    @app.get("/api/camera.jpg", dependencies=[Depends(auth)])
    async def camera():
        try:
            data = (
                m().jpeg
                if m().jpeg and time.time() - m().jpeg_at < 10
                else await m().picture()
            )
            return Response(
                data, media_type="image/jpeg", headers={"Cache-Control": "no-store"}
            )
        except (
            httpx.HTTPError,
            OSError,
            ValueError,
            RuntimeError,
            Image.DecompressionBombError,
        ):
            raise HTTPException(503, "Camera unavailable")

    @app.get("/api/media/{path:path}", dependencies=[Depends(auth)])
    async def media(path: str):
        if not re.fullmatch(r"(?:alerts|jobs)/\d+\.jpg|timelapses/\d+\.mp4", path):
            raise HTTPException(404)
        f = m().root / path
        if not f.is_file():
            raise HTTPException(404)
        return FileResponse(f, headers={"Cache-Control": "private, no-store"})

    @app.get("/api/camera.mjpg", dependencies=[Depends(auth)])
    async def camera_stream():
        url = os.getenv("CAMERA_STREAM_URL", "")

        async def frames():
            if url:
                try:
                    async with m().client.stream(
                        "GET", url, timeout=httpx.Timeout(15, read=30)
                    ) as upstream:
                        upstream.raise_for_status()
                        # Decode the upstream boundary and expose one stable boundary to clients.
                        content_type = upstream.headers.get("content-type", "")
                        if "multipart/x-mixed-replace" not in content_type:
                            return
                        # JPEG scanning bounds memory and tolerates common webcam multipart headers.
                        buffer = bytearray()
                        async for chunk in upstream.aiter_bytes():
                            buffer.extend(chunk)
                            if len(buffer) > 12 * 1024 * 1024:
                                return
                            while True:
                                start = buffer.find(b"\xff\xd8")
                                end = buffer.find(b"\xff\xd9", max(start, 0))
                                if start < 0 or end < 0:
                                    break
                                jpeg = bytes(buffer[start : end + 2])
                                del buffer[: end + 2]
                                yield (
                                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                    + str(len(jpeg)).encode()
                                    + b"\r\n\r\n"
                                    + jpeg
                                    + b"\r\n"
                                )
                except httpx.HTTPError:
                    return
            else:
                while True:
                    try:
                        jpeg = await m().picture()
                    except (
                        httpx.HTTPError,
                        OSError,
                        ValueError,
                        RuntimeError,
                        Image.DecompressionBombError,
                    ):
                        return
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                    await asyncio.sleep(0.5)

        return StreamingResponse(
            frames(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/files", dependencies=[Depends(auth)])
    async def files():
        try:
            result = await m().moon("GET", "/server/files/list?root=gcodes")
            return {
                "items": [
                    {"name": x["path"], "size": x["size"]}
                    for x in result
                    if x["path"].lower().endswith((".gcode", ".gco", ".g"))
                ]
            }
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            raise HTTPException(502, "Moonraker unavailable")

    @app.post("/api/upload", dependencies=[Depends(auth)])
    async def upload(request: Request, name: str):
        if not valid_name(name, basename=True):
            raise HTTPException(400, "Invalid G-code filename")
        maximum = 100 * 1024 * 1024
        with tempfile.TemporaryFile() as f:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise HTTPException(413, "Upload exceeds 100 MiB")
                f.write(chunk)
            if not size:
                raise HTTPException(400, "Empty upload")
            f.seek(0)
            async with m().lock:
                try:
                    existing = await m().moon("GET", "/server/files/list?root=gcodes")
                    if any(x["path"].casefold() == name.casefold() for x in existing):
                        raise HTTPException(
                            409, "File already exists; choose a different name"
                        )
                    printer = await m().moon(
                        "GET", "/printer/objects/query?print_stats"
                    )
                    printing = printer["status"]["print_stats"]
                    if (
                        printing.get("state") in ("printing", "paused")
                        and printing.get("filename", "").casefold() == name.casefold()
                    ):
                        raise HTTPException(409, "Cannot overwrite the active print")
                    r = await m().client.post(
                        m().base + "/server/files/upload",
                        headers={"X-Api-Key": m().api_key},
                        data={"root": "gcodes"},
                        files={"file": (name, f, "application/octet-stream")},
                    )
                    r.raise_for_status()
                except HTTPException:
                    raise
                except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                    raise HTTPException(502, "Upload to Moonraker failed")
        return {"ok": True}

    @app.post("/api/control/{action}", dependencies=[Depends(auth)])
    async def control(action: str, request: Request):
        if action not in ("pause", "resume", "cancel", "start"):
            raise HTTPException(404)
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Expected a JSON object")
        if not isinstance(body, dict):
            raise HTTPException(400, "Expected an object")
        async with m().lock:
            try:
                result = await m().moon("GET", "/printer/objects/query?print_stats")
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                raise HTTPException(502, "Moonraker unavailable")
            p = result["status"]["print_stats"]
            state = p.get("state")
            if action == "start":
                name = body.get("filename")
                if not valid_name(name):
                    raise HTTPException(400, "Invalid filename")
                if state in ("printing", "paused"):
                    raise HTTPException(409, "Printer already has an active job")
                payload = {"filename": name}
            else:
                jid = body.get("job_id")
                current = m().status
                job = (
                    m()
                    .db.execute(
                        "SELECT * FROM jobs WHERE id=? AND ended IS NULL", (jid,)
                    )
                    .fetchone()
                    if isinstance(jid, int) and not isinstance(jid, bool)
                    else None
                )
                if (
                    not job
                    or jid != current["job_id"]
                    or p.get("filename") != job["filename"]
                    or float(p.get("total_duration", 0)) + 2 < job["elapsed"]
                ):
                    raise HTTPException(409, "Job changed; refresh status")
                if job["source_id"]:
                    try:
                        hist = await m().moon(
                            "GET", "/server/history/list?limit=1&order=desc"
                        )
                        latest = hist.get("jobs", [])
                        if (
                            not latest
                            or str(latest[0].get("job_id")) != job["source_id"]
                        ):
                            raise HTTPException(409, "Job changed; refresh status")
                    except httpx.HTTPError:
                        raise HTTPException(502, "Cannot verify current job")
                required = {
                    "pause": ("printing",),
                    "resume": ("paused",),
                    "cancel": ("printing", "paused"),
                }[action]
                if state not in required:
                    raise HTTPException(
                        409, "Command is not valid in the current printer state"
                    )
                payload = {}
            try:
                await m().moon("POST", "/printer/print/" + action, json=payload)
            except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
                raise HTTPException(502, "Printer command failed")
        return {"ok": True}

    return app


def valid_name(name, basename=False):
    return (
        isinstance(name, str)
        and 0 < len(name.encode("utf-8")) <= 240
        and name.lower().endswith((".gcode", ".gco", ".g"))
        and not name.startswith(("/", "."))
        and not any(unicodedata.category(c).startswith("C") or c in "\\:" for c in name)
        and all(part not in ("", ".", "..") for part in name.split("/"))
        and (not basename or "/" not in name)
    )


app = create_app()
