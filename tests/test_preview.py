"""'Run it': the dev server must come up on the port the UI waits for, for CRA-style and Vite-style projects."""
import json
import os
import shutil
import stat
import tempfile
import unittest
import urllib.request
from pathlib import Path

from fusionedges import preview

# Stand-in dev servers. 'cra' reads only PORT and ignores arguments (like react-scripts);
# 'vite' reads only --port and ignores PORT.
SERVER_JS = r"""
const http = require("http");
const mode = process.argv[2];
let port;
if (mode === "cra") port = parseInt(process.env.PORT, 10) || 3000;
else { const i = process.argv.indexOf("--port"); port = i > 0 ? parseInt(process.argv[i + 1], 10) : 5173; }
http.createServer((q, r) => r.end("hello from " + mode + " on " + port)).listen(port, "127.0.0.1");
"""


def make_project(kind: str) -> Path:
    root = Path(tempfile.mkdtemp()) / "project"
    (root / "node_modules" / ".bin").mkdir(parents=True)
    (root / "server.js").write_text(SERVER_JS)
    bin_name = "react-scripts" if kind == "cra" else "vite"
    exe = root / "node_modules" / ".bin" / bin_name
    exe.write_text(f'#!/bin/sh\nexec node "{root}/server.js" {kind} "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    script = {"cra": "react-scripts start", "vite": "vite"}[kind]
    (root / "package.json").write_text(json.dumps({"scripts": {"start" if kind == "cra" else "dev": script}}))
    return root


@unittest.skipUnless(shutil.which("npm") and shutil.which("node"), "needs node and npm")
class PreviewTest(unittest.TestCase):
    def tearDown(self):
        preview.stop()

    def check(self, kind):
        root = make_project(kind)
        res = preview.start("run-" + kind, root, timeout=20)
        self.assertIn("url", res, res)
        self.assertNotIn("note", res, "the server did not come up on the port the UI waits for")
        body = urllib.request.urlopen(res["url"], timeout=5).read().decode()
        self.assertIn("hello from " + kind, body)

    def test_create_react_app_style_server_uses_the_port_variable(self):
        self.check("cra")

    def test_vite_style_server_uses_the_port_flag(self):
        self.check("vite")

    def test_two_previews_run_side_by_side_and_the_third_closes_the_oldest(self):
        urls = {}
        for name in ("a", "b", "c"):
            root = make_project("vite")
            res = preview.start("run-" + name, root, timeout=20)
            self.assertIn("url", res, res)
            urls[name] = res["url"]
        self.assertEqual(len({urls["b"], urls["c"]}), 2, "two live previews must not share a port")
        live = preview.status()["runs"]
        self.assertEqual(sorted(live), ["run-b", "run-c"])
        self.assertIn("hello from vite", urllib.request.urlopen(urls["b"], timeout=5).read().decode())
        self.assertIn("hello from vite", urllib.request.urlopen(urls["c"], timeout=5).read().decode())

    def test_run_script_picks_dev_or_start(self):
        self.assertEqual(preview.run_script(make_project("cra")), "start")
        self.assertEqual(preview.run_script(make_project("vite")), "dev")
        self.assertIsNone(preview.run_script(Path(tempfile.mkdtemp())))

    def test_dev_command(self):
        self.assertEqual(preview.dev_command("start", "react-scripts start", 3100), ["npm", "run", "start"])
        self.assertEqual(preview.dev_command("dev", "vite", 3100), ["npm", "run", "dev", "--", "--port", "3100"])
        self.assertEqual(preview.dev_command("dev", "next dev", 3101), ["npm", "run", "dev", "--", "--port", "3101"])


if __name__ == "__main__":
    unittest.main()
