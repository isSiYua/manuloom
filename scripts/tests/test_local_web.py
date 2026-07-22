from __future__ import annotations

import json
import stat
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from vtm_core.local_web import (
    LocalJobManager,
    create_local_server,
    load_or_create_browser_token,
    validate_source_url,
)


class LocalWebTest(unittest.TestCase):
    def test_pairing_token_is_persistent_and_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "token"
            first, written = load_or_create_browser_token(path)
            second, _ = load_or_create_browser_token(path)
            self.assertEqual(first, second)
            self.assertEqual(written, path.resolve())
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_source_url_rejects_non_web_and_embedded_credentials(self):
        self.assertEqual(validate_source_url("https://example.com/article"), "https://example.com/article")
        for invalid in ("file:///etc/passwd", "javascript:alert(1)", "https://user:pass@example.com"):
            with self.assertRaises(ValueError):
                validate_source_url(invalid)

    def test_local_api_requires_token_and_completes_job(self):
        def runner(url: str, vault: Path) -> dict[str, object]:
            return {"status": "pass", "url": url, "note": str(vault / "note.md")}

        manager = LocalJobManager(vault=Path("/tmp/vault"), runner=runner)
        try:
            server = create_local_server(port=0, token="t" * 32, manager=manager)
        except PermissionError:
            self.skipTest("this sandbox does not permit loopback sockets")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            preflight = urllib.request.Request(
                base + "/api/jobs",
                headers={
                    "Origin": "chrome-extension://test-extension-id",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization, content-type",
                },
                method="OPTIONS",
            )
            with urllib.request.urlopen(preflight) as response:
                self.assertEqual(response.status, 204)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "chrome-extension://test-extension-id")

            with self.assertRaises(urllib.error.HTTPError) as cross_origin:
                urllib.request.urlopen(
                    urllib.request.Request(
                        base + "/api/jobs",
                        data=json.dumps({"url": "https://example.com"}).encode(),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + "t" * 32,
                            "Origin": "https://malicious.example",
                        },
                        method="POST",
                    )
                )
            self.assertEqual(cross_origin.exception.code, 403)

            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(
                    urllib.request.Request(
                        base + "/api/jobs",
                        data=json.dumps({"url": "https://example.com"}).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                )
            self.assertEqual(denied.exception.code, 401)

            request = urllib.request.Request(
                base + "/api/jobs",
                data=json.dumps({"url": "https://example.com/article", "title": "Example"}).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + "t" * 32},
                method="POST",
            )
            with urllib.request.urlopen(request) as response:
                submitted = json.load(response)
                self.assertEqual(response.status, 202)
            for _ in range(100):
                query = urllib.request.Request(
                    base + "/api/jobs/" + submitted["id"],
                    headers={"Authorization": "Bearer " + "t" * 32},
                )
                with urllib.request.urlopen(query) as response:
                    job = json.load(response)
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            self.assertEqual(job["status"], "complete")
            self.assertEqual(job["result"]["url"], "https://example.com/article")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_extension_manifest_has_narrow_permissions_and_no_model_keys(self):
        root = Path(__file__).resolve().parents[2]
        manifest = json.loads((root / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["permissions"], ["activeTab", "storage"])
        self.assertNotIn("<all_urls>", json.dumps(manifest))
        self.assertTrue(all(value.startswith("http://127.0.0.1") or value.startswith("http://localhost") for value in manifest["host_permissions"]))
        self.assertNotIn("icons", manifest)
        self.assertNotIn("default_icon", manifest["action"])
        extension_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (root / "browser-extension").iterdir()
            if path.is_file()
        )
        for secret_name in ("DEEPSEEK_API_KEY", "VTM_LLM_API_KEY", "VTM_VISION_API_KEY", "BILIBILI_COOKIE"):
            self.assertNotIn(secret_name, extension_text)


if __name__ == "__main__":
    unittest.main()
