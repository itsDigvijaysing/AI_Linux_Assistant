"""Tools page: the built-in tools and the user's extra MCP servers."""

from __future__ import annotations

import copy
import logging
import re
import shlex

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux.tools_catalog import CATEGORIES, ToolInfo, for_category  # noqa: E402

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")  # becomes mcp.<name>.<tool>
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = {"skills_actions", "shell", "voice", "system_info", "time_info"}  # built-in server names


def _tag(text: str, kind: str = "") -> Gtk.Label:
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER, css_classes=["ail-sp-tag"])
    if kind:
        label.add_css_class(kind)
    return label


def _icon_tile(icon: str, kind: str = "") -> Gtk.Image:
    image = Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER, css_classes=["ail-sp-icon"])
    if kind:
        image.add_css_class(kind)
    return image


def parse_env(text: str) -> tuple[dict[str, str], str | None]:
    """KEY=VALUE per line; blank lines and # comments skipped. Returns (env, error)."""
    env: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not _ENV_RE.match(key):
            return env, f"Line {n}: use KEY=VALUE"
        env[key] = value.strip()
    return env, None


def summarize(server: dict) -> str:
    """Subtitle for a server row: transport and the command it runs (or its URL)."""
    transport = server.get("transport") or "stdio"
    if server.get("url"):
        return f"{transport} · {server['url']}"
    parts = [str(server.get("command") or "")] + [str(a) for a in server.get("args") or []]
    return f"{transport} · {shlex.join(p for p in parts if p)}"


class _Bindings:
    def __init__(self):
        self._ids: list[tuple[object, int]] = []

    def connect(self, obj, signal: str, cb) -> None:
        self._ids.append((obj, obj.connect(signal, cb)))

    def clear(self) -> None:
        for obj, hid in self._ids:
            try:
                obj.disconnect(hid)
            except Exception:
                pass
        self._ids.clear()


class Page(Adw.NavigationPage):
    def __init__(self, ctx):
        super().__init__(title="Tools", tag="tools")
        self.ctx = ctx
        self.settings = ctx.settings
        self._binds = _Bindings()

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self._banner = Adw.Banner(title="Actions are off, so tools that need them will not run",
                                  button_label="Allow Actions")
        self._banner.connect("button-clicked", lambda *_: self._arm())
        view.add_top_bar(self._banner)
        self._toasts = Adw.ToastOverlay()
        prefs = Adw.PreferencesPage()
        prefs.add(self._build_builtin())
        prefs.add(self._build_servers())
        self._toasts.set_child(prefs)
        view.set_content(self._toasts)
        self.set_child(view)

        self._binds.connect(self.settings, "changed::actions_armed", lambda *_: self._sync_armed())
        self._binds.connect(self.settings, "changed::web_access", lambda *_: self._sync_web())
        self._binds.connect(self.settings, "changed::mcp_servers", lambda *_: self._reload_servers())
        self.connect("destroy", lambda *_: self._binds.clear())
        self._sync_armed()
        self._sync_web()

    # built-in tools

    def _build_builtin(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Built-in Tools",
            description="Open a group to see its tools. Tools tagged “Changes your PC” run only while actions "
                        "are allowed on the Safety page, and the risky ones ask you first.",
        )
        self._gated_tags: list[Gtk.Label] = []
        self._web_row: Adw.ExpanderRow | None = None
        for key, (title, icon, blurb) in CATEGORIES.items():
            tools = for_category(key)
            row = Adw.ExpanderRow(title=title, subtitle=blurb)
            row.add_prefix(_icon_tile(icon))
            row.add_suffix(_tag(f"{len(tools)} tool{'s' * (len(tools) != 1)}", "neutral"))
            if key == "web":
                self._web_row = row
                for extra in self._web_rows():
                    row.add_row(extra)
            for tool in tools:
                row.add_row(self._tool_row(tool))
            group.add(row)
        return group

    def _web_rows(self) -> list[Gtk.Widget]:
        switch = Adw.SwitchRow(title="Allow Web Access",
                               subtitle="Weather and quick look-ups ask the internet. Off: nothing leaves "
                                        "this computer except web searches you open in the browser.")
        switch.set_active(bool(self.settings.get("web_access")))
        switch.connect("notify::active", lambda r, _p: self._set("web_access", r.get_active()))
        self._web_switch = switch
        city = Adw.EntryRow(title="Home city, for the weather", show_apply_button=True)
        city.set_text(str(self.settings.get("weather_city") or ""))
        city.connect("apply", lambda r: self._set("weather_city", r.get_text().strip()))
        return [switch, city]

    def _set(self, key: str, value) -> None:
        if self.settings.get(key) == value:
            return
        try:
            self.settings.set(key, value)
        except Exception:
            log.exception("saving %s", key)

    def _sync_web(self) -> None:
        on = bool(self.settings.get("web_access"))
        if self._web_switch.get_active() != on:
            self._web_switch.set_active(on)
        if self._web_row is not None:
            blurb = CATEGORIES["web"][2]
            self._web_row.set_subtitle(blurb if on else f"{blurb}. Weather and look-ups are off")

    def _tool_row(self, tool: ToolInfo) -> Adw.ActionRow:
        row = Adw.ActionRow(title=tool.title, subtitle=GLib.markup_escape_text(tool.description),
                            tooltip_text=tool.qualified)
        row.add_prefix(_icon_tile(tool.icon))
        if tool.online:
            row.add_suffix(_tag("Online", "neutral"))
        if tool.gated:
            tag = _tag("Changes your PC")
            tag.set_tooltip_text("Runs only while actions are allowed on the Safety page")
            row.add_suffix(tag)
            self._gated_tags.append(tag)
        return row

    def _sync_armed(self) -> None:
        armed = bool(self.settings.get("actions_armed"))
        self._banner.set_revealed(not armed)
        for tag in self._gated_tags:
            tag.remove_css_class("neutral" if armed else "")
            if armed:
                tag.remove_css_class("warning")
            else:
                tag.add_css_class("warning")

    def _arm(self) -> None:
        try:
            self.settings.set("actions_armed", True)
            self._toasts.add_toast(Adw.Toast(title="Actions allowed", timeout=2))
        except Exception:
            log.exception("arming actions")

    # MCP servers

    def _build_servers(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Extra Tools (MCP Servers)",
            description="Give the assistant more tools through the Model Context Protocol.",
        )
        self._server_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        group.add(self._server_list)
        group.add(Gtk.Label(label="Changes apply right away. Each server's tools need actions allowed on the "
                                  "Safety page.", xalign=0, wrap=True,
                            css_classes=["dim-label", "caption", "ail-sp-note"]))
        self._reload_servers()
        return group

    def _servers(self) -> list[dict]:
        value = self.settings.get("mcp_servers")
        return [s for s in value if isinstance(s, dict)] if isinstance(value, list) else []

    def _save_servers(self, servers: list[dict]) -> bool:
        try:
            self.settings.set("mcp_servers", servers)
            return True
        except Exception:
            log.exception("saving MCP servers")
            self._toasts.add_toast(Adw.Toast(title="Could not save the server list"))
            return False

    def _reload_servers(self) -> None:
        self._server_list.remove_all()
        for index, server in enumerate(self._servers()):
            try:
                self._server_list.append(self._server_row(index, server))
            except Exception:
                log.exception("server row %r", server)
        add = Adw.ButtonRow(title="Add Server", start_icon_name="list-add-symbolic")
        add.connect("activated", lambda *_: self.open_server_dialog())
        self._server_list.append(add)

    def _server_row(self, index: int, server: dict) -> Adw.ActionRow:
        name = str(server.get("name") or "unnamed")
        row = Adw.ActionRow(title=name, subtitle=summarize(server), use_markup=False,
                            subtitle_lines=1, activatable=True, tooltip_text="Edit server")
        row.add_prefix(_icon_tile("network-server-symbolic"))
        switch = Gtk.Switch(active=server.get("enabled", True) is not False, valign=Gtk.Align.CENTER,
                            tooltip_text="Use this server")
        switch.connect("notify::active", lambda sw, _p: self._set_enabled(name, sw.get_active()))
        delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Remove server", css_classes=["flat", "circular"])
        delete.connect("clicked", lambda *_: self._remove(name))
        row.add_suffix(switch)
        row.add_suffix(delete)
        row.connect("activated", lambda *_: self.open_server_dialog(name))
        return row

    def _find(self, servers: list[dict], name: str) -> int:
        return next((i for i, s in enumerate(servers) if s.get("name") == name), -1)

    def _set_enabled(self, name: str, enabled: bool) -> None:
        servers = self._servers()
        i = self._find(servers, name)
        if i < 0 or (servers[i].get("enabled", True) is not False) == enabled:
            return
        servers[i]["enabled"] = enabled
        self._save_servers(servers)

    def _remove(self, name: str) -> None:
        servers = self._servers()
        i = self._find(servers, name)
        if i < 0:
            return
        removed = servers.pop(i)
        if not self._save_servers(servers):
            return
        toast = Adw.Toast(title=f"Removed {name}", button_label="Undo", timeout=6)
        toast.set_use_markup(False)

        def undo(*_):
            current = self._servers()
            if self._find(current, name) < 0:
                current.insert(min(i, len(current)), removed)
                self._save_servers(current)

        toast.connect("button-clicked", undo)
        self._toasts.add_toast(toast)

    # add / edit dialog

    def open_server_dialog(self, name: str | None = None) -> None:
        servers = self._servers()
        i = self._find(servers, name) if name else -1
        existing = copy.deepcopy(servers[i]) if i >= 0 else None
        try:
            _ServerDialog(self, existing).present(self)
        except Exception:
            log.exception("server dialog")

    def _commit_server(self, original: str | None, server: dict) -> None:
        servers = self._servers()
        i = self._find(servers, original) if original else -1
        if i >= 0:
            keep = {k: v for k, v in servers[i].items() if k not in ("name", "command", "args", "env")}
            keep.update(server)
            servers[i] = keep
        else:
            servers.append(server)
        if self._save_servers(servers):
            verb = "Saved" if i >= 0 else "Added"
            toast = Adw.Toast(title=f"{verb} {server['name']}", timeout=3)
            toast.set_use_markup(False)
            self._toasts.add_toast(toast)

    def taken_names(self, except_name: str | None) -> set[str]:
        return {str(s.get("name")) for s in self._servers() if s.get("name") != except_name} | _RESERVED


class _ServerDialog(Adw.Dialog):
    def __init__(self, page: Page, server: dict | None):
        editing = server is not None
        super().__init__(title="Edit Server" if editing else "Add Server", content_width=460)
        self._page = page
        self._original = server.get("name") if editing else None
        server = server or {}

        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self._save = Gtk.Button(label="Save" if editing else "Add", css_classes=["suggested-action"],
                                sensitive=False)
        self._save.connect("clicked", lambda *_: self._submit())
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        header.pack_start(cancel)
        header.pack_end(self._save)

        self._name = Adw.EntryRow(title="Name", text=str(server.get("name") or ""))
        self._command = Adw.EntryRow(title="Command", text=str(server.get("command") or ""))
        self._args = Adw.EntryRow(title="Arguments", text=shlex.join(str(a) for a in server.get("args") or []))
        main = Adw.PreferencesGroup(description="The program to start, for example npx or uvx, and its arguments.")
        for row in (self._name, self._command, self._args):
            row.connect("changed", lambda *_: self._validate())
            row.connect("entry-activated", lambda *_: self._submit())
            main.add(row)
        self._confirm = Adw.SwitchRow(title="Ask Before Each Use",
                                      subtitle="A server's tools can do anything its program can",
                                      active=server.get("confirm", True) is not False)
        main.add(self._confirm)

        env_group = Adw.PreferencesGroup(title="Environment",
                                         description="Optional. One KEY=VALUE per line, for tokens and settings.")
        self._env = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.CHAR, accepts_tab=False,
                                 css_classes=["ail-sp-env"], top_margin=4, bottom_margin=4)
        env = server.get("env") or {}
        if isinstance(env, dict):
            self._env.get_buffer().set_text("\n".join(f"{k}={v}" for k, v in env.items()))
        self._env.get_buffer().connect("changed", lambda *_: self._validate())
        self._env.update_property([Gtk.AccessibleProperty.LABEL], ["Environment variables"])
        scroller = Gtk.ScrolledWindow(child=self._env, min_content_height=96, max_content_height=180,
                                      propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self._env_frame = Gtk.Frame(child=scroller, css_classes=["card", "ail-sp-env-frame"])
        env_group.add(self._env_frame)
        self._error = Gtk.Label(xalign=0, wrap=True, visible=False, css_classes=["error", "caption", "ail-sp-note"])
        env_group.add(self._error)

        prefs = Adw.PreferencesPage()
        prefs.add(main)
        prefs.add(env_group)
        view = Adw.ToolbarView(content=prefs)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_default_widget(self._save)
        self.set_focus(self._name)
        self._validate()

    def _collect(self) -> tuple[dict | None, str | None, Gtk.Widget | None]:
        name = self._name.get_text().strip()
        command = self._command.get_text().strip()
        if not name:
            return None, None, None
        if not _NAME_RE.match(name):
            return None, "Name: letters, numbers, - and _ only, no spaces", self._name
        if name in self._page.taken_names(self._original):
            return None, f"A server named {name} already exists", self._name
        if not command:
            return None, None, None
        try:
            args = shlex.split(self._args.get_text())
        except ValueError:
            return None, "Arguments: a quote is not closed", self._args
        buf = self._env.get_buffer()
        env, env_error = parse_env(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
        if env_error:
            return None, env_error, self._env_frame
        server = {"name": name, "command": command, "args": args, "enabled": True, "transport": "stdio",
                  "confirm": self._confirm.get_active()}
        if env:
            server["env"] = env
        return server, None, None

    def _validate(self) -> dict | None:
        server, error, culprit = self._collect()
        for widget in (self._name, self._args, self._env_frame):
            widget.remove_css_class("error")
        if culprit is not None:
            culprit.add_css_class("error")
        self._error.set_label(error or "")
        self._error.set_visible(bool(error))
        self._save.set_sensitive(server is not None)
        return server

    def _submit(self) -> None:
        server = self._validate()
        if server is None:
            return
        self._page._commit_server(self._original, server)
        self.close()
