"""Store one secret in the server's /etc/seoagent/env, without it ever being seen.

    python scripts/set_server_secret.py SERP_API_KEY            # hidden prompt
    python scripts/set_server_secret.py SERP_API_KEY --window   # a small window instead

The value is asked for, masked, on this machine and travels to the
server on ssh's standard input, so it is never in a chat, a command line
(`ps`), a shell history or a log. On the server it replaces any earlier line
of the same name, keeping the file root:seoagent 0640 like app-setup.sh made
it. The service is not restarted: settings that use the secret are switched
on separately, once it is in place.

The ssh options are the ones this server's network path needs: the default
post-quantum key exchange stalls on its large packets.
"""
from __future__ import annotations

import base64
import getpass
import re
import subprocess
import sys

HOST = "seo-deploy"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "KexAlgorithms=curve25519-sha256", HOST]

# Runs as root on the server. Reads the value from stdin; never prints it.
REMOTE = r'''
import grp, os, re, sys
name = sys.argv[1]
value = sys.stdin.read().strip()
if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", name):
    sys.exit("bad name")
if not value or any(c in value for c in "\r\n\0"):
    sys.exit("the value is empty or spans lines; nothing was written")
path = "/etc/seoagent/env"
with open(path, encoding="utf-8") as handle:
    lines = [line for line in handle.read().splitlines() if not line.startswith(name + "=")]
lines.append(f"{name}={value}")
temporary = path + ".new"
with open(temporary, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
os.chown(temporary, 0, grp.getgrnam("seoagent").gr_gid)
os.chmod(temporary, 0o640)
os.replace(temporary, path)
print(f"stored {name} ({len(value)} characters) in {path}")
'''


def ask_in_window(name: str) -> str:
    """A small window with one masked field, for someone who would rather not use a terminal.

    Tk binds Ctrl+V to the Latin "v", so with a Persian keyboard layout it
    does nothing. The window has a Paste button that reads the clipboard
    directly, and Ctrl+V is matched by the physical key instead.
    """
    import tkinter as tk

    result = {"value": ""}
    root = tk.Tk()
    root.title("HoshyarSEO")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    tk.Label(root, text=f"Copy the {name}, then press Paste (or Ctrl+V), then OK.",
             padx=16, pady=10).pack()
    entry = tk.Entry(root, show="*", width=46)
    entry.pack(padx=16)
    entry.focus_set()
    status = tk.Label(root, text="", fg="#555555", pady=6)
    status.pack()

    def paste(_event=None):
        try:
            text = root.clipboard_get()
        except tk.TclError:
            status.config(text="The clipboard is empty. Copy the key first.")
            return "break"
        entry.delete(0, tk.END)
        entry.insert(0, text.strip())
        status.config(text=f"Pasted: {len(text.strip())} characters.")
        return "break"

    def done(_event=None):
        result["value"] = entry.get().strip()
        root.destroy()

    # 86 is the V key on Windows whatever the layout says it types.
    entry.bind("<Control-KeyPress>", lambda event: paste() if event.keycode == 86 else None)
    entry.bind("<Button-3>", paste)
    entry.bind("<Return>", done)

    buttons = tk.Frame(root, pady=10)
    buttons.pack()
    tk.Button(buttons, text="Paste", width=10, command=paste).pack(side=tk.LEFT, padx=4)
    tk.Button(buttons, text="OK", width=10, command=done).pack(side=tk.LEFT, padx=4)
    tk.Button(buttons, text="Cancel", width=10, command=root.destroy).pack(side=tk.LEFT, padx=4)

    root.mainloop()
    return result["value"]


def tell(message: str, *, window: bool) -> None:
    print(message)
    if window:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showinfo("HoshyarSEO", message, parent=root)
        root.destroy()


def main() -> None:
    args = [arg for arg in sys.argv[1:] if arg != "--window"]
    window = "--window" in sys.argv[1:]
    if len(args) != 1 or not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", args[0]):
        sys.exit("Usage: python scripts/set_server_secret.py NAME [--window]   (NAME in CAPITALS, e.g. SERP_API_KEY)")
    name = args[0]
    if window:
        value = ask_in_window(name)
    else:
        value = getpass.getpass(f"Paste the value for {name} (hidden), then Enter: ").strip()
    if not value:
        tell("Nothing entered; nothing was sent.", window=window)
        sys.exit(1)
    code = base64.b64encode(REMOTE.encode("utf-8")).decode("ascii")
    remote = f'sudo -n python3 -c "$(echo {code} | base64 -d)" {name}'
    result = subprocess.run([*SSH, remote], input=value + "\n", text=True, capture_output=True, timeout=60)
    if result.returncode != 0:
        tell(f"Not stored: {result.stderr.strip() or result.stdout.strip()}", window=window)
        sys.exit(1)
    if window:
        # The key was copied to get here; it should not stay on the clipboard.
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.update()
        root.destroy()
    tell(result.stdout.strip(), window=window)


if __name__ == "__main__":
    main()
