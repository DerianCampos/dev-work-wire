import os
from contextvars import ContextVar
from dataclasses import dataclass

import typer
from InquirerPy.utils import InquirerPyStyle, get_style

from devworkwire.config.theme_config import ThemeName


@dataclass(frozen=True)
class ThemePalette:
    name: ThemeName
    foreground: str = ""
    background: str = ""
    accent: str = ""
    muted: str = ""
    work: str = ""
    success: str = ""
    warning: str = ""
    local: str = ""
    error: str = ""
    selection_foreground: str = ""

    @property
    def base_style(self) -> str:
        return f"fg:{self.foreground} bg:{self.background}" if self.foreground else ""

    def text_style(self, role: str) -> str:
        color = getattr(self, role)
        return f"fg:{color}" if color else ""

    def selected_style(self, role: str) -> str:
        color = getattr(self, role)
        return (
            f"bg:{color} fg:{self.selection_foreground} bold"
            if color
            else "reverse bold"
        )


PALETTES = {
    ThemeName.dark: ThemePalette(
        name=ThemeName.dark,
        foreground="#e2e8f0",
        background="#141b24",
        accent="#67d5e2",
        muted="#9aa9bb",
        work="#6db6f2",
        success="#73d6a2",
        warning="#e9be70",
        local="#b9a0e8",
        error="#f28b98",
        selection_foreground="#141b24",
    ),
    ThemeName.light: ThemePalette(
        name=ThemeName.light,
        foreground="#172033",
        background="#f8fafc",
        accent="#006b78",
        muted="#526176",
        work="#005ea8",
        success="#176b3a",
        warning="#885500",
        local="#7131a8",
        error="#b42336",
        selection_foreground="#ffffff",
    ),
    ThemeName.dracula: ThemePalette(
        name=ThemeName.dracula,
        foreground="#f8f8f2",
        background="#282a36",
        accent="#8be9fd",
        muted="#b4b9d0",
        work="#8be9fd",
        success="#50fa7b",
        warning="#f1fa8c",
        local="#bd93f9",
        error="#ff5555",
        selection_foreground="#282a36",
    ),
    ThemeName.solarized: ThemePalette(
        name=ThemeName.solarized,
        foreground="#eee8d5",
        background="#002b36",
        accent="#2aa198",
        muted="#93a1a1",
        work="#3298df",
        success="#859900",
        warning="#b58900",
        local="#b18cce",
        error="#f07169",
        selection_foreground="#002b36",
    ),
    ThemeName.monochrome: ThemePalette(name=ThemeName.monochrome),
}

active_theme: ContextVar[ThemeName] = ContextVar("active_theme", default=ThemeName.dark)


def current_palette() -> ThemePalette:
    name = ThemeName.monochrome if os.environ.get("NO_COLOR") else active_theme.get()
    return PALETTES[name]


def prompt_style() -> InquirerPyStyle:
    palette = current_palette()
    roles = {
        "questionmark": "accent",
        "answermark": "accent",
        "answer": "work",
        "input": "foreground",
        "question": "foreground",
        "answered_question": "muted",
        "instruction": "muted",
        "long_instruction": "muted",
        "pointer": "accent",
        "checkbox": "success",
        "separator": "muted",
        "skipped": "muted",
        "validator": "error",
        "marker": "success",
        "fuzzy_prompt": "accent",
        "fuzzy_info": "muted",
        "fuzzy_border": "muted",
        "fuzzy_match": "local",
        "spinner_pattern": "accent",
        "spinner_text": "foreground",
    }
    styles = {name: palette.text_style(role) for name, role in roles.items()}
    # Explicit entries prevent InquirerPy's environment colors leaking into monochrome.
    styles[""] = palette.base_style
    return get_style(styles)


def themed_echo(message: str, role: str = "accent") -> None:
    palette = current_palette()
    if palette.name == ThemeName.monochrome:
        typer.echo(message)
        return
    color = getattr(palette, role)
    foreground = (
        tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
        if color
        else None
    )
    background = (
        tuple(int(palette.background[index : index + 2], 16) for index in (1, 3, 5))
        if palette.background
        else None
    )
    typer.secho(message, fg=foreground, bg=background)
