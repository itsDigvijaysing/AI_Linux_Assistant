// AI Linux Assistant overlay — GNOME Shell extension (GJS / ESM, GNOME 48–50).
//
// Single entry point: the top-bar icon starts/stops the engine, shows honest state, and controls it.
// The engine writes live state to $XDG_RUNTIME_DIR/ai-linux/state.json (~2s heartbeat ts); this reads
// it to know if the engine is running and to drive the orb. Control goes back via control.json.

import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Pango from 'gi://Pango';
import cairo from 'gi://cairo';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const DESKTOP_ID = 'ai-linux-assistant.desktop';

const DIR = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'ai-linux']);
const STATE_PATH = GLib.build_filenamev([DIR, 'state.json']);
const CONTROL_PATH = GLib.build_filenamev([DIR, 'control.json']);

const STATES = ['loading', 'idle', 'listening', 'thinking', 'speaking', 'muted', 'off'];
const ACTIVE_STATES = ['loading', 'thinking', 'speaking']; // force the overlay open during a turn
const STALE_MS = 6000;            // state.json older than this => engine not running (heartbeat is 2s)
const IDLE_HIDE_MS = 10000;       // fade out + hide after 10s with no conversation activity
const TRANSCRIPT_FRESH_MS = 1500; // a just-changed transcript still counts as activity this tick
const PIN_TIMEOUT_MS = 30000;
const LOG_MAX = 24;

function writeControl(obj) {
    try {
        GLib.file_set_contents(CONTROL_PATH, JSON.stringify(obj));
    } catch (e) {
        logError(e, 'ai-linux: failed to write control.json');
    }
}

// Settings (model + deep-thinking) — the launcher reads this on Start.
const SETTINGS_DIR = GLib.build_filenamev([GLib.get_user_config_dir(), 'ai-linux']);
const SETTINGS_PATH = GLib.build_filenamev([SETTINGS_DIR, 'settings.json']);
const MODELS = [
    {id: 'qwen3:4b', label: 'Smart — qwen3:4b'},
    {id: 'qwen3:1.7b', label: 'Fast — qwen3:1.7b'},
];
// Listening modes the user can pick from: STT-robust wake words, plus 'always' (continuous, no wake
// word) and 'click' (mic stays off until you click the orb to talk).
const WAKE_WORDS = [
    {id: 'computer', label: 'Computer'},
    {id: 'jarvis', label: 'Jarvis'},
    {id: 'assistant', label: 'Assistant'},
    {id: 'hey linux', label: 'Hey Linux'},
    {id: 'always', label: 'Always listening (no wake word)'},
    {id: 'click', label: 'Click to talk (mic off until clicked)'},
];

function readSettings() {
    const def = {model: 'qwen3:4b', think: false, wake_word: 'computer'};
    try {
        const [ok, c] = GLib.file_get_contents(SETTINGS_PATH);
        if (ok) return {...def, ...JSON.parse(new TextDecoder().decode(c))};
    } catch (e) {}
    return def;
}

function writeSettings(s) {
    try {
        GLib.mkdir_with_parents(SETTINGS_DIR, 0o755);
        GLib.file_set_contents(SETTINGS_PATH, JSON.stringify(s));
    } catch (e) {
        logError(e, 'ai-linux: failed to write settings.json');
    }
}

// ---------------------------------------------------------------- animated orb
// A flowing multi-colour "plasma" orb drawn with Cairo on an St.DrawingArea at ~30fps. Cairo (not a GPU
// shader) renders identically offline and live, so the look is predictable. Each state has its OWN palette,
// motion mode and speed so it reads at a glance: idle drifts slowly, listening ripples, thinking swirls
// fast, speaking pulses energetically. 2-3 colours per state; soft blobs orbit and blend inside the sphere.
const ORB_PARAMS = {
    loading:   {colors: [[1.00, 0.78, 0.25], [1.00, 0.50, 0.12], [1.00, 0.88, 0.45]], speed: 1.8,  amp: 0.07, blobs: 3, mode: 'pulse'},
    idle:      {colors: [[0.28, 0.40, 0.95], [0.45, 0.30, 0.88], [0.16, 0.62, 0.86]], speed: 0.45, amp: 0.04, blobs: 3, mode: 'drift'},
    listening: {colors: [[0.13, 0.85, 0.55], [0.16, 0.70, 0.92], [0.50, 0.92, 0.42]], speed: 1.4,  amp: 0.07, blobs: 3, mode: 'ripple'},
    thinking:  {colors: [[0.56, 0.30, 0.99], [0.88, 0.26, 0.86], [0.36, 0.46, 1.00]], speed: 2.7,  amp: 0.09, blobs: 4, mode: 'swirl'},
    speaking:  {colors: [[1.00, 0.18, 0.55], [1.00, 0.46, 0.22], [0.97, 0.16, 0.78]], speed: 3.6,  amp: 0.15, blobs: 4, mode: 'pulse'},
    muted:     {colors: [[0.46, 0.46, 0.54], [0.36, 0.36, 0.44]], speed: 0.0, amp: 0.0, blobs: 2, mode: 'static'},
    off:       {colors: [[0.40, 0.40, 0.48], [0.32, 0.32, 0.40]], speed: 0.0, amp: 0.0, blobs: 2, mode: 'static'},
};

const Orb = GObject.registerClass(
class Orb extends St.DrawingArea {
    _init() {
        super._init({style_class: 'ai-orb', width: 128, height: 128, reactive: false});
        this._state = 'idle';
        this._t = 0;
        this._timer = 0;
        this.connect('repaint', this._draw.bind(this));
        this.connect('notify::mapped', () => this._sync());   // pause when hidden (perf)
        this.connect('destroy', () => this._stop());
    }

    setState(s) {
        this._state = ORB_PARAMS[s] ? s : 'idle';
        this._sync();
        this.queue_repaint();
    }

    _sync() {
        const p = ORB_PARAMS[this._state];
        if (this.mapped && p.speed > 0) this._start();
        else { this._stop(); this.queue_repaint(); }   // static states (muted/off) still draw one frame
    }

    _start() {
        if (this._timer) return;
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 33, () => { // ~30 fps
            this._t += 0.033;
            this.queue_repaint();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _stop() {
        if (this._timer) { GLib.source_remove(this._timer); this._timer = 0; }
    }

    _draw(area) {
        const cr = area.get_context();
        const [w, h] = area.get_surface_size();
        const p = ORB_PARAMS[this._state];
        const pal = p.colors, c0 = pal[0], t = this._t;
        const cx = w / 2, cy = h / 2, R = Math.min(cx, cy);
        const mn = (x) => Math.min(1, x);

        cr.setOperator(cairo.Operator.CLEAR); cr.paint();
        cr.setOperator(cairo.Operator.OVER);

        // Overall size motion: 'pulse' (speaking/loading) beats with two frequencies for an organic
        // throb; everything else just breathes gently. Idle barely moves; speaking heaves.
        const pulse = (p.mode === 'pulse')
            ? 1 + p.amp * (0.6 * Math.sin(t * p.speed * 1.6) + 0.4 * Math.sin(t * p.speed * 2.7 + 1))
            : 1 + p.amp * Math.sin(t * p.speed);
        const Rs = R * 0.62 * pulse;

        try {
            // soft outer halo (primary colour)
            let gl = new cairo.RadialGradient(cx, cy, Rs * 0.40, cx, cy, R * 0.99);
            gl.addColorStopRGBA(0, c0[0], c0[1], c0[2], 0.32);
            gl.addColorStopRGBA(0.5, c0[0], c0[1], c0[2], 0.09);
            gl.addColorStopRGBA(1, c0[0], c0[1], c0[2], 0.0);
            cr.setSource(gl); cr.arc(cx, cy, R * 0.99, 0, 2 * Math.PI); cr.fill();

            // translucent glass body (dark core -> colour -> soft edge)
            let bd = new cairo.RadialGradient(cx, cy, 0, cx, cy, Rs);
            bd.addColorStopRGBA(0.00, c0[0] * 0.35, c0[1] * 0.35, c0[2] * 0.5, 0.55);
            bd.addColorStopRGBA(0.70, c0[0] * 0.85, c0[1] * 0.85, c0[2] * 0.95, 0.45);
            bd.addColorStopRGBA(1.00, c0[0], c0[1], c0[2], 0.0);
            cr.setSource(bd); cr.arc(cx, cy, Rs, 0, 2 * Math.PI); cr.fill();

            // ---- flowing fluid: soft colour blobs orbiting + blending inside the sphere ----
            cr.save();
            cr.arc(cx, cy, Rs * 0.98, 0, 2 * Math.PI); cr.clip();
            const K = p.blobs;
            for (let k = 0; k < K; k++) {
                const col = pal[k % pal.length];
                const dir = (k % 2) ? -1 : 1;                       // alternate spin -> they cross + mix
                const sp = p.speed * (0.55 + 0.22 * k);
                const ang = t * sp * dir + k * 2.2;
                const orbit = Rs * (0.30 + 0.20 * Math.sin(t * p.speed * 0.7 + k * 1.7));  // breathing radius
                const bx = cx + Math.cos(ang) * orbit;
                const by = cy + Math.sin(ang * 1.25 + k) * orbit * 0.92;                   // elliptical -> organic
                const br = Rs * (0.55 + 0.18 * Math.sin(t * p.speed + k * 2.0));
                let bl = new cairo.RadialGradient(bx, by, 0, bx, by, br);
                bl.addColorStopRGBA(0, mn(col[0] * 1.25), mn(col[1] * 1.25), mn(col[2] * 1.25), 0.55);
                bl.addColorStopRGBA(0.6, col[0], col[1], col[2], 0.20);
                bl.addColorStopRGBA(1, col[0], col[1], col[2], 0.0);
                cr.setSource(bl); cr.arc(cx, cy, Rs, 0, 2 * Math.PI); cr.fill();
            }
            cr.restore();

            // per-mode signature motion
            if (p.mode === 'ripple') {            // listening: concentric rings expanding outward
                for (let i = 0; i < 2; i++) {
                    const ph = ((t * p.speed * 0.5) + i * 0.5) % 1;
                    const rr = Rs * 0.55 + ph * (R * 0.96 - Rs * 0.55);
                    cr.setLineWidth(R * 0.022);
                    cr.setSourceRGBA(pal[1][0], pal[1][1], pal[1][2], 0.40 * (1 - ph));
                    cr.arc(cx, cy, rr, 0, 2 * Math.PI); cr.stroke();
                }
            } else if (p.mode === 'swirl') {      // thinking: a bright arc sweeping round
                const a0 = t * p.speed * 1.4;
                cr.setLineWidth(R * 0.05);
                cr.setSourceRGBA(mn(pal[1][0] * 1.2), mn(pal[1][1] * 1.2), mn(pal[1][2] * 1.3), 0.5);
                cr.arc(cx, cy, Rs * 0.84, a0, a0 + Math.PI * 0.7); cr.stroke();
            }

            // crisp bright rim
            cr.setLineWidth(R * 0.022);
            cr.setSourceRGBA(mn(c0[0] * 1.5), mn(c0[1] * 1.5), mn(c0[2] * 1.7), 0.6);
            cr.arc(cx, cy, Rs * 0.98, 0, 2 * Math.PI); cr.stroke();

            // glassy specular highlight (upper-left) — static, sells the 3D sphere
            const hx = cx - Rs * 0.34, hy = cy - Rs * 0.36;
            let hl = new cairo.RadialGradient(hx, hy, 0, hx, hy, Rs * 0.55);
            hl.addColorStopRGBA(0, 1, 1, 1, 0.55);
            hl.addColorStopRGBA(1, 1, 1, 1, 0.0);
            cr.setSource(hl); cr.arc(hx, hy, Rs * 0.55, 0, 2 * Math.PI); cr.fill();
        } catch (e) {
            cr.setSourceRGBA(c0[0], c0[1], c0[2], 0.9);  // fallback: never invisible
            cr.arc(cx, cy, Rs, 0, 2 * Math.PI); cr.fill();
        }

        cr.$dispose();
    }
});

// ---------------------------------------------------------------- floating overlay
const Overlay = GObject.registerClass(
class Overlay extends St.BoxLayout {
    _init() {
        super._init({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'ai-overlay',
            reactive: true,
            track_hover: true,
        });

        // orb (clickable for click-to-talk), with a mute toggle on its own row just below, right-aligned
        this._orb = new Orb();
        this._orbStack = new St.Widget({
            layout_manager: new Clutter.BinLayout(),
            style_class: 'ai-orbstack',
            x_align: Clutter.ActorAlign.END,
        });
        this._orbStack.add_child(this._orb);
        // orb body click -> _onOrbClick (click-to-talk; wired in enable()). Use the button-release-event
        // signal, not Clutter.ClickAction — that class was removed in the Mutter 48+ gesture refactor
        // (GNOME 50), where `new Clutter.ClickAction()` throws "is not a constructor".
        this._orbStack.reactive = true;
        this._onOrbClick = null;
        this._orbStack.connect('button-release-event', () => {
            if (this._onOrbClick) this._onOrbClick();
            return Clutter.EVENT_STOP;
        });
        this.add_child(this._orbStack);

        this._muteBtn = new St.Button({style_class: 'ai-mute-mini', can_focus: true, accessible_name: 'Mute mic'});
        this._muteBtn.set_child(new St.Icon({
            icon_name: 'audio-input-microphone-muted-symbolic',
            icon_size: 18,
            style_class: 'ai-mute-mini-icon',
        }));
        this._muteBtn.connect('clicked', () => writeControl({action: 'toggle_mute'}));
        const muteRow = new St.Bin({x_expand: true, x_align: Clutter.ActorAlign.END});  // right side, below the orb
        muteRow.set_child(this._muteBtn);
        this.add_child(muteRow);

        // transcript panel (translucent; no Shell blur — its square corners poked past the rounding)
        this._panel = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'ai-panel'});
        this._scroll = new St.ScrollView({style_class: 'ai-scroll', x_expand: true});
        this._scroll.set_policy(St.PolicyType.NEVER, St.PolicyType.AUTOMATIC);
        this._scroll.style = 'max-height: 240px;';
        this._logBox = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'ai-log', x_expand: true});
        this._scroll.set_child(this._logBox);
        this._panel.add_child(this._scroll);
        this._panel.visible = false;
        this.add_child(this._panel);

        this._state = '';
        this._scrollIdleId = 0;
    }

    update(data) {
        const state = STATES.includes(data.state) ? data.state : 'idle';
        this._state = state;
        this._orb.setState(state);
        if (state === 'muted') this._muteBtn.add_style_class_name('active');
        else this._muteBtn.remove_style_class_name('active');
        return state;
    }

    renderLog(entries) {
        this._logBox.destroy_all_children();
        for (const e of entries) {
            const lbl = new St.Label({
                style_class: e.role === 'you' ? 'ai-you' : 'ai-ai',
                x_expand: true,
                text: (e.role === 'you' ? 'You: ' : 'AI: ') + e.text,
            });
            lbl.clutter_text.line_wrap = true;
            lbl.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
            lbl.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
            this._logBox.add_child(lbl);
        }
        this._panel.visible = entries.length > 0;
        this._scrollToBottom();
    }

    _scrollToBottom() {
        if (this._scrollIdleId) GLib.source_remove(this._scrollIdleId);
        this._scrollIdleId = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._scrollIdleId = 0;
            try {
                const adj = this._scroll?.get_vadjustment();
                if (adj) adj.set_value(Math.max(0, adj.get_upper() - adj.get_page_size()));
            } catch (e) {}
            return GLib.SOURCE_REMOVE;
        });
    }

    destroy() {
        if (this._scrollIdleId) { GLib.source_remove(this._scrollIdleId); this._scrollIdleId = 0; }
        super.destroy();
    }
});

// ---------------------------------------------------------------- top-bar indicator + menu
const Indicator = GObject.registerClass(
class Indicator extends PanelMenu.Button {
    _init(cb) {
        super._init(0.0, 'AI Linux Assistant'); // click opens its menu
        this._cb = cb;
        this._dotState = '';

        const box = new St.BoxLayout({style_class: 'ai-indicator-box'});
        this._dot = new St.Widget({style_class: 'ai-indicator state-off', y_align: Clutter.ActorAlign.CENTER});
        box.add_child(this._dot);
        this.add_child(box);

        this._header = new PopupMenu.PopupMenuItem('AI Linux Assistant', {reactive: false});
        this.menu.addMenuItem(this._header);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._startStop = new PopupMenu.PopupMenuItem('Start assistant');
        this._startStop.connect('activate', () => this._cb.startStop());
        this.menu.addMenuItem(this._startStop);

        this._showItem = new PopupMenu.PopupMenuItem('Show / hide overlay');
        this._showItem.connect('activate', () => this._cb.toggleOverlay());
        this.menu.addMenuItem(this._showItem);

        // settings: model + deep-thinking (apply on next Start)
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._settings = readSettings();
        this._modelSub = new PopupMenu.PopupSubMenuMenuItem('Model: ' + this._settings.model);
        this._modelItems = {};
        for (const m of MODELS) {
            const it = new PopupMenu.PopupMenuItem(m.label);
            it.setOrnament(this._settings.model === m.id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
            it.connect('activate', () => this._pickModel(m.id));
            this._modelItems[m.id] = it;
            this._modelSub.menu.addMenuItem(it);
        }
        this.menu.addMenuItem(this._modelSub);
        this._thinkSwitch = new PopupMenu.PopupSwitchMenuItem('Deep thinking (slower)', !!this._settings.think);
        this._thinkSwitch.connect('toggled', (_i, state) => { this._settings.think = state; this._saveSettings(); });
        this.menu.addMenuItem(this._thinkSwitch);

        // wake word: pick from options; applies live to a running engine and persists for next Start
        this._wakeSub = new PopupMenu.PopupSubMenuMenuItem(this._wakeLabel());
        this._wakeItems = {};
        for (const w of WAKE_WORDS) {
            const it = new PopupMenu.PopupMenuItem(w.label);
            it.setOrnament(this._settings.wake_word === w.id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
            it.connect('activate', () => this._pickWake(w.id));
            this._wakeItems[w.id] = it;
            this._wakeSub.menu.addMenuItem(it);
        }
        this.menu.addMenuItem(this._wakeSub);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const logs = new PopupMenu.PopupMenuItem('Open logs');
        logs.connect('activate', () => this._cb.logs());
        this.menu.addMenuItem(logs);
    }

    setState(state, running) {
        const s = running ? (STATES.includes(state) ? state : 'idle') : 'off';
        this._dot.style_class = `ai-indicator state-${s}`;
        this._header.label.text = running ? `AI Linux — ${s}` : 'AI Linux — not running';
        this._startStop.label.text = running ? 'Shutdown assistant' : 'Start assistant';
        this._showItem.setSensitive(running);

        if (s === this._dotState) return;
        this._dotState = s;
        this._dot.remove_all_transitions();
        this._dot.opacity = 255;
        const PULSE = {loading: 700, listening: 1300, thinking: 500, speaking: 380};
        if (PULSE[s]) {
            this._dot.ease({
                opacity: 90,
                duration: PULSE[s],
                mode: Clutter.AnimationMode.EASE_IN_OUT_SINE,
                autoReverse: true,
                repeatCount: -1,
            });
        }
    }

    _pickModel(id) {
        this._settings.model = id;
        this._modelSub.label.text = 'Model: ' + id;
        for (const k in this._modelItems)
            this._modelItems[k].setOrnament(k === id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        this._saveSettings();
    }

    _wakeLabel() {
        const w = WAKE_WORDS.find(x => x.id === this._settings.wake_word);
        return 'Listening: ' + (w ? w.label : this._settings.wake_word);
    }

    _pickWake(id) {
        this._settings.wake_word = id;
        this._wakeSub.label.text = this._wakeLabel();
        for (const k in this._wakeItems)
            this._wakeItems[k].setOrnament(k === id ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        writeSettings(this._settings);                              // persists for next Start
        let ctl, msg;                                              // live to a running engine
        if (id === 'always') { ctl = {mode: 'always'}; msg = 'Always listening.'; }
        else if (id === 'click') { ctl = {mode: 'click'}; msg = 'Click the orb to talk.'; }
        else { ctl = {mode: 'wake', wake_word: id}; msg = 'Wake word: ' + id; }
        writeControl(ctl);
        Main.notify('AI Linux Assistant', msg);
    }

    _saveSettings() {
        writeSettings(this._settings);
        Main.notify('AI Linux Assistant', 'Saved — applies on next Start.');
    }

    destroy() {
        this._dot?.remove_all_transitions();
        super.destroy();
    }
});

export default class AiLinuxOverlayExtension extends Extension {
    enable() {
        this._pinned = false;
        this._activeUntil = 0;
        this._lastYou = '';
        this._lastReply = '';
        this._lastTranscriptTs = 0;
        this._pinTimeoutId = 0;
        this._log = [];
        this._running = false;
        this._mode = '';             // last listening mode from state.json (always|wake|click)
        this._session = false;       // wake-word conversation window open (keeps the overlay up)
        this._shownTarget = false;   // desired overlay visibility (drives fade in/out)

        this._overlay = new Overlay();
        // In click-to-talk mode, tapping the orb starts one listen turn.
        this._overlay._onOrbClick = () => {
            if (this._running && this._mode === 'click') writeControl({action: 'activate'});
        };
        Main.layoutManager.addChrome(this._overlay);
        this._overlay.hide();
        this._reposition();
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._reposition());

        this._indicator = new Indicator({
            startStop: () => this._startStop(),
            toggleOverlay: () => this._toggleOverlay(),
            logs: () => this._openLogs(),
        });
        Main.panel.addToStatusArea('ai-linux-assistant', this._indicator, 0, 'right');

        try {
            const dir = Gio.File.new_for_path(DIR);
            if (!dir.query_exists(null)) dir.make_directory_with_parents(null);
            this._dirMon = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null);
            this._dirMonId = this._dirMon.connect('changed', (_m, f) => {
                if (f && f.get_basename() === 'state.json') this._readState();
            });
        } catch (e) {
            logError(e, 'ai-linux: dir monitor failed');
        }
        this._pollId = GLib.timeout_add(GLib.PRIORITY_DEFAULT_IDLE, 1000, () => {
            this._readState();
            return GLib.SOURCE_CONTINUE;
        });

        this._readState();
    }

    _startStop() {
        if (this._running) {
            writeControl({action: 'quit'});
        } else {
            const app = Gio.DesktopAppInfo.new(DESKTOP_ID);
            if (app) {
                try { app.launch([], null); } catch (e) { Main.notify('AI Linux Assistant', 'Failed to start: ' + e); }
            } else {
                Main.notify('AI Linux Assistant', 'Launcher not found — run "./ai-linux setup", or start it from a terminal with "./ai-linux".');
            }
        }
    }

    _toggleOverlay() {
        if (!this._overlay) return;
        if (this._shownTarget) {
            this._pinned = false;
            this._clearPinTimeout();
            this._fadeOut();
        } else {
            this._pinned = true;
            this._armPinTimeout();
            this._fadeIn();
        }
    }

    _armPinTimeout() {
        this._clearPinTimeout();
        this._pinTimeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, PIN_TIMEOUT_MS, () => {
            this._pinned = false;
            this._pinTimeoutId = 0;
            this._applyVisibility(this._overlay?._state || 'idle');
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearPinTimeout() {
        if (this._pinTimeoutId) {
            GLib.source_remove(this._pinTimeoutId);
            this._pinTimeoutId = 0;
        }
    }

    _openLogs() {
        const logPath = GLib.build_filenamev([GLib.get_user_state_dir(), 'ai-linux', 'run.log']);
        if (Gio.File.new_for_path(logPath).query_exists(null)) {
            try {
                Gio.AppInfo.launch_default_for_uri('file://' + logPath, null);
            } catch (e) {
                Main.notify('AI Linux Assistant', 'Could not open ' + logPath);
            }
        } else {
            Main.notify('AI Linux Assistant', 'No log file yet at ' + logPath + ' — start the assistant first.');
        }
    }

    _pushLog(role, text) {
        const last = this._log[this._log.length - 1];
        if (last && last.role === role && last.text === text) return;
        this._log.push({role, text});
        if (this._log.length > LOG_MAX) this._log.shift();
    }

    _readState() {
        try {
            let running = false;
            let data = null;
            const f = Gio.File.new_for_path(STATE_PATH);
            if (f.query_exists(null)) {
                const [ok, contents] = GLib.file_get_contents(STATE_PATH);
                if (ok) {
                    data = JSON.parse(new TextDecoder().decode(contents));
                    const nowMs = GLib.get_real_time() / 1000; // µs -> ms (wall clock, matches ts)
                    const fresh = data.ts && (nowMs - data.ts) < STALE_MS;
                    running = !!fresh && data.state !== 'off';
                }
            }
            this._running = running;

            if (!running) {
                if (this._log.length) { this._log = []; this._overlay?.renderLog(this._log); }
                this._lastYou = '';
                this._lastReply = '';
                this._session = false;
                this._indicator?.setState('off', false);
                this._applyVisibility('off');
                return;
            }

            this._mode = data.mode || '';
            this._session = !!data.session;
            const state = this._overlay.update(data);
            this._indicator?.setState(state, true);

            const you = (data.you || '').trim();
            const reply = (data.assistant || '').trim();
            let changed = false;
            if (you && you !== this._lastYou) { this._lastYou = you; this._pushLog('you', you); changed = true; }
            if (reply && reply !== this._lastReply) { this._lastReply = reply; this._pushLog('ai', reply); changed = true; }
            if (changed) {
                this._overlay.renderLog(this._log);
                this._lastTranscriptTs = GLib.get_monotonic_time() / 1000;
            }
            this._applyVisibility(state);
        } catch (e) {
            // bad/mid-write tick; keep polling
        }
    }

    _applyVisibility(state) {
        if (!this._overlay) return;
        if (state === 'off') {
            if (!this._pinned) { this._activeUntil = 0; this._fadeOut(); }
            return;
        }
        const nowMs = GLib.get_monotonic_time() / 1000;
        // "someone is speaking" = a turn is in progress (thinking/speaking/loading) or the transcript
        // just changed; plain idle/listening (mic open, waiting) does NOT keep it open.
        const active = ACTIVE_STATES.includes(state) || (nowMs - this._lastTranscriptTs < TRANSCRIPT_FRESH_MS);
        if (active) this._activeUntil = nowMs + IDLE_HIDE_MS;   // keep open until 10s after the last activity
        // Show while: pinned, click-to-talk (orb must stay reachable), a wake conversation window is open,
        // or there was recent activity. In wake mode this means the overlay appears on the wake word and
        // goes away after the session's silence timeout.
        const visible = this._pinned || this._mode === 'click' || this._session || nowMs < this._activeUntil;
        if (visible) this._fadeIn(); else this._fadeOut();
    }

    // fade the overlay in (appears when speech starts) / out (after 10s of silence); idempotent per tick
    _fadeIn() {
        if (!this._overlay || this._shownTarget) return;
        this._shownTarget = true;
        this._overlay.remove_all_transitions();
        if (!this._overlay.visible) {
            this._overlay.opacity = 0;
            this._overlay.show();
            this._reposition();
        }
        this._overlay.ease({opacity: 255, duration: 320, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
    }

    _fadeOut() {
        if (!this._overlay || !this._shownTarget) return;
        this._shownTarget = false;
        this._overlay.remove_all_transitions();
        if (!this._overlay.visible) return;
        this._overlay.ease({
            opacity: 0,
            duration: 300,
            mode: Clutter.AnimationMode.EASE_IN_QUAD,
            onComplete: () => { if (!this._shownTarget && this._overlay) this._overlay.hide(); },
        });
    }

    _reposition() {
        if (!this._overlay) return;
        const mon = Main.layoutManager.primaryMonitor;
        if (!mon) return;
        const [, natW] = this._overlay.get_preferred_width(-1);
        const w = natW || 340;
        let x = mon.x + mon.width - w - 16;
        if (x < mon.x + 8) x = mon.x + 8;
        this._overlay.set_position(x, mon.y + 44);
    }

    disable() {
        if (this._pollId) { GLib.source_remove(this._pollId); this._pollId = null; }
        this._clearPinTimeout();
        if (this._dirMon) {
            if (this._dirMonId) this._dirMon.disconnect(this._dirMonId);
            this._dirMon.cancel();
            this._dirMon = null;
        }
        if (this._monitorsId) { Main.layoutManager.disconnect(this._monitorsId); this._monitorsId = null; }
        if (this._indicator) { this._indicator.destroy(); this._indicator = null; }
        if (this._overlay) {
            Main.layoutManager.removeChrome(this._overlay);
            this._overlay.destroy();
            this._overlay = null;
        }
        this._log = [];
    }
}
