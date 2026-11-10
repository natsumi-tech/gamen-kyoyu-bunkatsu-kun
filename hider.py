"""Discord を「見えないけど描画は続く」状態に隠す。

最小化すると Discord(Chromium) は描画を止めるので配信が映らなくなる。
画面外に完全に出しても止まるが、一番右のモニターの右端に 2px だけ残して最背面に置くと描画が続く（実験で確認）。
"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

SLIVER = 2  # 画面に残す幅(px)
HWND_BOTTOM = wintypes.HWND(1)
SWP_NOACTIVATE = 0x10
SW_SHOWNORMAL, SW_SHOWMINIMIZED, SW_SHOWMAXIMIZED, SW_SHOWNOACTIVATE = 1, 2, 3, 4
SW_SHOWMINNOACTIVE = 7
WPF_RESTORETOMAXIMIZED = 0x2


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM]
MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)


def rightmost_monitor():
    return max(monitors(), key=lambda r: r[2])


def _placement(hwnd):
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    return wp


def _to_dict(wp):
    r = wp.rcNormalPosition
    return {"flags": wp.flags, "showCmd": wp.showCmd, "normal": [r.left, r.top, r.right, r.bottom]}


def _from_dict(d):
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    wp.flags = d["flags"]
    wp.showCmd = d["showCmd"]
    wp.ptMinPosition = wintypes.POINT(-1, -1)
    wp.ptMaxPosition = wintypes.POINT(-1, -1)
    wp.rcNormalPosition = wintypes.RECT(*d["normal"])
    return wp


def monitors():
    rects = []

    def cb(hmon, hdc, prc, lp):
        r = prc.contents
        rects.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
    return rects


def is_mostly_offscreen(hwnd):
    """ウィンドウがほぼ画面外（隠し位置や、外したモニターの上など）にあるか"""
    if user32.IsIconic(hwnd):  # 最小化中の座標は -32000 なので、戻したときの位置で判定
        r = _placement(hwnd).rcNormalPosition
    else:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    area = max(1, (r.right - r.left) * (r.bottom - r.top))
    visible = 0
    for ml, mt, mr, mb in monitors():
        w = min(r.right, mr) - max(r.left, ml)
        h = min(r.bottom, mb) - max(r.top, mt)
        if w > 0 and h > 0:
            visible += w * h
    return visible < area * 0.1 and visible < 300 * 300


def rescue(hwnd):
    """元の位置が分からないときの救出: メインモニターの左上寄りに普通の大きさで出す（最小化中ならそのまま）"""
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    w, h = min(1280, sw - 100), min(800, sh - 100)
    keep_min = bool(user32.IsIconic(hwnd))
    wp = _from_dict({"flags": 0, "showCmd": SW_SHOWMINNOACTIVE if keep_min else SW_SHOWNOACTIVATE,
                     "normal": [50, 50, 50 + w, 50 + h]})
    user32.SetWindowPlacement(hwnd, ctypes.byref(wp))


def hidden_spot():
    m = rightmost_monitor()
    return m[2] - SLIVER, m[1]


def is_at_hidden_spot(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top) == hidden_spot()


def hide(hwnd):
    """隠して、元に戻すための情報(dict)を返す。"""
    wp = _placement(hwnd)
    saved = _to_dict(wp)
    if user32.IsIconic(hwnd):
        # 最小化から戻すときに最大化していたか
        maximized = bool(wp.flags & WPF_RESTORETOMAXIMIZED)
        saved["showCmd"] = SW_SHOWMAXIMIZED if maximized else SW_SHOWNORMAL
    else:
        maximized = bool(user32.IsZoomed(hwnd))

    if maximized or user32.IsIconic(hwnd):
        # 最大化中/最小化中は、そのときの大きさのまま通常表示に切り替えてから動かす
        if maximized:
            r = wintypes.RECT()
            if user32.IsIconic(hwnd):
                m = rightmost_monitor()
                w, h = m[2] - m[0], m[3] - m[1]
            else:
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                w, h = r.right - r.left, r.bottom - r.top
        else:
            n = wp.rcNormalPosition
            w, h = n.right - n.left, n.bottom - n.top
        x, y = hidden_spot()
        new = _from_dict({"flags": 0, "showCmd": SW_SHOWNOACTIVATE, "normal": [x, y, x + w, y + h]})
        user32.SetWindowPlacement(hwnd, ctypes.byref(new))
    else:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top

    x, y = hidden_spot()
    user32.SetWindowPos(hwnd, HWND_BOTTOM, x, y, w, h, SWP_NOACTIVATE)
    saved["hidden_size"] = [w, h]  # 隠す直前の見た目の大きさ（画質「そのまま」で使う）
    return saved


def window_size(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def resize_hidden(hwnd, w, h):
    """隠し位置のまま大きさだけ変える（画面外なのでモニターより大きくてもよい）"""
    x, y = hidden_spot()
    user32.SetWindowPos(hwnd, HWND_BOTTOM, x, y, w, h, SWP_NOACTIVATE)


def show(hwnd, saved, activate=True):
    """hide() 前の位置・状態に戻す。最小化中に呼ばれたら、位置だけ直して最小化のままにする。"""
    d = dict(saved)
    if user32.IsIconic(hwnd):
        if d["showCmd"] == SW_SHOWMAXIMIZED:
            d["flags"] |= WPF_RESTORETOMAXIMIZED
        d["showCmd"] = SW_SHOWMINNOACTIVE
        activate = False
    elif d["showCmd"] == SW_SHOWMINIMIZED:
        d["showCmd"] = SW_SHOWNORMAL
    wp = _from_dict(d)
    # 最大化で戻す場合、通常位置へ一度置いてから最大化（隠し位置のモニターで最大化されないように）
    user32.SetWindowPlacement(hwnd, ctypes.byref(wp))
    if activate:
        user32.SetForegroundWindow(hwnd)
