// Shared settings core — the SINGLE implementation used by BOTH the shell extension
// (extension.js) and the preferences window (prefs.js, a separate gjs process).
// Storage contract: ~/.config/ai-linux/settings.json holds ONLY user-touched keys; DEFAULTS are
// display-time fallbacks and are never written back (the launcher treats present keys as explicit
// user choices). Live-apply channels go through the engine bridge's runtime files.
// This module imports only GLib so it is safe in both processes.

import GLib from 'gi://GLib';

export const RUNTIME_DIR = GLib.build_filenamev([GLib.get_user_runtime_dir(), 'ai-linux']);
export const STATE_PATH = GLib.build_filenamev([RUNTIME_DIR, 'state.json']);
export const CONTROL_PATH = GLib.build_filenamev([RUNTIME_DIR, 'control.json']);
export const VOICE_PATH = GLib.build_filenamev([RUNTIME_DIR, 'voice.json']);
export const SETTINGS_DIR = GLib.build_filenamev([GLib.get_user_config_dir(), 'ai-linux']);
export const SETTINGS_PATH = GLib.build_filenamev([SETTINGS_DIR, 'settings.json']);

// think:true mirrors the config default (llm_think: true — reasoning is required for reliable
// tool-calling on qwen3).
export const SETTINGS_DEFAULTS = {
    model: 'qwen3:4b', think: true, wake_word: 'computer',
    voice: 'M1', actions: true, barge_in: true,
    window_control: false,  // GUI-automation window service; off = the D-Bus service never registers
};

export const MODELS = [
    {id: 'qwen3:4b', label: 'Smart — qwen3:4b'},
    {id: 'qwen3:1.7b', label: 'Fast — qwen3:1.7b'},
];
export const VOICES = [
    {id: 'M1', label: 'M1 — male (default)'}, {id: 'M3', label: 'M3 — male'},
    {id: 'M4', label: 'M4 — male'}, {id: 'M5', label: 'M5 — male'},
    {id: 'F1', label: 'F1 — female'}, {id: 'F2', label: 'F2 — female'},
    {id: 'F3', label: 'F3 — female'}, {id: 'F4', label: 'F4 — female'},
    {id: 'F5', label: 'F5 — female'},
];
export const WAKE_WORDS = [
    {id: 'computer', label: 'Computer'}, {id: 'jarvis', label: 'Jarvis'},
    {id: 'assistant', label: 'Assistant'}, {id: 'hey linux', label: 'Hey Linux'},
    {id: 'always', label: 'Always listening (no wake word)'},
    {id: 'click', label: 'Click to talk (mic off until clicked)'},
];

export function readSettings() {
    try {
        const [ok, c] = GLib.file_get_contents(SETTINGS_PATH);
        if (ok) return JSON.parse(new TextDecoder().decode(c));
    } catch (e) {}
    return {};
}

export function writeSettings(s) {
    try {
        GLib.mkdir_with_parents(SETTINGS_DIR, 0o755);
        GLib.file_set_contents(SETTINGS_PATH, JSON.stringify(s));
    } catch (e) {
        logError(e, 'ai-linux: failed to write settings.json');
    }
}

// Read-modify-write: two writer processes share the file, so never flush a cached copy wholesale.
export function saveKey(key, value) {
    const s = readSettings();
    s[key] = value;
    writeSettings(s);
    return s;
}

function writeRuntime(path, obj) {  // best-effort live apply; harmless when the engine is off
    try {
        GLib.mkdir_with_parents(RUNTIME_DIR, 0o700);
        GLib.file_set_contents(path, JSON.stringify(obj));
    } catch (e) {
        logError(e, 'ai-linux: failed to write ' + path);
    }
}

export function writeControl(obj) { writeRuntime(CONTROL_PATH, obj); }
export function writeVoice(id) { writeRuntime(VOICE_PATH, {voice: id}); }

// A wake-word pick maps to a listening-mode command for the running engine.
export function wakeControl(id) {
    if (id === 'always') return {mode: 'always'};
    if (id === 'click') return {mode: 'click'};
    return {mode: 'wake', wake_word: id};
}
