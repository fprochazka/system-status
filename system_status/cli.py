"""CLI of system-status: the window by default, and the tools to write its config."""

import json
import sys
import time
from importlib.metadata import version as get_version
from pathlib import Path
from typing import Annotated

import typer

from system_status.core.config import (
    Config,
    ConfigError,
    dump_config,
    get_default_config_path,
    load_config,
)
from system_status.core.layout import default_sections, unmatched_patterns, unmatched_sensors
from system_status.core.sampler import Sampler, build_sources
from system_status.core.series import format_value
from system_status.gui import launcher

app = typer.Typer(
    name="system-status",
    help="System monitor with graphs of CPU, memory, temperatures, fans, disks and GPUs.",
    add_completion=False,
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
    no_args_is_help=False,
)

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Settings file. Default: $XDG_CONFIG_HOME/system-status/settings.toml",
    ),
]
ThemeOption = Annotated[
    str | None,
    typer.Option("--theme", help="auto, light or dark; overrides the settings file."),
]


def _config_path(config: Path | None) -> Path:
    return config if config is not None else get_default_config_path()


def _check_theme(theme: str | None) -> None:
    if theme is not None and theme not in ("auto", "light", "dark"):
        raise typer.BadParameter("expected auto, light or dark", param_hint="--theme")


InstallDesktopEntryOption = Annotated[
    bool,
    typer.Option(
        "--install-desktop-entry",
        help="Add System Status with its icon to the desktop's application list, then exit",
    ),
]
RemoveDesktopEntryOption = Annotated[
    bool,
    typer.Option(
        "--remove-desktop-entry",
        help="Remove System Status and its icon from the application list, then exit",
    ),
]


# The options that each command takes, so ``--config`` or ``--theme`` given before a command
# name passes on to that command, and is refused where the command has no use for it.
GLOBAL_OPTIONS = {
    "gui": {"config", "theme"},
    "sensors": {"config"},
    "dump-config": {"config"},
    "screenshot": {"config", "theme"},
    "version": set(),
}


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: ConfigOption = None,
    theme: ThemeOption = None,
    install_desktop_entry: InstallDesktopEntryOption = False,
    remove_desktop_entry: RemoveDesktopEntryOption = False,
) -> None:
    """Without a command, open the window."""
    ctx.obj = {"config": config, "theme": theme}
    command = ctx.invoked_subcommand
    if command is None:
        gui(
            ctx,
            config=config,
            theme=theme,
            install_desktop_entry=install_desktop_entry,
            remove_desktop_entry=remove_desktop_entry,
        )
        return
    if install_desktop_entry or remove_desktop_entry:
        typer.echo("Error: --install-desktop-entry and --remove-desktop-entry take no command")
        raise typer.Exit(2)
    for name, value in (("config", config), ("theme", theme)):
        if value is not None and name not in GLOBAL_OPTIONS.get(command, set()):
            typer.echo(f"Error: {command} doesn't take --{name}")
            raise typer.Exit(2)


def _inherit(ctx: typer.Context, name: str, value):
    """Return the command's own option value, or else the one given before the command name."""
    if value is not None:
        return value
    return (ctx.obj or {}).get(name) if isinstance(ctx.obj, dict) else None


def start_gui(config_path: Path, theme: str | None) -> int:
    """Run the window until it closes. Tests replace this to skip opening a window."""
    from system_status.gui.app import run_gui

    return run_gui(config_path, build_sources, theme)


@app.command()
def gui(
    ctx: typer.Context,
    config: ConfigOption = None,
    theme: ThemeOption = None,
    install_desktop_entry: InstallDesktopEntryOption = False,
    remove_desktop_entry: RemoveDesktopEntryOption = False,
) -> None:
    """Open the window."""
    config = _inherit(ctx, "config", config)
    theme = _inherit(ctx, "theme", theme)
    # --theme only applies to the window, which the desktop entry options don't open.
    options = {
        "--theme": theme is not None,
        "--install-desktop-entry": install_desktop_entry,
        "--remove-desktop-entry": remove_desktop_entry,
    }
    if sum(options.values()) > 1:
        names = list(options)
        typer.echo(f"Error: use only one of {', '.join(names[:-1])} and {names[-1]}")
        raise typer.Exit(2)
    _check_theme(theme)
    if remove_desktop_entry:
        removed_entry, removed_icon = launcher.remove()
        if removed_entry is None:
            typer.echo(f"No launcher to remove at {launcher.entry_path()}")
        else:
            typer.echo(f"Removed the launcher {removed_entry}")
        if removed_icon is None:
            typer.echo(f"No icon to remove at {launcher.icon_path()}")
        else:
            typer.echo(f"Removed the icon {removed_icon}")
        return
    if install_desktop_entry:
        try:
            # The launcher would open a window that only shows the error; refuse it now.
            load_config(_config_path(config))
        except ConfigError as error:
            typer.echo(f"Error: {error}", err=True)
            raise typer.Exit(1) from None
        try:
            executable = launcher.system_status_executable()
            entry, icon = launcher.install(executable, config)
        except OSError as error:
            typer.echo(f"Error installing the desktop entry: {error}", err=True)
            raise typer.Exit(1) from None
        typer.echo(f"Wrote the launcher {entry}, which runs:")
        typer.echo(f"  {launcher.exec_line(executable, config)}")
        typer.echo(f"Wrote the icon {icon}")
        return
    raise typer.Exit(start_gui(_config_path(config), theme))


@app.command()
def sensors(
    ctx: typer.Context,
    config: ConfigOption = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON instead of a table.")] = False,
) -> None:
    """List every series of this machine: ID, label, current value, and why it is hidden.

    Labels include the overrides of the settings file. Afterwards, list the patterns of the
    settings file that match no series, and the [sensors] overrides for IDs that no series has.
    """
    path = _config_path(_inherit(ctx, "config", config))
    try:
        settings = load_config(path)
    except ConfigError as error:
        print(f"warning: {error}", file=sys.stderr)
        settings = Config()
    sampler = Sampler(build_sources())
    try:
        # Rates need two samples.
        sampler.sample()
        time.sleep(1)
        sampler.sample()
        snapshot = sampler.snapshot()
    finally:
        sampler.close()
    rows = []
    for info in sampler.catalog.series:
        override = settings.sensors.get(info.id)
        hidden = info.hidden_reason
        if override is not None and override.hidden is not None:
            hidden = "hidden in settings" if override.hidden else None
        rows.append(
            {
                "id": info.id,
                "label": (override.label if override and override.label else info.label),
                "kind": info.kind.value,
                "value": snapshot.latest(info.id),
                "text": format_value(info.kind, snapshot.latest(info.id)),
                "hidden": hidden,
            }
        )
    missing = unmatched_patterns(settings, sampler.catalog)
    unknown = unmatched_sensors(settings, sampler.catalog)
    if as_json:
        result = {"series": rows, "unmatched_patterns": missing, "unmatched_sensors": unknown}
        print(json.dumps(result, indent=2))
        return
    id_width = max(len(row["id"]) for row in rows) if rows else 10
    label_width = max(len(row["label"]) for row in rows) if rows else 10
    for row in rows:
        line = f"{row['id']:<{id_width}}  {row['label']:<{label_width}}  {row['text']:>13}"
        if row["hidden"]:
            line += f"  (hidden: {row['hidden']})"
        print(line.rstrip())
    if missing:
        print(f"\nPatterns in {path} that match no series:", file=sys.stderr)
        for pattern in missing:
            print(f"  {pattern}", file=sys.stderr)
    if unknown:
        print(f"\n[sensors] overrides in {path} for IDs that no series has:", file=sys.stderr)
        for series_id in unknown:
            print(f"  {series_id}", file=sys.stderr)


@app.command("dump-config")
def dump_config_command(
    ctx: typer.Context,
    config: ConfigOption = None,
    write: Annotated[
        bool, typer.Option("--write", help="Write to the settings file instead of stdout.")
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    """Print the generated default layout of this machine as a settings file.

    History, theme and sensor overrides come from the current settings file, if there is one.
    """
    path = _config_path(_inherit(ctx, "config", config))
    try:
        settings = load_config(path)
    except ConfigError as error:
        print(f"warning: {error}", file=sys.stderr)
        settings = Config()
    sampler = Sampler(build_sources())
    sampler.close()
    sections = default_sections(sampler.catalog)
    text = dump_config(
        Config(
            history_seconds=settings.history_seconds,
            interval_seconds=settings.interval_seconds,
            theme=settings.theme,
            sensors=settings.sensors,
            sections=sections,
        )
    )
    if not write:
        print(text, end="")
        return
    if path.exists() and not force:
        print(f"{path} exists; add --force to overwrite it", file=sys.stderr)
        raise typer.Exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(f"Wrote {path}", file=sys.stderr)


@app.command()
def screenshot(
    ctx: typer.Context,
    output: Annotated[Path, typer.Argument(help="PNG file to write.")],
    config: ConfigOption = None,
    theme: ThemeOption = None,
    seconds: Annotated[float, typer.Option(help="How long to sample first.")] = 5.0,
    width: Annotated[int, typer.Option(help="Window width in pixels.")] = 760,
) -> None:
    """Render the window off screen to a PNG, with every section, after sampling a while.

    Useful to check a layout after editing the settings file, without opening a window.
    """
    config = _inherit(ctx, "config", config)
    theme = _inherit(ctx, "theme", theme)
    _check_theme(theme)
    from system_status.gui.app import render_screenshot

    written = render_screenshot(_config_path(config), build_sources, output, seconds, theme, width)
    print(f"Wrote {written}", file=sys.stderr)


@app.command()
def version() -> None:
    """Print the version."""
    print(get_version("system-status"))
