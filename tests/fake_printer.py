"""Disposable Moonraker/camera fixture. Never connects to a printer."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

from PIL import Image, ImageDraw

started = time.monotonic()
state = "printing"
filename = "calibration-cube.gcode"
files = {filename: 174328}
job_number = 1


def reference_frame():
    frame = Image.new("RGB", (640, 360), "#202c34")
    pen = ImageDraw.Draw(frame)
    for i in range(-320, 800, 40):
        pen.line((i, 360, i + 220, 135), fill="#435459")
        pen.line((i, 135, i + 220, 360), fill="#435459")
    pen.polygon([(246, 255), (246, 170), (316, 195), (316, 282)], fill="#dd8460")
    pen.polygon([(316, 282), (316, 195), (385, 161), (385, 245)], fill="#a9513a")
    pen.polygon([(246, 170), (315, 137), (385, 161), (316, 195)], fill="#efab81")
    pen.text((16, 14), "ENDER / SIMULATED CAMERA", fill="white")
    buf = BytesIO()
    frame.save(buf, "JPEG")
    return buf.getvalue()


picture = (
    Path(os.environ["FIXTURE_JPEG"]).read_bytes()
    if os.getenv("FIXTURE_JPEG")
    else reference_frame()
)


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, result, status=200):
        data = json.dumps({"result": result}, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        elapsed = int(time.monotonic() - started) + 30
        if path == "/fixture":
            self.reply("ender-watch-simulator")
        elif path == "/snapshot":
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(picture)))
            self.end_headers()
            self.wfile.write(picture)
        elif path == "/printer/objects/query":
            self.reply(
                {
                    "status": {
                        "print_stats": {
                            "state": state,
                            "filename": filename,
                            "total_duration": elapsed,
                        },
                        "virtual_sdcard": {"progress": 0.42},
                        "extruder": {
                            "temperature": 201 + math.sin(elapsed / 5),
                            "target": 200,
                        },
                        "heater_bed": {
                            "temperature": 59 + math.cos(elapsed / 5),
                            "target": 60,
                        },
                    }
                }
            )
        elif path == "/server/history/list":
            self.reply(
                {
                    "jobs": [
                        {
                            "job_id": str(job_number),
                            "filename": filename,
                            "status": "in_progress"
                            if state in ("printing", "paused")
                            else state,
                        }
                    ]
                }
            )
        elif path == "/server/files/list":
            self.reply([{"path": name, "size": size} for name, size in files.items()])
        else:
            self.reply({}, 404)

    def do_POST(self):
        global state, filename, job_number, started
        path = urlsplit(self.path).path
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        transitions = {"pause": "paused", "resume": "printing", "cancel": "cancelled"}
        action = path.rsplit("/", 1)[-1]
        if path == "/server/files/upload":
            from email import policy
            from email.parser import BytesParser

            raw = (
                "Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n"
            ).encode() + body
            for part in BytesParser(policy=policy.default).parsebytes(raw).iter_parts():
                if part.get_filename():
                    files[part.get_filename()] = len(part.get_payload(decode=True))
            self.reply("ok")
        elif path.startswith("/printer/print/") and action in transitions:
            state = transitions[action]
            self.reply("ok")
        elif path == "/printer/print/start":
            filename = json.loads(body)["filename"]
            started, state, job_number = time.monotonic(), "printing", job_number + 1
            self.reply("ok")
        else:
            self.reply({}, 404)


if __name__ == "__main__":
    ThreadingHTTPServer(
        (os.getenv("FIXTURE_BIND", "127.0.0.1"), 17255), Fixture
    ).serve_forever()
