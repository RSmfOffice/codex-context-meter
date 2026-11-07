from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from codex_context_meter.team_dashboard import (
    discover_team_root,
    ensure_dashboard,
    is_dashboard_available,
)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/health":
            self.send_response(404)
            self.end_headers()
            return
        content = json.dumps(
            {
                "ok": True,
                "service": "codex-team-board",
                "version": "test",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        return


class TeamDashboardTests(unittest.TestCase):
    def test_team_root_discovers_default_plugins_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            root = home / "plugins" / "codex-team-board"
            (root / "src").mkdir(parents=True)
            (root / "src" / "teamctl.py").write_text("", encoding="utf-8")
            with (
                patch("pathlib.Path.home", return_value=home),
                patch.dict(os.environ, {}, clear=True),
            ):
                found = discover_team_root({})
        self.assertEqual(found, root.resolve())

    def test_team_root_uses_environment_and_ignores_stale_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "codex-team-board"
            (root / "src").mkdir(parents=True)
            (root / "src" / "teamctl.py").write_text("", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"CODEX_TEAM_BOARD_ROOT": str(root)},
                clear=False,
            ):
                found = discover_team_root(
                    {"team_board_root": r"D:\missing\old-team-board"}
                )
        self.assertEqual(found, root.resolve())

    def test_health_check_requires_codex_team_service(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            self.assertTrue(
                is_dashboard_available(f"http://127.0.0.1:{port}", timeout=1.0)
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_existing_dashboard_opens_without_starting_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with (
                patch(
                    "codex_context_meter.team_dashboard.is_dashboard_available",
                    return_value=True,
                ),
                patch(
                    "codex_context_meter.team_dashboard.webbrowser.open",
                    return_value=True,
                ) as browser_open,
            ):
                result = ensure_dashboard(
                    {"team_dashboard_url": "http://127.0.0.1:8765"},
                    log_dir=Path(temporary),
                )
        self.assertFalse(result["started"])
        self.assertTrue(result["opened"])
        browser_open.assert_called_once()


if __name__ == "__main__":
    unittest.main()
