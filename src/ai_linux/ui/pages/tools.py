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

from ai_linux.mcp_presets import PRESETS, Preset  # noqa: E402
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
        self._expanded: set[str] = set()  # server rows kept open across a reload

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
        self._binds.connect(self.settings, "changed::todoist_connected", lambda *_: self._sync_todoist())
        if getattr(ctx, "bus", None) is not None:
            self._binds.connect(ctx.bus, "mcp-tools-changed", lambda *_: self._reload_servers())
        self.connect("destroy", lambda *_: self._binds.clear())
        self._sync_armed()
        self._sync_web()
        self._sync_todoist()

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
            if key == "tasks":
                self._todoist_row = row
                for extra in self._todoist_rows():
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

    # Todoist: the token goes to the keyring, never into the settings file

    def _todoist_rows(self) -> list[Gtk.Widget]:
        self._todoist_status = Adw.ActionRow(title="Not connected")
        self._todoist_off = Gtk.Button(label="Disconnect", valign=Gtk.Align.CENTER)
        self._todoist_off.connect("clicked", lambda *_: self._todoist_disconnect())
        self._todoist_status.add_suffix(self._todoist_off)
        self._todoist_entry = Adw.PasswordEntryRow(title="API token", show_apply_button=True)
        self._todoist_entry.connect("apply", lambda r: self._todoist_save(r.get_text()))
        help_row = Adw.ActionRow(title="Where to find it",
                                 subtitle="In Todoist: Settings, Integrations, Developer, API token. It is kept "
                                          "in your login keyring and checked with Todoist before it is saved.")
        return [self._todoist_status, self._todoist_entry, help_row]

    def _sync_todoist(self) -> None:
        link = getattr(self.ctx, "todoist", None)
        on = bool(self.settings.get("todoist_connected"))
        busy = bool(link and link.busy)
        if link is None:
            self._todoist_status.set_title("Not available")
            self._todoist_status.set_subtitle("The keyring helper didn't load; see the app log")
        else:
            self._todoist_status.set_title("Connected" if on else "Not connected")
            self._todoist_status.set_subtitle("Ask “what's on my Todoist today?”" if on else
                                              "Paste your API token below")
        self._todoist_off.set_visible(on and link is not None)
        self._todoist_off.set_sensitive(not busy)
        self._todoist_entry.set_sensitive(link is not None and not busy)
        blurb = CATEGORIES["tasks"][2]
        self._todoist_row.set_subtitle(blurb if on else f"{blurb}. Not connected")

    def _todoist_done(self, ok: bool, text: str) -> None:
        if ok:
            self._todoist_entry.set_text("")
        self._toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))
        self._sync_todoist()

    def _todoist_save(self, token: str) -> None:
        link = getattr(self.ctx, "todoist", None)
        if link is not None:
            link.save(token, self._todoist_done)
            self._sync_todoist()

    def _todoist_disconnect(self) -> None:
        link = getattr(self.ctx, "todoist", None)
        if link is not None:
            link.disconnect(self._todoist_done)
            self._sync_todoist()

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
        preset = Adw.ButtonRow(title="Add from a Preset", start_icon_name="view-list-bullet-symbolic")
        preset.connect("activated", lambda *_: self._open_presets())
        self._server_list.append(preset)

    def _server_row(self, index: int, server: dict) -> Adw.ExpanderRow:
        name = str(server.get("name") or "unnamed")
        enabled = server.get("enabled", True) is not False
        row = Adw.ExpanderRow(title=name, subtitle=summarize(server), use_markup=False, subtitle_lines=1)
        row.add_prefix(_icon_tile("network-server-symbolic"))
        offered = getattr(getattr(self.ctx, "bus", None), "mcp_tools", {}).get(name) if enabled else None
        off = {str(t) for t in server.get("disabled_tools") or []}
        if offered:
            on = sum(t["name"] not in off for t in offered)
            row.add_suffix(_tag(f"{on} of {len(offered)} tools", "neutral"))
        switch = Gtk.Switch(active=enabled, valign=Gtk.Align.CENTER, tooltip_text="Use this server")
        switch.connect("notify::active", lambda sw, _p: self._set_enabled(name, sw.get_active()))
        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Edit server", css_classes=["flat", "circular"])
        edit.connect("clicked", lambda *_: self.open_server_dialog(name))
        delete = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Remove server", css_classes=["flat", "circular"])
        delete.connect("clicked", lambda *_: self._remove(name))
        for widget in (switch, edit, delete):
            row.add_suffix(widget)
        if offered:
            for tool in offered:
                tool_row = Adw.SwitchRow(title=tool["name"], subtitle=str(tool.get("description") or ""),
                                         use_markup=False, active=tool["name"] not in off)
                tool_row.connect("notify::active",
                                 lambda r, _p, t=tool["name"]: self._set_tool(name, t, r.get_active()))
                row.add_row(tool_row)
        else:
            row.add_row(Adw.ActionRow(title="Its tools show here once it has started" if enabled else
                                      "Turn it on to choose its tools", css_classes=["dim-label"]))
        row.set_expanded(name in self._expanded)
        row.connect("notify::expanded",
                    lambda r, _p: (self._expanded.add if r.get_expanded() else self._expanded.discard)(name))
        return row

    def _set_tool(self, name: str, tool: str, on: bool) -> None:
        """Only switched-on tools reach the model's menu; fewer tools route more accurately."""
        servers = self._servers()
        i = self._find(servers, name)
        if i < 0:
            return
        off = {str(t) for t in servers[i].get("disabled_tools") or []}
        new = off - {tool} if on else off | {tool}
        if new == off:
            return
        servers[i]["disabled_tools"] = sorted(new)
        self._save_servers(servers)

    def _open_presets(self) -> None:
        try:
            _PresetDialog(self).present(self)
        except Exception:
            log.exception("preset dialog")

    def open_preset(self, preset: Preset) -> None:
        server = preset.server()
        name, n = server["name"], 2
        while name in self.taken_names(None):  # a second Files server gets files-2
            name, n = f"{server['name']}-{n}", n + 1
        server["name"] = name
        try:
            _ServerDialog(self, server, editing=False).present(self)
        except Exception:
            log.exception("server dialog")

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


class _PresetDialog(Adw.Dialog):
    """Known servers; picking one opens the Add Server form filled in."""

    def __init__(self, page: Page):
        super().__init__(title="Add from a Preset", content_width=460)
        group = Adw.PreferencesGroup(description="Each needs its program installed. Only read-only tools "
                                                 "start on; you can switch others on after it starts.")
        for preset in PRESETS:
            ok = preset.available()
            row = Adw.ActionRow(title=preset.title, use_markup=False, activatable=True,
                                subtitle=preset.blurb if ok else f"{preset.blurb} Needs {preset.needs}, "
                                                                 "which isn't installed.")
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda *_, p=preset: (self.close(), page.open_preset(p)))
            group.add(row)
        prefs = Adw.PreferencesPage()
        prefs.add(group)
        view = Adw.ToolbarView(content=prefs)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)


class _ServerDialog(Adw.Dialog):
    def __init__(self, page: Page, server: dict | None, editing: bool | None = None):
        editing = server is not None if editing is None else editing
        super().__init__(title="Edit Server" if editing else "Add Server", content_width=460)
        self._page = page
        self._original = server.get("name") if editing else None
        server = server or {}
        self._extra = {} if editing else {k: server[k] for k in ("disabled_tools",) if k in server}

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
        self._page._commit_server(self._original, {**server, **self._extra})
        self.close()
