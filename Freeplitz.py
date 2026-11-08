import os, sys, time, threading, json, shutil, hashlib, secrets, tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

APP = "FreezeLite"
DATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CFG = os.path.join(DATA, "config.json")
SNAP = os.path.join(DATA, "snapshot")
os.makedirs(DATA, exist_ok=True)

BLOCKED = {os.path.normcase(p) for p in [
    os.path.expanduser("~"), os.environ.get("WINDIR", "C:\\Windows"),
    os.environ.get("ProgramFiles", "C:\\Program Files"), DATA]}


def hpw(pw, salt):
    return hashlib.sha256((salt + pw).encode()).hexdigest()


def load():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {"folder": os.path.join(os.path.expanduser("~"), "Downloads"),
                "frozen": False, "salt": "fl", "hash": hpw("1718", "fl")}


def save(c):
    with open(CFG, "w") as f:
        json.dump(c, f)


def safe_folder(p):
    p = os.path.normcase(os.path.abspath(p))
    return (os.path.splitdrive(p)[1] not in ("", "\\", "/")
            and p not in BLOCKED and not p.startswith(os.path.normcase(DATA)))


def same(a, b):
    try:
        sa, sb = os.stat(a), os.stat(b)
        return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)
    except OSError:
        return False


def sync(src, dst):
    """Make dst identical to src (copy missing/changed, delete extras)."""
    os.makedirs(dst, exist_ok=True)
    want = set(os.listdir(src))
    for name in os.listdir(dst):
        if name not in want:
            p = os.path.join(dst, name)
            shutil.rmtree(p, True) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    for name in want:
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if os.path.isdir(s) and not os.path.islink(s):
            if os.path.exists(d) and not os.path.isdir(d):
                os.remove(d)
            sync(s, d)
        else:
            if os.path.isdir(d):
                shutil.rmtree(d, True)
            if not same(s, d):
                try:
                    shutil.copy2(s, d)
                except OSError:
                    pass  # file in use; skipped


def purge_new(snap, dst):
    """Delete anything in dst that was not in the snapshot."""
    try:
        have = set(os.listdir(snap))
        for name in os.listdir(dst):
            s_, d_ = os.path.join(snap, name), os.path.join(dst, name)
            try:
                if name not in have:
                    if os.path.isdir(d_) and not os.path.islink(d_):
                        shutil.rmtree(d_, True)
                    else:
                        os.remove(d_)
                elif os.path.isdir(d_) and os.path.isdir(s_):
                    purge_new(s_, d_)
            except OSError:
                pass  # still downloading / in use; retried next pass
    except OSError:
        pass


def watch_forever():
    """Every 2 seconds, delete new files from the frozen folder."""
    while True:
        c = load()
        if c["frozen"] and os.path.isdir(SNAP) and os.path.isdir(c["folder"]):
            purge_new(SNAP, c["folder"])
        time.sleep(2)


def log(msg):
    try:
        with open(os.path.join(DATA, "log.txt"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def scan(snap, dst):
    """Full check: compare the folder with the snapshot."""
    def rel(root):
        out = set()
        for d, dirs, files in os.walk(root):
            for n in dirs + files:
                out.add(os.path.relpath(os.path.join(d, n), root))
        return out
    a, b = rel(snap), rel(dst)
    changed = [p for p in a & b
               if os.path.isfile(os.path.join(snap, p)) and not same(os.path.join(snap, p), os.path.join(dst, p))]
    return sorted(b - a), sorted(a - b), changed


def restore(cfg):
    """Check everything against the snapshot, fix it, return (new, missing, changed)."""
    if cfg["frozen"] and os.path.isdir(SNAP) and cfg["folder"]:
        new, missing, changed = scan(SNAP, cfg["folder"])
        sync(SNAP, cfg["folder"])
        log(f"restore: removed {len(new)} new, brought back {len(missing)} missing, reverted {len(changed)} changed")
        return len(new), len(missing), len(changed)
    return None


def count_files(p):
    try:
        return sum(len(f) for _, _, f in os.walk(p))
    except OSError:
        return 0


def set_startup(on):
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if on:
            exe = sys.executable if getattr(sys, "frozen", False) else f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'{exe} --autorestore')
        else:
            try:
                winreg.DeleteValue(k, APP)
            except FileNotFoundError:
                pass
        winreg.CloseKey(k)
        return True
    except Exception:
        return False


class UI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP)
        self.geometry("460x400")
        self.resizable(False, False)
        self.cfg = load()
        self.status = tk.Label(self, font=("Segoe UI", 16, "bold"), pady=12)
        self.status.pack(fill="x")
        self.path = tk.Label(self, wraplength=420, fg="#555")
        self.path.pack()
        for text, cmd in [("Choose protected folder", self.choose),
                          ("Freeze (take snapshot)", self.freeze),
                          ("Thaw (password)", self.thaw),
                          ("Restore now", self.restore_now),
                          ("Run restore at Windows login", self.startup)]:
            tk.Button(self, text=text, command=cmd, width=34).pack(pady=3)
        unclean = not self.cfg.get("closed_clean", True)
        self.cfg["closed_clean"] = False
        save(self.cfg)
        res = restore(self.cfg)  # full check on startup
        lc = self.cfg.get("last_closed")
        txt = ""
        if lc:
            txt = f"Last closed: {lc['time']} ({'FROZEN' if lc['frozen'] else 'THAWED'}, {lc['files']} files)"
        if unclean:
            txt += "\nWas NOT closed normally (restart/shutdown/crash)"
        if res:
            txt += f"\nStartup check: removed {res[0]}, restored {res[1]}, reverted {res[2]}"
        tk.Label(self, text=txt, fg="#555", justify="center").pack()
        threading.Thread(target=watch_forever, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.try_close)
        self.refresh()

    def try_close(self):
        pw = self.ask_pw("Password to close:")
        if pw is not None and hpw(pw, self.cfg["salt"]) == self.cfg["hash"]:
            self.cfg["last_closed"] = {"time": time.strftime("%Y-%m-%d %H:%M"),
                                       "frozen": self.cfg["frozen"],
                                       "files": count_files(self.cfg["folder"])}
            self.cfg["closed_clean"] = True
            save(self.cfg)
            log(f"closed with password while {'frozen' if self.cfg['frozen'] else 'thawed'}")
            self.destroy()
        elif pw is not None:
            messagebox.showerror(APP, "Wrong password.")

    def refresh(self):
        f = self.cfg["frozen"]
        self.status.config(text="FROZEN" if f else "THAWED",
                           bg="#bde0fe" if f else "#ffd6a5")
        self.path.config(text=self.cfg["folder"] or "No folder chosen")

    def choose(self):
        if self.cfg["frozen"]:
            return messagebox.showwarning(APP, "Thaw first.")
        p = filedialog.askdirectory()
        if p:
            if not safe_folder(p):
                return messagebox.showerror(APP, "That folder is not allowed (drive root / system / home).")
            self.cfg["folder"] = p
            save(self.cfg)
            self.refresh()

    def ask_pw(self, msg):
        pw = simpledialog.askstring(APP, msg, show="*")
        return pw

    def set_pw(self):
        if self.cfg["hash"]:
            old = self.ask_pw("Current password:")
            if old is None or hpw(old, self.cfg["salt"]) != self.cfg["hash"]:
                return messagebox.showerror(APP, "Wrong password.")
        new = self.ask_pw("New password:")
        if new:
            self.cfg["salt"] = secrets.token_hex(8)
            self.cfg["hash"] = hpw(new, self.cfg["salt"])
            save(self.cfg)
            messagebox.showinfo(APP, "Password set.")

    def freeze(self):
        if not self.cfg["folder"]:
            return messagebox.showwarning(APP, "Choose a folder first.")
        if self.cfg["frozen"]:
            return
        sync(self.cfg["folder"], SNAP)
        self.cfg["frozen"] = True
        save(self.cfg)
        set_startup(True)
        self.refresh()
        messagebox.showinfo(APP, "Frozen. Anything new in this folder is removed on restart.")

    def thaw(self):
        pw = self.ask_pw("Password:")
        if pw is not None and hpw(pw, self.cfg["salt"]) == self.cfg["hash"]:
            self.cfg["frozen"] = False
            save(self.cfg)
            shutil.rmtree(SNAP, True)
            self.refresh()
        elif pw is not None:
            messagebox.showerror(APP, "Wrong password.")

    def restore_now(self):
        r = restore(self.cfg)
        messagebox.showinfo(APP, f"Removed {r[0]}, restored {r[1]}, reverted {r[2]}." if r else "Not frozen.")

    def startup(self):
        ok = set_startup(True)
        messagebox.showinfo(APP, "Enabled." if ok else "Failed (Windows only).")


if __name__ == "__main__":
    if "--autorestore" in sys.argv:
        restore(load())
        watch_forever()
    else:
        UI().mainloop()
