"""Knowledge: your name, facts you keep for good, and what Phoenix learned (each fades after 14 days)."""

from __future__ import annotations

import logging

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ai_linux import knowledge as kn  # noqa: E402

log = logging.getLogger(__name__)


def _fades(days: int | None) -> str:
    if days is None:
        return ""
    return "Fades today" if days == 0 else f"Fades in {days} day{'s' if days != 1 else ''}"


class KnowledgeDialog(Adw.PreferencesDialog):
    def __init__(self, ctx):
        super().__init__(title="Knowledge", search_enabled=False, content_width=620, content_height=680)
        self.ctx = ctx
        self.store = kn.Knowledge()
        self._groups: list[Adw.PreferencesGroup] = []
        self._page = Adw.PreferencesPage(title="Knowledge", icon_name="user-info-symbolic")
        self.add(self._page)
        self._page.add(self._you())
        self._handler = ctx.bus.connect("knowledge-changed", lambda *_: self._refresh())
        self.connect("closed", lambda *_: ctx.bus.disconnect(self._handler))
        self._refresh()

    def _you(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="You", description="Phoenix tells the big model these with each request, so it can sign an "
                                     "email or remember how you like things. It all stays on this computer.")
        s = self.ctx.settings
        name = Adw.EntryRow(title="Your name", show_apply_button=True, text=str(s.get("user_name") or ""))
        name.connect("apply", lambda r: s.set("user_name", r.get_text().strip()) if r.get_text().strip()
                     else s.reset("user_name"))
        group.add(name)
        return group

    # the two lists

    def _refresh(self) -> None:
        for group in self._groups:
            self._page.remove(group)
        self._groups = [self._kept_group(), self._learned_group(), self._note()]
        for group in self._groups:
            self._page.add(group)

    def _kept_group(self) -> Adw.PreferencesGroup:
        kept = self.store.kept()
        group = Adw.PreferencesGroup(title="Always Known",
                                     description="Facts you keep. They stay until you delete them.")
        group.set_header_suffix(Gtk.Label(label=f"{len(kept)} of {kn.KEPT_MAX}", css_classes=["dim-label"]))
        for fact in kept:
            row = Adw.EntryRow(title="Fact", text=fact.text, show_apply_button=True, max_length=kn.FACT_CHARS)
            row.connect("apply", lambda r, f=fact: self._do(self.store.update(f.id, text=r.get_text())))
            row.add_suffix(self._button("view-pin-symbolic", "Let it fade like a learned fact",
                                        lambda f=fact: self._do(self.store.update(f.id, kept=False))))
            row.add_suffix(self._button("user-trash-symbolic", "Delete",
                                        lambda f=fact: self._do(self.store.delete(f.id))))
            group.add(row)
        add = Adw.EntryRow(title="Add a fact, such as “I work night shifts”", show_apply_button=True,
                           max_length=kn.FACT_CHARS, sensitive=len(kept) < kn.KEPT_MAX)
        add.connect("apply", lambda r: self._add(r))
        group.add(add)
        return group

    def _learned_group(self) -> Adw.PreferencesGroup:
        learned = self.store.learned()
        group = Adw.PreferencesGroup(
            title="Learned",
            description=f"Picked up from your conversations. Each fades after {kn.LEARNED_DAYS} days unless you "
                        "keep it; you can also say “remember that…” or “forget that…”.")
        suffix = Gtk.Box(spacing=8)
        suffix.append(Gtk.Label(label=f"{len(learned)} of {kn.LEARNED_MAX}", css_classes=["dim-label"]))
        if learned:
            clear = Gtk.Button(label="Forget All", css_classes=["flat", "destructive-action"], valign=Gtk.Align.CENTER)
            clear.connect("clicked", lambda *_: self._do(self.store.clear_learned()))
            suffix.append(clear)
        group.set_header_suffix(suffix)
        if not learned:
            group.add(Adw.ActionRow(title="Nothing learned yet", subtitle="Mention things about yourself, or say "
                                    "“remember that I prefer short answers”.", css_classes=["dim-label"]))
        for fact in learned:
            row = Adw.ActionRow(title=GLib.markup_escape_text(fact.text), subtitle=_fades(fact.days_left()),
                                title_lines=3)
            row.add_suffix(self._button("view-pin-symbolic", "Keep it for good",
                                        lambda f=fact: self._do(self.store.update(f.id, kept=True))))
            row.add_suffix(self._button("user-trash-symbolic", "Delete",
                                        lambda f=fact: self._do(self.store.delete(f.id))))
            group.add(row)
        return group

    def _note(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup()
        group.add(Gtk.Label(
            label=f"Saved in {str(kn.KNOWLEDGE_FILE).replace(GLib.get_home_dir(), '~', 1)}. At most {kn.KEPT_MAX} kept and {kn.LEARNED_MAX} learned facts, "
                  "about 600 words of the model's instructions, so a small model isn't overwhelmed.",
            wrap=True, xalign=0, css_classes=["dim-label", "caption"]))
        return group

    # edits

    @staticmethod
    def _button(icon: str, tip: str, cb) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat"])
        btn.update_property([Gtk.AccessibleProperty.LABEL], [tip])
        btn.connect("clicked", lambda *_: cb())
        return btn

    def _add(self, row: Adw.EntryRow) -> None:
        fact, why = self.store.add(row.get_text(), kept=True)
        self._do(why if fact is None else "")

    def _do(self, why: str | None) -> None:
        if why:
            self.add_toast(Adw.Toast(title=why, timeout=3))
        GLib.idle_add(lambda: self._refresh() and False)  # not while the clicked row is being torn down


def show_knowledge(parent, ctx) -> KnowledgeDialog | None:
    try:
        dialog = KnowledgeDialog(ctx)
        dialog.present(parent)
        return dialog
    except Exception:
        log.exception("knowledge dialog failed")
        return None
