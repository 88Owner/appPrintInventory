from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuthStrategy:
    name: str
    headers: dict[str, str]


@dataclass(frozen=True)
class AppConfig:
    base_url: str
    token_primary: str
    token_secondary: str
    timeout_seconds: int
    auth_strategies: list[AuthStrategy]


def _render_headers(template_headers: dict[str, str], *, token_primary: str, token_secondary: str) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for k, v in template_headers.items():
        rendered[k] = (
            v.replace("{token_primary}", token_primary)
            .replace("{token_secondary}", token_secondary)
        )
    return rendered


def load_config(config_path: Path) -> AppConfig:
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    base_url = str(raw.get("base_url", "")).rstrip("/")
    token_primary = str(raw.get("token_primary", "")).strip()
    token_secondary = str(raw.get("token_secondary", "")).strip()
    timeout_seconds = int(raw.get("timeout_seconds", 30))

    strategies_raw: list[dict[str, Any]] = list(raw.get("auth_strategies", []))
    strategies: list[AuthStrategy] = []
    for s in strategies_raw:
        name = str(s.get("name", "strategy"))
        headers_t = dict(s.get("headers", {}) or {})
        strategies.append(
            AuthStrategy(
                name=name,
                headers=_render_headers(headers_t, token_primary=token_primary, token_secondary=token_secondary),
            )
        )

    if not base_url:
        raise ValueError("Missing base_url in config.json")
    if not strategies:
        raise ValueError("Missing auth_strategies in config.json")

    return AppConfig(
        base_url=base_url,
        token_primary=token_primary,
        token_secondary=token_secondary,
        timeout_seconds=timeout_seconds,
        auth_strategies=strategies,
    )


def default_config_path() -> Path:
    # repo_root/app/config.py -> repo_root/config.json
    return Path(__file__).resolve().parents[1] / "config.json"
