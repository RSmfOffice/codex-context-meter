from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
import tomllib
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DASHBOARD_URL = "http://127.0.0.1:8765"


def discover_team_root(config: dict | None = None) -> Path | None:
    config = config or {}
    module_path = Path(__file__).resolve()
    candidates: list[object] = [
        config.get("team_board_root"),
        os.environ.get("CODEX_TEAM_BOARD_ROOT"),
        os.environ.get("CODEX_TEAM_ROOT"),
        Path.home() / "plugins" / "codex-team-board",
        Path.home() / "CodexTools" / "codex-team-board",
        Path.home() / "source" / "codex-team-board",
        module_path.parents[1].parent / "codex-team-board"
        if len(module_path.parents) > 1
        else None,
        module_path.parents[3] / "codex-team-board"
        if len(module_path.parents) > 3
        else None,
        Path.cwd() / "codex-team-board",
        Path.cwd().parent / "codex-team-board",
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "CodexTeamBoard")

    seen: set[str] = set()
    for value in candidates:
        if not value:
            continue
        try:
            root = Path(value).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            continue
        key = str(root).casefold()
        if key in seen:
            continue
        seen.add(key)
        if (root / "src" / "teamctl.py").is_file():
            return root
    return None


DEFAULT_TEAM_ROOT = discover_team_root()


def _codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return (
        Path(configured).expanduser()
        if configured
        else Path.home() / ".codex"
    )


def _team_home_from_codex_config() -> Path | None:
    config_path = _codex_home() / "config.toml"
    try:
        with config_path.open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        return None
    team_board = servers.get("codexTeamBoard")
    if not isinstance(team_board, dict):
        return None
    env = team_board.get("env")
    if not isinstance(env, dict):
        return None
    value = env.get("CODEX_TEAM_HOME")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve()
    except (OSError, ValueError):
        return None


def discover_team_home(config: dict | None = None) -> Path:
    config = config or {}
    candidates: list[object] = [
        config.get("team_home"),
        os.environ.get("CODEX_TEAM_HOME"),
        _team_home_from_codex_config(),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "CodexTeam")
    candidates.append(Path.home() / ".codex" / "team-board")
    for value in candidates:
        if not value:
            continue
        try:
            candidate = Path(value).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            continue
        return candidate
    raise RuntimeError("无法确定 Codex Team Board Home")


def _endpoint(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("团队看板地址必须是本地 HTTP 地址")
    return parsed.hostname, parsed.port or 80


def dashboard_health(url: str, timeout: float = 0.6) -> dict | None:
    try:
        host, port = _endpoint(url)
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            if not (
                payload.get("ok")
                and payload.get("service") == "codex-team-board"
            ):
                return None
            return payload
        finally:
            connection.close()
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException):
        return None


def _normalized_database(payload: dict) -> Path | None:
    value = payload.get("database")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve()
    except (OSError, ValueError):
        return None


def is_dashboard_available(
    url: str,
    timeout: float = 0.6,
    *,
    expected_home: Path | None = None,
) -> bool:
    payload = dashboard_health(url, timeout=timeout)
    if payload is None:
        return False
    if expected_home is None:
        return True
    expected_database = (expected_home / "team.sqlite").resolve()
    return _normalized_database(payload) == expected_database


def ensure_dashboard(
    config: dict,
    *,
    log_dir: Path,
    open_browser: bool = True,
    log_callback=None,
) -> dict:
    url = str(
        config.get("team_dashboard_url")
        or os.environ.get("CODEX_TEAM_DASHBOARD_URL")
        or DEFAULT_DASHBOARD_URL
    ).strip()
    team_home = discover_team_home(config)
    expected_database = (team_home / "team.sqlite").resolve()
    existing = dashboard_health(url)
    if existing is not None:
        actual_database = _normalized_database(existing)
        if actual_database != expected_database:
            raise RuntimeError(
                "团队看板数据库不匹配："
                f"expected={expected_database} actual={actual_database or 'unknown'}"
            )
        opened = _open_url(url) if open_browser else False
        return {
            "url": url,
            "started": False,
            "opened": opened,
            "team_home": str(team_home),
            "database": str(actual_database),
        }

    root = discover_team_root(config)
    if root is None:
        raise FileNotFoundError(
            "未找到 Codex 团队看板源码。请安装 codex-team-board，"
            "或设置 CODEX_TEAM_BOARD_ROOT / config.json 的 team_board_root。"
        )
    teamctl = root / "src" / "teamctl.py"
    host, port = _endpoint(url)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("团队看板未运行，且配置的地址不是本机地址")

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "team-dashboard.log"
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"starting {url} from {root}\n"
        )
        stream.flush()
        process = subprocess.Popen(
            [
                sys.executable,
                str(teamctl),
                "dashboard",
                "--host",
                host,
                "--port",
                str(port),
                "--no-browser",
            ],
            cwd=str(root),
            env={
                **os.environ,
                "CODEX_TEAM_HOME": str(team_home),
                "PYTHONUTF8": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True,
        )

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"团队看板启动失败，详情见 {log_path}")
        if is_dashboard_available(url, expected_home=team_home):
            health = dashboard_health(url) or {}
            if log_callback:
                log_callback(f"team dashboard started pid={process.pid} url={url}")
            opened = _open_url(url) if open_browser else False
            return {
                "url": url,
                "started": True,
                "opened": opened,
                "pid": process.pid,
                "log_path": str(log_path),
                "team_home": str(team_home),
                "database": str(expected_database),
                "database_id": health.get("database_id"),
                "schema_version": health.get("schema_version"),
            }
        time.sleep(0.2)
    process.terminate()
    raise TimeoutError(f"团队看板在 8 秒内未就绪，详情见 {log_path}")


def _open_url(url: str) -> bool:
    opened = webbrowser.open(url, new=2)
    if not opened:
        raise RuntimeError(f"默认浏览器未能打开：{url}")
    return True
