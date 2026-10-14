import json
import os
from enum import Enum
from tempfile import NamedTemporaryFile

from devworkwire.config.paths import Paths


class ThemeName(str, Enum):
    dark = "dark"
    light = "light"
    dracula = "dracula"
    solarized = "solarized"
    monochrome = "monochrome"


def load_theme() -> ThemeName:
    path = Paths.user_config_dir() / "theme.json"
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ThemeName.dark
    try:
        return ThemeName(json.loads(contents)["theme"])
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(
            f"Invalid theme settings in {path}; use `dwire config theme NAME` to repair them."
        ) from error


def save_theme(theme: ThemeName) -> None:
    """Atomically save the preference without touching the Jira credentials store."""
    theme = ThemeName(theme)
    path = Paths.user_config_dir() / "theme.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as temporary:
            temporary_path = temporary.name
            json.dump({"theme": theme.value}, temporary)
            temporary.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.unlink(temporary_path)
