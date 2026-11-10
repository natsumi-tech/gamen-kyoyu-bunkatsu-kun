"""画面共有分割くん - 他アプリ(Discord等)のウィンドウの一部を、独立したウィンドウとして表示するツール。

Windows の DWM サムネイル API を使うので、録画やキャプチャはせず GPU が直接描画する（軽量・低遅延）。
Discord 本体には一切手を加えない。
"""
import ctypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import traceback
from ctypes import wintypes
from tkinter import messagebox, simpledialog, ttk

try:
    from discord_detect import DiscordScanner  # 配信タイルの自動検出（comtypes が必要）
except Exception:
    DiscordScanner = None

import hider
from ui_widgets import (C, FONT, FONT_BOLD, ICON_FONT, ICONS, Card, IconButton, Segmented, Toggle,
                        dark_titlebar, label, rounded_rect, setup_ttk)

# 隠している間の Discord の大きさ。大きいほど配信タイルが高解像度で描かれ、拡大してもぼやけない
HQ_OPTIONS = [("off", "そのまま"), ("auto", "自動"), ("1440", "1440p"), ("2160", "4K")]
HQ_SIZES = {"1440": (2560, 1440), "2160": (3840, 2160)}


def _valid_placement(d):
    """設定ファイルに保存した Discord の元の位置が正しい形か"""
    n = d.get("normal")
    return (isinstance(n, list) and len(n) == 4 and all(isinstance(v, int) for v in n)
            and isinstance(d.get("flags"), int) and isinstance(d.get("showCmd"), int))


def hq_target(mode, stream_count):
    if mode in HQ_SIZES:
        return HQ_SIZES[mode]
    if mode == "auto":
        # 配信 1 つ → 1440p（タイルが 1080p 以上）、4 つまで → 4K、それ以上 → 5K
        if stream_count <= 1:
            return 2560, 1440
        if stream_count <= 4:
            return 3840, 2160
        return 5120, 2880
    return None

APP_NAME = "画面共有分割くん"
OLD_APP_NAME = "StreamSplitter"  # 旧名。設定フォルダの引き継ぎに使う
DEFAULT_TARGET_EXE = "discord.exe"
DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), APP_NAME)
LOG_PATH = os.path.join(DATA_DIR, "error.log")


def _migrate_old_settings():
    """旧名 (StreamSplitter) の設定を新しいフォルダへ引き継ぐ（隠したまま落ちた時の復元情報も含む）"""
    old = os.path.join(os.environ.get("APPDATA", "."), OLD_APP_NAME, "layout.json")
    new = os.path.join(DATA_DIR, "layout.json")
    try:
        if os.path.exists(old) and not os.path.exists(new):
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(old, "rb") as src, open(new, "wb") as dst:
                dst.write(src.read())
    except OSError:
        pass


_migrate_old_settings()


def log_error(where, exc=None):
    """.exe 版はエラーが画面に出ないので、ファイルに残す（1MB を超えたら .old に回す）"""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        detail = "".join(traceback.format_exception(exc)) if exc else traceback.format_exc()
        with open(LOG_PATH, "a", encoding="utf-8") as fp:
            fp.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {where}\n{detail}\n")
    except Exception:
        pass


sys.excepthook = lambda t, e, tb: log_error("unhandled", e)
threading.excepthook = lambda a: log_error(f"thread {a.thread.name if a.thread else ''}", a.exc_value)

# ---------------------------------------------------------------- Win32 / DWM
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware: 座標を物理ピクセルで扱う
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

HTHUMBNAIL = ctypes.c_void_p

DWM_TNP_RECTDESTINATION = 0x01
DWM_TNP_RECTSOURCE = 0x02
DWM_TNP_OPACITY = 0x04
DWM_TNP_VISIBLE = 0x08
DWM_TNP_SOURCECLIENTAREAONLY = 0x10


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("rcDestination", wintypes.RECT),
        ("rcSource", wintypes.RECT),
        ("opacity", ctypes.c_ubyte),
        ("fVisible", wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(HTHUMBNAIL)]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long
dwmapi.DwmUnregisterThumbnail.argtypes = [HTHUMBNAIL]
dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long
dwmapi.DwmUpdateThumbnailProperties.argtypes = [HTHUMBNAIL, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long

user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_exe(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)


def client_size(hwnd):
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def list_windows():
    """表示中で、タイトルのあるトップレベルウィンドウ一覧 [(hwnd, exe, title, area)]"""
    own_pid = os.getpid()
    result = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindow(hwnd, 4):  # GW_OWNER: 所有ウィンドウ(ダイアログ等)は除外
            return True
        title = window_title(hwnd)
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        exe = window_exe(hwnd)
        if exe.lower() == "explorer.exe" and title == "Program Manager":  # デスクトップ自体は除外
            return True
        w, h = client_size(hwnd)
        result.append((hwnd, exe, title, w * h))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return result


def find_window_by_exe(exe_name):
    """指定 exe の一番大きいウィンドウ（Discord ならメイン画面）を返す"""
    cands = [w for w in list_windows() if w[1].lower() == exe_name.lower()]
    if not cands:
        return None
    return max(cands, key=lambda w: w[3])[0]


def toplevel_hwnd(widget):
    return user32.GetParent(widget.winfo_id()) or widget.winfo_id()


# ---------------------------------------------------------------- 切り出しウィンドウ
class Viewer:
    def __init__(self, app, name, frac, geometry=None, topmost=False, keep_aspect=True, opacity=255,
                 auto_name=None):
        self.app = app
        self.name = name
        self.frac = frac  # [left, top, right, bottom] 対象ウィンドウに対する割合
        self.auto_name = auto_name  # 自動検出した配信のユーザー名
        self.topmost = tk.BooleanVar(value=bool(topmost))
        self.keep_aspect = tk.BooleanVar(value=bool(keep_aspect))
        try:
            self.opacity = max(20, min(255, int(opacity)))
        except (TypeError, ValueError):
            self.opacity = 255
        self.thumb = None
        self.thumb_src = None
        self.thumb_dest = None
        self.last_state = None
        self.fullscreen = False

        self.win = tk.Toplevel(app.root)
        self.win.title(name)
        self.win.configure(bg=C["viewer_bg"])
        self.win.minsize(80, 60)
        self.holder = tk.Frame(self.win, bg=C["viewer_bg"])
        self.holder.place(relx=0.5, rely=0.5, anchor="center")
        self.icon = tk.Label(self.holder, text=ICONS["video"], bg=C["viewer_bg"], fg=C["border"],
                             font=(ICON_FONT, 26))
        self.icon.pack()
        self.label = tk.Label(self.holder, bg=C["viewer_bg"], fg=C["muted"], justify="center",
                              text="対象ウィンドウが見つからないか、最小化されています", font=(FONT, 10))
        self.label.pack(pady=(8, 0))
        self.win.after(10, lambda: dark_titlebar(self.win, C["viewer_bg"]))
        try:
            self.win.geometry(geometry if isinstance(geometry, str) and geometry else self.default_geometry())
        except tk.TclError:  # 設定ファイルの値が壊れていた
            self.win.geometry(self.default_geometry())
        self.win.attributes("-topmost", self.topmost.get())

        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_checkbutton(label="常に最前面に表示", variable=self.topmost, command=self.apply_topmost)
        self.menu.add_checkbutton(label="縦横比を保つ", variable=self.keep_aspect, command=self.update)
        op = tk.Menu(self.menu, tearoff=0)
        for pct in (100, 85, 70, 50, 30):
            op.add_command(label=f"{pct}%", command=lambda p=pct: self.set_opacity(p))
        self.menu.add_cascade(label="不透明度", menu=op)
        self.menu.add_command(label="全画面 切替（ダブルクリック）", command=self.toggle_fullscreen)
        self.menu.add_separator()
        self.menu.add_command(label="名前を変更", command=self.rename)
        self.menu.add_separator()
        self.menu.add_command(label="このウィンドウを閉じる", command=self.close)

        for w in (self.win, self.holder, self.icon, self.label):
            w.bind("<Button-3>", self.popup)
            w.bind("<Double-Button-1>", lambda e: self.toggle_fullscreen())
        self.win.bind("<Escape>", lambda e: self.fullscreen and self.toggle_fullscreen())
        self.win.bind("<Configure>", lambda e: self.update())
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.after(100, self.update)

    def default_geometry(self):
        cw, ch = (640, 360)
        if self.app.src_hwnd and self.frac:
            w, h = client_size(self.app.src_hwnd)
            if w > 0 and h > 0:
                cw = max(160, int((self.frac[2] - self.frac[0]) * w))
                ch = max(90, int((self.frac[3] - self.frac[1]) * h))
        return f"{cw}x{ch}"

    # --- DWM サムネイル
    def register(self, src):
        self.unregister()
        if not src:
            return
        th = HTHUMBNAIL()
        dest = toplevel_hwnd(self.win)
        hr = dwmapi.DwmRegisterThumbnail(dest, src, ctypes.byref(th))
        if hr == 0:
            self.thumb = th
            self.thumb_src = src
            self.thumb_dest = dest
            self.last_state = None

    def unregister(self):
        if self.thumb:
            dwmapi.DwmUnregisterThumbnail(self.thumb)
        self.thumb = None
        self.thumb_src = None

    def update(self):
        """対象ウィンドウの大きさ・自ウィンドウの大きさに合わせて表示範囲を更新"""
        src = self.app.src_hwnd
        if not src or not user32.IsWindow(src):
            self.unregister()
            return
        if not self.win.winfo_ismapped():
            return  # 画面に出る前に登録すると、表示先ウィンドウを取り違える
        if self.thumb_src != src or self.thumb_dest != toplevel_hwnd(self.win):
            self.register(src)
        if not self.thumb:
            return
        sw, sh = client_size(src)
        dw, dh = self.win.winfo_width(), self.win.winfo_height()
        if self.auto_name:
            self.frac = self.app.auto_fracs.get(self.auto_name)
        if user32.IsIconic(src):
            self.label.config(text="Discord が最小化されているため映せません\n"
                                   "最小化の代わりに「Discord を隠す」を使ってください")
        elif self.frac is None:
            self.label.config(text=f"{self.auto_name} の配信を待っています\n"
                                   "Discord で通話画面を開き、配信を視聴すると表示されます")
        else:
            self.label.config(text="対象ウィンドウが見つからないか、最小化されています")
        if user32.IsIconic(src) or sw <= 0 or sh <= 0 or dw <= 1 or self.frac is None:
            visible = False
            sw = sh = 0
        else:
            visible = True
        state = (sw, sh, dw, dh, tuple(self.frac or ()), self.keep_aspect.get(), self.opacity, visible)
        if state == self.last_state:
            return
        self.last_state = state

        props = DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = (DWM_TNP_RECTDESTINATION | DWM_TNP_RECTSOURCE | DWM_TNP_OPACITY |
                         DWM_TNP_VISIBLE | DWM_TNP_SOURCECLIENTAREAONLY)
        props.fSourceClientAreaOnly = True
        props.fVisible = visible
        props.opacity = self.opacity
        l, t, r, b = self.frac or (0, 0, 1, 1)
        props.rcSource = wintypes.RECT(int(l * sw), int(t * sh), int(r * sw), int(b * sh))
        rw, rh = max(1, (r - l) * sw), max(1, (b - t) * sh)
        if self.keep_aspect.get() and visible:
            aspect = rw / rh
            if dw / dh > aspect:
                w = int(dh * aspect)
                x = (dw - w) // 2
                props.rcDestination = wintypes.RECT(x, 0, x + w, dh)
            else:
                h = int(dw / aspect)
                y = (dh - h) // 2
                props.rcDestination = wintypes.RECT(0, y, dw, y + h)
        else:
            props.rcDestination = wintypes.RECT(0, 0, dw, dh)
        dwmapi.DwmUpdateThumbnailProperties(self.thumb, ctypes.byref(props))

    # --- メニュー操作
    def popup(self, e):
        self.menu.tk_popup(e.x_root, e.y_root)

    def apply_topmost(self):
        self.win.attributes("-topmost", self.topmost.get())

    def set_opacity(self, pct):
        self.opacity = int(255 * pct / 100)
        self.update()

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.win.attributes("-fullscreen", self.fullscreen)
        self.win.after(100, self.update)

    def rename(self):
        n = simpledialog.askstring(APP_NAME, "新しい名前:", initialvalue=self.name, parent=self.win)
        if n:
            self.name = n
            self.win.title(n)
            self.app.refresh_list()

    def close(self, by_user=True):
        if self.auto_name:
            self.app.auto_geometries[self.auto_name] = self.to_dict()
            if by_user:  # 配信が出ている間は勝手に再表示しない
                self.app.dismissed.add(self.auto_name)
        self.unregister()
        self.win.destroy()
        self.app.remove_viewer(self)

    def to_dict(self):
        if self.fullscreen:
            self.toggle_fullscreen()
        return {"name": self.name, "frac": self.frac, "geometry": self.win.geometry(),
                "topmost": self.topmost.get(), "keep_aspect": self.keep_aspect.get(),
                "opacity": self.opacity}


# ---------------------------------------------------------------- 配信タイルの自動検出（別スレッド）
class AutoDetector(threading.Thread):
    """Discord の画面を定期的に読み、配信タイルの位置を app.scan_result に入れる。"""

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.stop = threading.Event()

    def run(self):
        if DiscordScanner is None:
            return
        try:
            scanner = DiscordScanner()
        except Exception as e:
            log_error("DiscordScanner init", e)
            self.app.scan_error = str(e)
            return
        last_err = None
        while not self.stop.wait(0.7):
            hwnd = self.app.src_hwnd
            if not self.app.auto_mode_on or not hwnd or not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
                continue
            try:
                self.app.scan_result = (hwnd, scanner.scan(hwnd))
                last_err = None
            except Exception as e:
                self.app.scan_result = None
                if repr(e) != last_err:  # 同じエラーを 0.7 秒ごとに書き続けない
                    log_error("scan", e)
                    last_err = repr(e)


# ---------------------------------------------------------------- メイン画面
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda t, e, tb: log_error("tk callback", e)
        self.root.title(APP_NAME)
        self.root.configure(bg=C["bg"])
        self.root.resizable(False, False)
        setup_ttk(self.root)
        self.viewers = []
        self.src_hwnd = None
        self.target_exe = DEFAULT_TARGET_EXE
        self.windows = []
        self.cfg_path = os.path.join(DATA_DIR, "layout.json")
        cfg = self.read_cfg()
        self.auto_mode = tk.BooleanVar(value=cfg.get("auto_mode", True) and DiscordScanner is not None)
        self.auto_mode_on = self.auto_mode.get()
        self.auto_geometries = cfg.get("auto_geometries", {})  # ユーザー名 → 前回のウィンドウ位置など
        self.auto_fracs = {}  # ユーザー名 → 現在の配信タイルの範囲（割合）
        self.dismissed = set()  # ユーザーが閉じたウィンドウ（その配信が消えるまで再表示しない）
        self.scan_result = None
        self.scan_error = None
        self.rows = {}  # Viewer → (dot ラベル, ピンボタン)
        self.settings_open = False
        self.hide_var = tk.BooleanVar(value=False)
        self.auto_hide_min = tk.BooleanVar(value=cfg.get("auto_hide_min", True))
        self.hq_size = tk.StringVar(value=cfg.get("hq_size", "auto"))
        self.stream_count = 0
        self.hq_requested = None  # 最後に要求した隠し中の大きさ
        self.hidden_saved = None  # Discord を隠す前の位置（戻すときに使う）
        self.prev_fg = False
        self.pending_restore = cfg.get("discord_hidden")  # 前回、隠したまま終了していた場合
        if not (isinstance(self.pending_restore, dict) and _valid_placement(self.pending_restore)):
            self.pending_restore = None
        self.startup_checked = False
        self.quitting = False
        self.tick_job = None

        outer = tk.Frame(self.root, bg=C["bg"], padx=18, pady=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1, minsize=420)

        # --- ヘッダー
        head = tk.Frame(outer, bg=C["bg"])
        head.grid(row=0, column=0, sticky="we")
        logo = tk.Canvas(head, width=36, height=36, bg=C["bg"], highlightthickness=0)
        rounded_rect(logo, 0, 0, 35, 35, 10, fill=C["accent"], outline="")
        logo.create_text(18, 18, text=ICONS["video"], fill="white", font=(ICON_FONT, 14))
        logo.pack(side="left")
        tf = tk.Frame(head, bg=C["bg"])
        tf.pack(side="left", padx=10)
        label(tf, APP_NAME, 14, bold=True).pack(anchor="w")
        label(tf, "Discord の配信をウィンドウに分割", 9, "muted").pack(anchor="w")
        self.pill = tk.Frame(head, bg=C["card"], padx=10, pady=4)
        self.pill.pack(side="right")
        self.pill_dot = tk.Label(self.pill, text="●", bg=C["card"], fg=C["muted"], font=(FONT, 8))
        self.pill_dot.pack(side="left")
        self.pill_text = tk.Label(self.pill, text="", bg=C["card"], fg=C["sub"], font=(FONT, 9))
        self.pill_text.pack(side="left", padx=(4, 0))

        # --- 自動モード
        auto = Card(outer)
        auto.grid(row=1, column=0, sticky="we", pady=(16, 0))
        b = auto.body
        b.columnconfigure(0, weight=1)
        label(b, "自動モード", 11, bold=True).grid(row=0, column=0, sticky="w")
        label(b, "通話中の配信を見つけて、自動でウィンドウにします", 9, "muted").grid(row=1, column=0, sticky="w")
        Toggle(b, self.auto_mode, self.on_auto_mode).grid(row=0, column=1, rowspan=2, sticky="e")
        tk.Frame(b, bg=C["border"], height=1).grid(row=2, column=0, columnspan=2, sticky="we", pady=10)
        self.detect_icon = tk.Label(b, text=ICONS["live"], bg=C["card"], fg=C["muted"], font=(ICON_FONT, 11))
        self.detect_icon.grid(row=3, column=0, sticky="nw")
        self.detect_label = tk.Label(b, text="", bg=C["card"], fg=C["sub"], font=(FONT, 9),
                                     justify="left", anchor="w", wraplength=360)
        self.detect_label.grid(row=3, column=0, columnspan=2, sticky="w", padx=(24, 0))

        # --- Discord を隠す
        hc = Card(outer)
        hc.grid(row=2, column=0, sticky="we", pady=(8, 0))
        hb = hc.body
        hb.columnconfigure(1, weight=1)
        self.hide_icon = tk.Label(hb, text=ICONS["hide"], bg=C["card"], fg=C["sub"], font=(ICON_FONT, 14))
        self.hide_icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))
        label(hb, "Discord を隠す", 11, bold=True).grid(row=0, column=1, sticky="w")
        label(hb, "配信を映したまま、Discord を画面から見えなくします", 9, "muted").grid(row=1, column=1, sticky="w")
        Toggle(hb, self.hide_var, self.on_hide_toggle).grid(row=0, column=2, rowspan=2, sticky="e")
        tk.Frame(hb, bg=C["border"], height=1).grid(row=2, column=0, columnspan=3, sticky="we", pady=10)
        qf = tk.Frame(hb, bg=C["card"])
        qf.grid(row=3, column=0, columnspan=3, sticky="we")
        label(qf, "隠している間の画質", 10).pack(side="left")
        Segmented(qf, HQ_OPTIONS, self.hq_size, self.apply_hq_size).pack(side="right")
        self.hq_label = label(hb, "", 8, "muted", anchor="w", justify="left")
        self.hq_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # --- ウィンドウ一覧
        sh = tk.Frame(outer, bg=C["bg"])
        sh.grid(row=3, column=0, sticky="we", pady=(18, 6))
        label(sh, "切り出し中のウィンドウ", 9, "muted", bold=True).pack(side="left")
        self.count_badge = tk.Label(sh, text="0", bg=C["card"], fg=C["sub"], font=(FONT_BOLD, 8), padx=6)
        self.count_badge.pack(side="left", padx=6)
        self.list_frame = tk.Frame(outer, bg=C["bg"])
        self.list_frame.grid(row=4, column=0, sticky="we")
        self.list_frame.columnconfigure(0, weight=1)

        # --- 詳細設定（折りたたみ）
        self.settings_btn = tk.Frame(outer, bg=C["bg"], cursor="hand2")
        self.settings_btn.grid(row=5, column=0, sticky="w", pady=(16, 0))
        si = tk.Label(self.settings_btn, text=ICONS["settings"], bg=C["bg"], fg=C["muted"], font=(ICON_FONT, 9))
        si.pack(side="left")
        st = label(self.settings_btn, "詳細設定", 9, "muted")
        st.pack(side="left", padx=4)
        self.settings_chev = tk.Label(self.settings_btn, text=ICONS["chevron_down"], bg=C["bg"], fg=C["muted"],
                                      font=(ICON_FONT, 7))
        self.settings_chev.pack(side="left")
        for w in (self.settings_btn, si, st, self.settings_chev):
            w.bind("<ButtonRelease-1>", lambda e: self.toggle_settings())

        self.settings = Card(outer)
        s = self.settings.body
        s.columnconfigure(0, weight=1)
        label(s, "切り出し元のウィンドウ", 9, "muted").grid(row=0, column=0, sticky="w")
        self.combo = ttk.Combobox(s, state="readonly", style="Dark.TCombobox", font=(FONT, 9))
        self.combo.grid(row=1, column=0, sticky="we", pady=(4, 0))
        self.combo.bind("<<ComboboxSelected>>", self.on_combo)
        IconButton(s, "refresh", self.refresh_windows, "ウィンドウ一覧を更新").grid(row=1, column=1, padx=(6, 0))
        rh = tk.Frame(s, bg=C["card"])
        rh.grid(row=2, column=0, columnspan=2, sticky="we", pady=(12, 0))
        label(rh, "Discord を最小化したら、代わりに隠す", 10).pack(side="left")
        Toggle(rh, self.auto_hide_min).pack(side="right")

        self.status = label(outer, "", 8, "muted", anchor="w", justify="left", wraplength=420)
        self.status.grid(row=7, column=0, sticky="we", pady=(12, 0))
        label(outer, "右クリックで設定 ・ ダブルクリックで全画面", 8, "muted").grid(row=8, column=0, sticky="w")

        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        dark_titlebar(self.root)
        self.refresh_windows()
        self.refresh_list()
        if not self.auto_mode_on:
            self.detect_label.config(text="オフ")
        self.detector = AutoDetector(self)
        self.detector.start()
        self.tick_job = self.root.after(500, self.tick)

    # --- Discord を隠す
    def on_hide_toggle(self):
        if self.hide_var.get():
            if not self.hide_discord():
                self.hide_var.set(False)
        else:
            self.show_discord()

    def hide_discord(self):
        h = self.src_hwnd
        if not h or not user32.IsWindow(h):
            messagebox.showwarning(APP_NAME, "Discord のウィンドウが見つかりません。")
            return False
        saved = hider.hide(h)
        self.hq_requested = None  # 隠し直したら大きさも設定し直す
        self.prev_fg = user32.GetForegroundWindow() == h  # 隠した直後に「開いた」と誤判定しない
        if self.hidden_saved is None:  # 既に隠している最中なら最初の位置を残す
            self.hidden_saved = saved
        self.hide_var.set(True)
        self.persist_hidden()
        self.apply_hq_size()
        self.status.config(text="Discord を隠しました。タスクバーの Discord をクリックするか、スイッチをオフにすると戻ります。")
        return True

    def show_discord(self, activate=True):
        h = self.src_hwnd
        if self.hidden_saved and h and user32.IsWindow(h):
            hider.show(h, self.hidden_saved, activate)
        self.hidden_saved = None
        self.hide_var.set(False)
        self.persist_hidden()
        if self.status.cget("text").startswith("Discord を隠しました"):
            self.status.config(text="")

    def apply_hq_size(self):
        """隠している間だけ、Discord を画質設定の大きさにする"""
        h = self.src_hwnd
        mode = self.hq_size.get()
        if not self.hidden_saved or not h or not user32.IsWindow(h) or user32.IsIconic(h):
            self.hq_label.config(text="Discord を隠すと、その間だけ大きく描画して画質を上げます")
            return
        target = hq_target(mode, self.stream_count)
        if target is None:  # そのまま → 隠す前の大きさ
            target = tuple(self.hidden_saved.get("hidden_size") or hider.window_size(h))
        # 同じ大きさを要求済みなら繰り返さない（Windows/Discord 側で大きさを制限された場合の空回り防止）
        if target != self.hq_requested and hider.window_size(h) != target:
            hider.resize_hidden(h, *target)
        self.hq_requested = target
        actual = hider.window_size(h)
        text = f"いまの Discord の大きさ: {actual[0]}×{actual[1]}"
        if actual != target:
            text += f"（{target[0]}×{target[1]} を指定しましたが、この PC では制限されました）"
        elif mode == "auto":
            text += f"（配信 {self.stream_count} 件に合わせて自動）"
        self.hq_label.config(text=text)

    def persist_hidden(self):
        """隠したままアプリが落ちても次回起動時に戻せるよう保存"""
        try:
            cfg = self.read_cfg()
            cfg["discord_hidden"] = self.hidden_saved
            self.write_cfg(cfg)
        except Exception:
            pass

    def watch_discord_window(self):
        h = self.src_hwnd
        if not h or not user32.IsWindow(h):
            return
        if not self.startup_checked:
            # 前回、隠したまま強制終了/シャットダウンした等で Discord が画面外に残っていたら戻す
            # （モニター構成が変わっていても戻せるよう「ほぼ画面外か」で判定）
            self.startup_checked = True
            if hider.is_mostly_offscreen(h):
                if self.pending_restore:
                    hider.show(h, self.pending_restore, activate=False)
                if hider.is_mostly_offscreen(h):  # 元の位置も画面外（モニターを外した等）
                    hider.rescue(h)
                self.status.config(text="画面外に残っていた Discord を元に戻しました。")
            if self.pending_restore:
                self.pending_restore = None
                self.persist_hidden()
        fg = user32.GetForegroundWindow() == h
        if user32.IsIconic(h) and (self.hidden_saved or self.auto_hide_min.get()):
            self.hide_discord()  # 最小化 → 代わりに隠す（最小化すると配信の描画が止まるため）
            fg = user32.GetForegroundWindow() == h
        elif self.hidden_saved and fg and not self.prev_fg:
            self.show_discord()  # タスクバー等から Discord を開いた瞬間 → 元の位置に戻す
        self.prev_fg = fg
        self.apply_hq_size()  # 配信数が変わったら自動の大きさを追従

    def toggle_settings(self):
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.settings.grid(row=6, column=0, sticky="we", pady=(8, 0))
            self.settings_chev.config(text=ICONS["chevron_up"])
        else:
            self.settings.grid_remove()
            self.settings_chev.config(text=ICONS["chevron_down"])

    # --- 対象ウィンドウ
    def refresh_windows(self):
        self.windows = sorted(list_windows(), key=lambda w: (w[1].lower() != self.target_exe.lower(), -w[3]))
        self.combo["values"] = [f"[{exe}] {title}" for _, exe, title, _ in self.windows]
        idx = next((i for i, w in enumerate(self.windows) if w[0] == self.src_hwnd), None)
        if idx is None and self.windows and self.windows[0][1].lower() == self.target_exe.lower():
            idx = 0
        if idx is not None:
            self.combo.current(idx)
            self.set_source(self.windows[idx][0], self.windows[idx][1])
        else:
            self.set_status()

    def on_combo(self, _):
        hwnd, exe, _, _ = self.windows[self.combo.current()]
        self.set_source(hwnd, exe)

    def set_source(self, hwnd, exe):
        if hwnd != self.src_hwnd:
            self.auto_fracs = {}
            self.scan_result = None
        self.src_hwnd = hwnd
        self.target_exe = exe
        for v in self.viewers:
            v.update()
        self.set_status()

    def set_status(self):
        name = os.path.splitext(self.target_exe)[0]
        if self.src_hwnd and user32.IsWindow(self.src_hwnd):
            self.hide_icon.config(fg=C["accent"] if self.hidden_saved else C["sub"])
            if self.hidden_saved:
                self.pill_dot.config(fg=C["accent"])
                self.pill_text.config(text=f"{name} 非表示中")
            elif user32.IsIconic(self.src_hwnd):
                self.pill_dot.config(fg=C["yellow"])
                self.pill_text.config(text=f"{name} 最小化中")
                self.status.config(text="Discord が最小化されていると映りません。最小化の代わりに「Discord を隠す」を使ってください。")
            else:
                self.pill_dot.config(fg=C["green"])
                self.pill_text.config(text=f"{name} に接続中")
                if self.status.cget("text").startswith(("Discord が最小化", f"{self.target_exe} の")):
                    self.status.config(text="")
        else:
            self.pill_dot.config(fg=C["red"])
            self.pill_text.config(text=f"{name} 未検出")
            self.status.config(text=f"{self.target_exe} のウィンドウが見つかりません。起動すると自動で接続します。")

    def tick(self):
        # どこかで例外が出ても、次回の予約は必ず行う（止まると隠す/戻す/検出が全部止まるため）
        try:
            self._tick()
        except Exception as e:
            log_error("tick", e)
        finally:
            if not self.quitting:
                self.tick_job = self.root.after(500, self.tick)

    def _tick(self):
        # 対象が閉じられた/再起動された場合は exe 名で探し直す
        if not self.src_hwnd or not user32.IsWindow(self.src_hwnd):
            hwnd = find_window_by_exe(self.target_exe)
            if hwnd:
                self.src_hwnd = None
                self.refresh_windows()
                self.set_source(hwnd, self.target_exe)
        self.watch_discord_window()
        if self.auto_mode_on:
            self.apply_scan()
        for v in list(self.viewers):
            try:
                v.update()
            except Exception as e:  # 1 つのウィンドウの不具合で他を止めない
                log_error("viewer update", e)  # ログに配信者名は残さない
        self.update_rows()
        self.set_status()

    # --- 自動モード
    def on_auto_mode(self):
        self.auto_mode_on = self.auto_mode.get()
        if DiscordScanner is None and self.auto_mode_on:
            self.auto_mode.set(False)
            self.auto_mode_on = False
            messagebox.showwarning(APP_NAME, "自動検出の部品が読み込めませんでした。")
        if not self.auto_mode_on:
            self.auto_fracs = {}
            self.detect_icon.config(fg=C["muted"])
            self.detect_label.config(text="オフ")
        else:
            self.detect_label.config(text="Discord の画面を読み取り中…")

    def apply_scan(self):
        if self.scan_error:
            self.detect_icon.config(fg=C["red"])
            self.detect_label.config(text=f"自動検出を開始できませんでした: {self.scan_error}")
            return
        res = self.scan_result
        if not res or res[0] != self.src_hwnd:
            self.detect_label.config(text="Discord の画面を読み取り中…")
            return
        hwnd, data = res
        cw, ch = client_size(hwnd)
        if cw <= 0 or ch <= 0:
            return
        fracs = {}
        for name, (l, t, r, b) in data["tiles"]:
            p0, p1 = wintypes.POINT(l, t), wintypes.POINT(r, b)
            user32.ScreenToClient(hwnd, ctypes.byref(p0))
            user32.ScreenToClient(hwnd, ctypes.byref(p1))
            frac = [max(0.0, min(1.0, v)) for v in (p0.x / cw, p0.y / ch, p1.x / cw, p1.y / ch)]
            if frac[2] - frac[0] > 0.01 and frac[3] - frac[1] > 0.01:
                fracs[name] = frac
        self.auto_fracs = fracs

        # 消えた配信は「閉じた」扱いを解除（次に出てきたら再び自動表示）
        self.dismissed &= set(fracs)

        open_names = {v.auto_name for v in self.viewers if v.auto_name}
        for name in fracs:
            if name in open_names or name in self.dismissed:
                continue
            d = self.auto_geometries.get(name)
            if not isinstance(d, dict):
                d = {}
            self.viewers.append(Viewer(self, name, fracs[name], d.get("geometry"), d.get("topmost", False),
                                       d.get("keep_aspect", True), d.get("opacity", 255), auto_name=name))
            self.refresh_list()

        n_tiles, n_live = len(fracs), len(data["live_users"])
        self.stream_count = max(n_tiles, n_live)
        if n_tiles:
            self.detect_icon.config(fg=C["green"])
            msg = f"{n_tiles} 件の配信を検出中"
        else:
            self.detect_icon.config(fg=C["muted"])
            msg = "Discord の画面に配信が表示されていません"
        if n_live > n_tiles:
            self.detect_icon.config(fg=C["yellow"])
            msg += (f"\n通話内で配信中: {n_live} 人（" + "、".join(data["live_users"]) + "）"
                    "\nDiscord で各配信を「視聴」してグリッド表示にすると全部切り出せます")
        elif not n_tiles:
            msg += "\nDiscord で通話中のボイスチャンネルを開き、配信を視聴してください"
        self.detect_label.config(text=msg)

    # --- 切り出しウィンドウ一覧
    def remove_viewer(self, v):
        if v in self.viewers:
            self.viewers.remove(v)
        self.refresh_list()

    def refresh_list(self):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.rows = {}
        self.count_badge.config(text=str(len(self.viewers)))
        if not self.viewers:
            card = Card(self.list_frame, bg=C["bg"], pad=(14, 18))
            card.configure(highlightthickness=0)
            card.grid(row=0, column=0, sticky="we")
            b = card.body
            tk.Label(b, text=ICONS["video"], bg=C["bg"], fg=C["border"], font=(ICON_FONT, 22)).pack()
            label(b, "配信が見つかると、ここに表示されます", 9, "muted").pack(pady=(6, 0))
            return
        for i, v in enumerate(self.viewers):
            card = Card(self.list_frame, hover=C["card_hover"], pad=(12, 8), radius=8)
            card.grid(row=i, column=0, sticky="we", pady=(0, 6))
            b = card.body
            dot = tk.Label(b, text="●", bg=C["card"], fg=C["muted"], font=(FONT, 9))
            dot.pack(side="left")
            name = label(b, v.name, 10, bold=True)
            name.pack(side="left", padx=(6, 0))
            IconButton(b, "close", lambda v=v: v.close(), "閉じる").pack(side="right")
            IconButton(b, "front", lambda v=v: (v.win.deiconify(), v.win.lift()), "前面に出す").pack(side="right")
            pin = IconButton(b, "pinned" if v.topmost.get() else "pin", lambda v=v: self.toggle_pin(v),
                             "常に最前面", active=v.topmost.get())
            pin.pack(side="right")
            for w in (card.body, name, dot):
                w.bind("<Double-Button-1>", lambda e, v=v: (v.win.deiconify(), v.win.lift()))
            self.rows[v] = (dot, pin)
        self.update_rows()

    def toggle_pin(self, v):
        v.topmost.set(not v.topmost.get())
        v.apply_topmost()
        self.update_rows()

    def update_rows(self):
        for v, (dot, pin) in self.rows.items():
            showing = bool(v.last_state and v.last_state[-1])
            try:
                dot.config(fg=C["green"] if showing else C["muted"])
                pin.set_active(v.topmost.get(), "pinned" if v.topmost.get() else "pin")
            except tk.TclError:
                pass

    # --- 配置の保存/読み込み
    def read_cfg(self):
        try:
            with open(self.cfg_path, encoding="utf-8") as fp:
                cfg = json.load(fp)
        except FileNotFoundError:
            return {}
        except Exception as e:
            log_error("read_cfg", e)
            return {}
        if not isinstance(cfg, dict):
            return {}
        if not isinstance(cfg.get("auto_geometries"), dict):
            cfg["auto_geometries"] = {}
        cfg.pop("viewers", None)  # 旧バージョンの手動切り出しの配置（機能削除済み）
        cfg.pop("auto_restore", None)
        if cfg.get("hq_size") not in dict(HQ_OPTIONS):
            cfg.pop("hq_size", None)
        if not isinstance(cfg.get("target_exe", ""), str):
            cfg.pop("target_exe")
        return cfg

    def write_cfg(self, cfg):
        # 一時ファイルに書いてから置き換える（書き込み中に落ちても設定ファイルが壊れない）
        os.makedirs(os.path.dirname(self.cfg_path), exist_ok=True)
        tmp = self.cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, self.cfg_path)

    def save_settings(self):
        """設定と、配信者ごとのウィンドウ位置を保存（終了時に呼ばれる）"""
        for v in self.viewers:
            self.auto_geometries[v.auto_name] = v.to_dict()
        cfg = self.read_cfg()
        cfg.update({"target_exe": self.target_exe, "auto_mode": self.auto_mode.get(),
                    "auto_geometries": self.auto_geometries, "auto_hide_min": self.auto_hide_min.get(),
                    "discord_hidden": self.hidden_saved, "hq_size": self.hq_size.get()})
        self.write_cfg(cfg)

    def quit(self):
        self.quitting = True
        if self.tick_job:
            self.root.after_cancel(self.tick_job)
        self.detector.stop.set()
        if self.hidden_saved:  # 隠したまま終了しない
            self.show_discord(activate=False)
        try:
            self.save_settings()
        except Exception as e:
            log_error("save_settings", e)
        for v in list(self.viewers):
            v.unregister()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        sys.exit("Windows 専用です")
    App().run()
