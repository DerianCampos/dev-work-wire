"""Responsive, keyboard-driven screen for the interactive CLI menu."""

from dataclasses import dataclass

import pyfiglet
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import FormattedTextControl, Layout, Window

from devworkwire.presentation.cli.banner import app_version


@dataclass(frozen=True)
class MenuOption:
    number: str
    label: str
    value: str


@dataclass(frozen=True)
class MenuGroup:
    title: str
    description: str
    symbol: str
    color: str
    options: tuple[MenuOption, ...]


def _clip(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _add_line(fragments: list[tuple[str, str]], *parts: tuple[str, str]) -> None:
    if fragments:
        fragments.append(("", "\n"))
    fragments.extend(parts)


def _logo_lines() -> list[str]:
    return pyfiglet.figlet_format("DWIRE", font="ansi_shadow").rstrip().splitlines()


def _render_full(
    fragments: list[tuple[str, str]],
    groups: tuple[MenuGroup, ...],
    selected: int,
    width: int,
) -> None:
    cyan = "fg:#00d7e9"
    muted = "fg:#a5b4c4"
    inner_width = width - 2

    _add_line(fragments, (cyan, "╔" + "═" * inner_width + "╗"))
    for logo_line in _logo_lines():
        text = _clip(logo_line, inner_width - 2).center(inner_width)
        _add_line(fragments, (cyan, "║"), ("fg:#00d7e9 bold", text), (cyan, "║"))
    title = "DevWorkWire · Backlog Workflow Assistant"
    _add_line(fragments, (cyan, "║"), (cyan, title.center(inner_width)), (cyan, "║"))
    _add_line(fragments, (cyan, "║"), (muted, "─" * inner_width), (cyan, "║"))
    legend = f"Developed by alonsovndev · v{app_version()}"
    _add_line(fragments, (cyan, "║"), (muted, " " + legend.ljust(inner_width - 2) + " "), (cyan, "║"))
    _add_line(fragments, (cyan, "╚" + "═" * inner_width + "╝"))
    _add_line(fragments, ("", ""))
    _add_line(fragments, ("fg:#00e587", "✓  System ready."))
    _add_line(fragments, ("bold", "What would you like to do?"))

    option_index = 0
    left_width = 32
    right_width = width - left_width - 3
    for group in groups:
        _add_line(fragments, (f"fg:{group.color}", "╭" + "─" * (width - 2) + "╮"))
        description = group.description.split("\n")
        rows = max(len(group.options), len(description) + 1)
        for row_index in range(rows):
            if row_index == 0:
                left = f" {group.symbol}  {group.title}"
                left_style = f"fg:{group.color} bold"
            else:
                left = f"    {description[row_index - 1]}" if row_index <= len(description) else ""
                left_style = muted
            left = _clip(left, left_width).ljust(left_width)

            if row_index < len(group.options):
                option = group.options[row_index]
                active = option_index == selected
                marker = "❯" if active else " "
                right = f" {marker} [{option.number}]  {option.label}"
                right = _clip(right, right_width).ljust(right_width)
                right_style = f"bg:{group.color} fg:#000000 bold" if active else ""
                option_index += 1
            else:
                right = " " * right_width
                right_style = ""

            _add_line(
                fragments,
                (f"fg:{group.color}", "│"),
                (left_style, left),
                (f"fg:{group.color}", "│"),
                (right_style, right),
                (f"fg:{group.color}", "│"),
            )
        _add_line(fragments, (f"fg:{group.color}", "╰" + "─" * (width - 2) + "╯"))

    exit_style = "bg:#ff5f72 fg:#000000 bold" if selected == option_index else "fg:#ff5f72"
    _add_line(fragments, (exit_style, " ❯ [0]  Exit " if selected == option_index else "   [0]  Exit"))
    _add_line(fragments, (muted, "↑ ↓ Navigate   Enter Select   0–9/s/w Shortcut   Esc Exit   ? Help"))


def _render_compact(
    fragments: list[tuple[str, str]],
    groups: tuple[MenuGroup, ...],
    selected: int,
    width: int,
    height: int,
) -> None:
    _add_line(fragments, ("fg:#00d7e9 bold", _clip("DWIRE · DevWorkWire", width)))
    _add_line(fragments, ("fg:#a5b4c4", _clip("Backlog Workflow Assistant", width)))
    _add_line(fragments, ("fg:#00e587", _clip("✓ System ready.", width)))
    _add_line(fragments, ("bold", _clip("Choose an action:", width)))

    option_index = 0
    for group in groups:
        title = f"{group.symbol} {group.title}"
        _add_line(fragments, (f"fg:{group.color} bold", _clip(title, width)))
        for option in group.options:
            active = option_index == selected
            text = f" {'❯' if active else ' '} [{option.number}] {option.label}"
            style = f"bg:{group.color} fg:#000000 bold" if active else ""
            _add_line(fragments, (style, _clip(text, width).ljust(width)))
            option_index += 1
        if height >= 6 + sum(len(group.options) for group in groups) + 2 * len(groups):
            _add_line(fragments, ("", ""))

    exit_style = "bg:#ff5f72 fg:#000000 bold" if selected == option_index else "fg:#ff5f72"
    exit_text = " ❯ [0] Exit" if selected == option_index else "   [0] Exit"
    _add_line(fragments, (exit_style, _clip(exit_text, width).ljust(width)))
    guide = "↑↓ Move  Enter Select  0–9/s/w Shortcut  Esc Exit  ? Help"
    if width < 64:
        guide = "↑↓ Move  Enter Select  Esc Exit  ? Help"
    _add_line(fragments, ("fg:#a5b4c4", _clip(guide, width)))


def _fit_height(fragments: list[tuple[str, str]], height: int) -> None:
    lines: list[list[tuple[str, str]]] = [[]]
    for style, text in fragments:
        if text == "\n":
            lines.append([])
        else:
            lines[-1].append((style, text))
    if len(lines) <= height:
        return

    selected_line = next(
        (index for index, line in enumerate(lines) if any("bg:" in style for style, _ in line)),
        0,
    )
    if height <= 2:
        visible = lines[selected_line:selected_line + max(1, height)]
    else:
        body = lines[1:-1]
        capacity = height - 2
        selected_body_line = max(0, selected_line - 1)
        first = max(0, min(selected_body_line - capacity // 2, len(body) - capacity))
        visible = [lines[0], *body[first:first + capacity], lines[-1]]

    fragments.clear()
    for line in visible:
        _add_line(fragments, *line)


def render_menu(
    groups: tuple[MenuGroup, ...],
    selected: int,
    width: int,
    height: int,
    show_help: bool = False,
) -> FormattedText:
    """Render the menu for a terminal size without writing to the terminal."""
    fragments: list[tuple[str, str]] = []
    width = max(1, width)
    if show_help:
        if height < 6:
            short_help = (
                "Esc Back · ? Help",
                "↑↓ Move · Enter Select",
                "0–9/s/w Select actions",
            )
            for line in short_help[:max(1, height)]:
                _add_line(fragments, ("fg:#00d7e9", _clip(line, width)))
            return FormattedText(fragments)
        _add_line(fragments, ("fg:#00d7e9 bold", _clip("Keyboard help", width)))
        _add_line(fragments, ("", _clip("↑ / ↓    Move between actions", width)))
        _add_line(fragments, ("", _clip("Enter    Select the highlighted action", width)))
        _add_line(fragments, ("", _clip("0–9/s/w  Select an action directly", width)))
        _add_line(fragments, ("", _clip("Esc      Return to the menu", width)))
        _add_line(fragments, ("", _clip("?        Show this help", width)))
        # Header (6 lines) + blank + every option + Exit.
        if height >= 8 + sum(len(group.options) for group in groups):
            _add_line(fragments, ("", ""))
            for group in groups:
                for option in group.options:
                    _add_line(fragments, ("", _clip(f"[{option.number}] {option.label}", width)))
            _add_line(fragments, ("", _clip("[0] Exit", width)))
    else:
        full_height = len(_logo_lines()) + 5 + sum(
            max(len(group.options), len(group.description.split("\n")) + 1) + 2
            for group in groups
        ) + 5
        if width >= 100 and height >= full_height:
            _render_full(fragments, groups, selected, width)
        else:
            _render_compact(fragments, groups, selected, width, height)
            _fit_height(fragments, height)
    return FormattedText(fragments)


def run_menu(groups: tuple[MenuGroup, ...], exit_value: str) -> str:
    """Return the selected action, or the exit value when the menu is dismissed."""
    options = tuple(option for group in groups for option in group.options)
    selected = 0
    show_help = False
    bindings = KeyBindings()

    def render() -> FormattedText:
        size = get_app().output.get_size()
        return render_menu(groups, selected, size.columns, size.rows, show_help)

    @bindings.add("up")
    def move_up(event) -> None:
        nonlocal selected
        if not show_help:
            selected = (selected - 1) % (len(options) + 1)
            event.app.invalidate()

    @bindings.add("down")
    def move_down(event) -> None:
        nonlocal selected
        if not show_help:
            selected = (selected + 1) % (len(options) + 1)
            event.app.invalidate()

    @bindings.add("enter")
    def select(event) -> None:
        if not show_help:
            event.app.exit(result=options[selected].value if selected < len(options) else exit_value)

    @bindings.add("escape")
    def go_back(event) -> None:
        nonlocal show_help
        if show_help:
            show_help = False
            event.app.invalidate()
        else:
            event.app.exit(result=exit_value)

    @bindings.add("?")
    def toggle_help(event) -> None:
        nonlocal show_help
        show_help = not show_help
        event.app.invalidate()

    for index, option in enumerate(options):
        @bindings.add(option.number)
        def choose_number(event, action_index=index) -> None:
            if not show_help:
                event.app.exit(result=options[action_index].value)

    @bindings.add("0")
    def exit_menu(event) -> None:
        if not show_help:
            event.app.exit(result=exit_value)

    @bindings.add("c-c")
    @bindings.add("c-d")
    def interrupt(event) -> None:
        event.app.exit(result=exit_value)

    window = Window(content=FormattedTextControl(render), wrap_lines=False, always_hide_cursor=True)
    application: Application[str] = Application(
        layout=Layout(window), key_bindings=bindings, full_screen=True
    )
    return application.run()
