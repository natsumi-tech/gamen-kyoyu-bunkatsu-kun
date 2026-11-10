"""画面共有分割くん 用のダークテーマと自作ウィジェット（tkinter だけで動く）。"""
import ctypes
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

# Discord 風のダークカラー
C = {
    "bg": "#1e1f22",
    "card": "#2b2d31",
    "card_hover": "#32343a",
    "border": "#3a3c42",
    "text": "#f2f3f5",
    "sub": "#b5bac1",
    "muted": "#80848e",
    "accent": "#5865f2",
    "accent_hover": "#4752c4",
    "green": "#23a55a",
    "red": "#f23f43",
    "yellow": "#f0b232",
    "viewer_bg": "#111214",
}
FONT = "Yu Gothic UI"
FONT_BOLD = "Yu Gothic UI Semibold"
ICON_FONT = "Segoe MDL2 Assets"  # Windows 10/11 標準のアイコンフォント

ICONS = {
    "pin": "", "pinned": "", "close": "", "front": "", "crop": "",
    "settings": "", "add": "", "refresh": "", "video": "", "live": "",
    "hide": "", "view": "", "save": "", "open": "", "chevron_down": "", "chevron_up": "",
}

_dwm = ctypes.windll.dwmapi
_dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_user32 = ctypes.windll.user32
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.GetParent.restype = wintypes.HWND
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_uint]


def dark_titlebar(win, color=C["bg"]):
    """タイトルバーをダークにする（Windows 10 20H1 以降。Windows 11 では色も合わせる）"""
    try:
        win.update_idletasks()
        hwnd = _user32.GetParent(win.winfo_id()) or win.winfo_id()
        on = ctypes.c_int(1)
        _dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)  # DWMWA_USE_IMMERSIVE_DARK_MODE
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        cref = ctypes.c_uint(r | (g << 8) | (b << 16))
        _dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cref), 4)  # DWMWA_CAPTION_COLOR (Win11)
        # SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED で枠を描き直す
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x1 | 0x2 | 0x4 | 0x10 | 0x20)
    except Exception:
        pass


def setup_ttk(root):
    """Combobox などの ttk 部品をダークに"""
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure("Dark.TCombobox", fieldbackground=C["bg"], background=C["card"], foreground=C["text"],
                 arrowcolor=C["sub"], bordercolor=C["border"], lightcolor=C["border"], darkcolor=C["border"],
                 selectbackground=C["bg"], selectforeground=C["text"], padding=6)
    st.map("Dark.TCombobox", fieldbackground=[("readonly", C["bg"])], foreground=[("readonly", C["text"])],
           selectbackground=[("readonly", C["bg"])], selectforeground=[("readonly", C["text"])])
    root.option_add("*TCombobox*Listbox.background", C["card"])
    root.option_add("*TCombobox*Listbox.foreground", C["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", "white")
    root.option_add("*TCombobox*Listbox.font", (FONT, 10))
    root.option_add("*TCombobox*Listbox.borderWidth", 0)


def rounded_rect(cv, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, **kw)


class Card(tk.Canvas):
    """角丸の背景を持つ入れ物。中身は .body (Frame) に置く。"""

    def __init__(self, parent, bg=C["card"], radius=10, pad=(14, 12), hover=None):
        # 初期の高さが中身の位置より小さいと中身が表示されず、サイズ調整も発火しないので余裕を持たせる
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, height=pad[1] * 2 + 20)
        self.fill, self.hover_fill, self.radius, self.pad = bg, hover, radius, pad
        self.body = tk.Frame(self, bg=bg)
        self.shape = None
        self.win_id = self.create_window(pad[0], pad[1], window=self.body, anchor="nw")
        self.bind("<Configure>", self._redraw)
        self.body.bind("<Configure>", self._fit)
        self.after_idle(self._fit)
        if hover:
            for w in (self, self.body):
                w.bind("<Enter>", lambda e: self.set_fill(self.hover_fill), add="+")
                w.bind("<Leave>", self._leave, add="+")

    def _leave(self, e):
        x, y = self.winfo_pointerxy()
        if self.winfo_containing(x, y) not in self._descendants():
            self.set_fill(self.fill)

    def _descendants(self):
        out, stack = {self}, [self]
        while stack:
            w = stack.pop()
            for c in w.winfo_children():
                out.add(c)
                stack.append(c)
        return out

    def set_fill(self, color):
        if self.shape:
            self.itemconfig(self.shape, fill=color)
        self._recolor(self.body, color)

    def _recolor(self, w, color):
        try:
            if w.cget("bg") in (self.fill, self.hover_fill) and not getattr(w, "keep_bg", False):
                w.configure(bg=color)
        except tk.TclError:
            pass
        for c in w.winfo_children():
            self._recolor(c, color)

    def _fit(self, e=None):
        self.configure(height=self.body.winfo_reqheight() + self.pad[1] * 2)

    def _redraw(self, e=None):
        w, h = self.winfo_width(), self.winfo_height()
        self.itemconfig(self.win_id, width=max(1, w - self.pad[0] * 2))
        if self.shape:
            self.delete(self.shape)
        self.shape = rounded_rect(self, 0, 0, w - 1, h - 1, self.radius, fill=self.fill, outline="")
        self.tag_lower(self.shape)


class RoundButton(tk.Canvas):
    """角丸ボタン（primary / secondary / ghost）"""

    STYLES = {
        "primary": (C["accent"], C["accent_hover"], "white"),
        "secondary": (C["border"], "#4a4d55", C["text"]),
        "ghost": (C["bg"], C["card"], C["sub"]),
    }

    def __init__(self, parent, text, command, icon=None, style="primary", height=38, font_size=10):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, height=height, cursor="hand2")
        self.base, self.hover, self.fg = self.STYLES[style]
        self.text, self.icon, self.command, self.font_size = text, icon, command, font_size
        self.cur = self.base
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self.draw(self.hover))
        self.bind("<Leave>", lambda e: self.draw(self.base))
        self.bind("<ButtonRelease-1>", lambda e: self.command())

    def draw(self, color=None):
        self.cur = color or self.cur
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        rounded_rect(self, 0, 0, w - 1, h - 1, 8, fill=self.cur, outline="")
        if self.icon:
            self.create_text(w / 2 - self._text_w() / 2 - 4, h / 2, text=self.icon, fill=self.fg,
                             font=(ICON_FONT, self.font_size))
            self.create_text(w / 2 + 10, h / 2, text=self.text, fill=self.fg, font=(FONT_BOLD, self.font_size))
        else:
            self.create_text(w / 2, h / 2, text=self.text, fill=self.fg, font=(FONT_BOLD, self.font_size))

    def _text_w(self):
        tmp = self.create_text(0, 0, text=self.text, font=(FONT_BOLD, self.font_size))
        b = self.bbox(tmp)
        self.delete(tmp)
        return b[2] - b[0]


class Toggle(tk.Canvas):
    """iOS / Discord 風のスイッチ"""

    def __init__(self, parent, variable, command=None):
        super().__init__(parent, width=44, height=24, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.keep_bg = False
        self.var, self.command = variable, command
        self.bind("<ButtonRelease-1>", self.flip)
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def flip(self, e=None):
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def draw(self):
        self.delete("all")
        on = self.var.get()
        rounded_rect(self, 1, 1, 43, 23, 11, fill=C["green"] if on else "#4e5058", outline="")
        x = 32 if on else 12
        self.create_oval(x - 9, 3, x + 9, 21, fill="white", outline="")


class Segmented(tk.Frame):
    """[そのまま | 自動 | 1440p | 4K] のような切り替えボタン"""

    def __init__(self, parent, options, variable, command=None):
        super().__init__(parent, bg=C["bg"], padx=2, pady=2)
        self.keep_bg = True
        self.var, self.command, self.items = variable, command, {}
        for value, text in options:
            lb = tk.Label(self, text=text, font=(FONT_BOLD, 9), padx=10, pady=3, cursor="hand2")
            lb.keep_bg = True
            lb.pack(side="left", padx=1)
            lb.bind("<ButtonRelease-1>", lambda e, v=value: self.select(v))
            lb.bind("<Enter>", lambda e, v=value: self._hover(v, True))
            lb.bind("<Leave>", lambda e, v=value: self._hover(v, False))
            self.items[value] = lb
        variable.trace_add("write", lambda *a: self.draw())
        self.draw()

    def select(self, value):
        self.var.set(value)
        if self.command:
            self.command()

    def _hover(self, value, on):
        if value != self.var.get():
            self.items[value].configure(fg=C["text"] if on else C["sub"])

    def draw(self):
        for value, lb in self.items.items():
            sel = value == self.var.get()
            lb.configure(bg=C["accent"] if sel else C["bg"], fg="white" if sel else C["sub"])


class IconButton(tk.Label):
    def __init__(self, parent, icon, command, tip=None, size=11, active=False):
        super().__init__(parent, text=ICONS[icon], font=(ICON_FONT, size), bg=parent["bg"],
                         fg=C["accent"] if active else C["muted"], cursor="hand2", padx=6, pady=4)
        self.command, self.active = command, active
        self.bind("<Enter>", lambda e: self.configure(fg=C["text"]))
        self.bind("<Leave>", lambda e: self.configure(fg=C["accent"] if self.active else C["muted"]))
        self.bind("<ButtonRelease-1>", lambda e: self.command())
        if tip:
            Tooltip(self, tip)

    def set_active(self, active, icon=None):
        self.active = active
        if icon:
            self.configure(text=ICONS[icon])
        self.configure(fg=C["accent"] if active else C["muted"])


class Tooltip:
    def __init__(self, widget, text):
        self.w, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self.schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def schedule(self, e=None):
        self.job = self.w.after(450, self.show)

    def show(self):
        x = self.w.winfo_rootx() + self.w.winfo_width() // 2
        y = self.w.winfo_rooty() - 30
        self.tip = tk.Toplevel(self.w)
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        tk.Label(self.tip, text=self.text, bg="#111214", fg=C["text"], font=(FONT, 9),
                 padx=8, pady=3).pack()
        self.tip.update_idletasks()
        self.tip.geometry(f"+{x - self.tip.winfo_width() // 2}+{y}")

    def hide(self, e=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def label(parent, text="", size=10, color="text", bold=False, **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=C[color],
                    font=(FONT_BOLD if bold else FONT, size), **kw)
