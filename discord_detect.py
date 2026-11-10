"""Discord の画面から「配信タイル」の位置を自動で見つける（Windows UI Automation で読むだけ。Discord は改造しない）。

Discord(Electron/Chromium) は画面読み上げソフト向けに各要素の名前と位置を公開している。
通話画面の配信タイルは「通話タイル、配信、<ユーザー名>」(英語版は "Call tile, stream, ...") という名前のボタンになる。
"""
import re

import comtypes
import comtypes.client

_uia_mod = None


def _uia():
    global _uia_mod
    if _uia_mod is None:
        _uia_mod = comtypes.client.GetModule("UIAutomationCore.dll")
    return _uia_mod


UIA_ButtonControlTypeId = 50000
UIA_TextControlTypeId = 50020
UIA_ControlTypePropertyId = 30003

# 「通話タイル、配信、name」/「Call tile, stream, name」
TILE_RE = re.compile(r"^(?:通話タイル|call tile)[、,]\s*(?:配信|stream)[、,]\s*(.+)$", re.IGNORECASE)
LIVE_WORDS = {"ライブ", "live"}


class DiscordScanner:
    """別スレッドから呼ぶ想定。スレッドごとに作成すること（COM 初期化のため）。"""

    def __init__(self):
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # このスレッドは既に COM 初期化済み
        uia = _uia()
        self.auto = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        self.cond_button = self.auto.CreatePropertyCondition(UIA_ControlTypePropertyId, UIA_ButtonControlTypeId)
        self.cond_text = self.auto.CreatePropertyCondition(UIA_ControlTypePropertyId, UIA_TextControlTypeId)
        self.cond_either = self.auto.CreateOrCondition(self.cond_button, self.cond_text)

    def scan(self, hwnd):
        """戻り値: {"tiles": [(name, (l,t,r,b) スクリーン座標)], "live_users": [name, ...]}"""
        root = self.auto.ElementFromHandle(hwnd)
        arr = root.FindAll(4, self.cond_either)  # TreeScope_Descendants
        buttons, live_texts = [], []
        for i in range(arr.Length):
            el = arr.GetElement(i)
            try:
                name = el.CurrentName or ""
                ctype = el.CurrentControlType
                r = el.CurrentBoundingRectangle
            except comtypes.COMError:
                continue
            rect = (r.left, r.top, r.right, r.bottom)
            if ctype == UIA_ButtonControlTypeId:
                buttons.append((name, rect))
            elif name.strip().lower() in LIVE_WORDS:
                live_texts.append(rect)

        tiles = []
        for name, rect in buttons:
            m = TILE_RE.match(name.strip())
            if m and rect[2] - rect[0] > 40 and rect[3] - rect[1] > 30:
                tiles.append((m.group(1).strip(), rect))

        # チャンネル一覧で「ライブ」バッジが付いている参加者（＝配信中だが画面に出ていない人も含む）
        live_users = []
        tile_rects = [t[1] for t in tiles]
        for lr in live_texts:
            if any(_inside(lr, tr) for tr in tile_rects):
                continue
            cy = (lr[1] + lr[3]) / 2
            for name, br in buttons:
                if br[1] <= cy <= br[3] and br[0] <= lr[0] <= br[2] and (br[3] - br[1]) < 60:
                    user = re.split(r"[、,]", name)[0].strip()
                    if user and user not in live_users:
                        live_users.append(user)
                    break
        return {"tiles": tiles, "live_users": live_users}


def _inside(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


if __name__ == "__main__":
    import time
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    from stream_splitter import find_window_by_exe
    hwnd = find_window_by_exe("discord.exe")
    s = DiscordScanner()
    for _ in range(3):
        t = time.perf_counter()
        res = s.scan(hwnd)
        print(f"{(time.perf_counter() - t) * 1000:.0f}ms", res)
        time.sleep(1)
