"""画面共有分割くん.exe をビルドする（build.bat から呼ばれる）。

exe 名に日本語を使うので、文字化けしやすいバッチファイルではなく Python 側で処理する。
"""
import hashlib
import os
import sys

APP_NAME = "画面共有分割くん"
HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    os.chdir(HERE)

    # UI Automation のラッパーを先に生成しておく（exe に同梱され、実行時のコード生成が不要になる）
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    import comtypes.gen.UIAutomationClient  # noqa: F401

    import tempfile
    import PyInstaller.__main__
    work = os.path.join(tempfile.gettempdir(), "gamen_kyoyu_build")  # 中間ファイルはプロジェクト外へ
    PyInstaller.__main__.run([
        "--onefile", "--noconsole", "--clean", "--noconfirm",
        "--workpath", work, "--specpath", work,
        "--name", APP_NAME,
        "--collect-submodules", "comtypes",
        "stream_splitter.py",
    ])

    exe = os.path.join(HERE, "dist", APP_NAME + ".exe")
    with open(exe, "rb") as fp:
        digest = hashlib.sha256(fp.read()).hexdigest().upper()
    print()
    print(f"完成: {exe}")
    print(f"SHA256（友人に伝えると、ファイルが本物か確認できます）: {digest}")


if __name__ == "__main__":
    sys.exit(main())
