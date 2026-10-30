"""The keyring helper against a real gnome-keyring on a private D-Bus session (never the user's keyring)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

SCRIPT = textwrap.dedent("""
    import sys
    sys.path.insert(0, sys.argv[1])
    from ai_linux import secret_store as S
    assert S.lookup("todoist") is None
    S.store("todoist", "dummy-token-1", "Phoenix test")
    S.store("todoist", "dummy-token-2", "Phoenix test")
    print(S.lookup("todoist"), S.lookup("other"), S.clear("todoist"), S.lookup("todoist"))
""")


@unittest.skipUnless(shutil.which("gnome-keyring-daemon") and shutil.which("dbus-run-session"),
                     "needs gnome-keyring and dbus")
class SecretStoreTests(unittest.TestCase):
    def test_store_replace_lookup_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run.mkdir(mode=0o700)
            env = {**os.environ, "HOME": tmp, "XDG_DATA_HOME": f"{tmp}/data", "XDG_RUNTIME_DIR": str(run)}
            env.pop("DBUS_SESSION_BUS_ADDRESS", None)
            # our own foreground daemon, stopped by its PID; wait until it owns the name so none is auto-started
            inner = ('printf pw | gnome-keyring-daemon --foreground --unlock --components=secrets >/dev/null 2>&1 & '
                     'k=$!; gdbus wait --session --timeout 10 org.freedesktop.secrets; '
                     '"$@"; status=$?; kill $k; exit $status')
            out = subprocess.run(["dbus-run-session", "--", "sh", "-c", inner, "sh", sys.executable, "-c", SCRIPT,
                                  str(SRC)], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        self.assertEqual(out.stdout.split(), ["dummy-token-2", "None", "1", "None"])


if __name__ == "__main__":
    unittest.main()
