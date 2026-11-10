"""Build, ad-hoc sign and package RallyLite for macOS (universal2 preview).

Run on macOS only:
    python3 rally-lite/tools/package_macos.py

Requires: Xcode Command Line Tools (xcode-select --install) + CMake (brew install cmake).
Output:   dist/RallyLite-macOS-universal-preview/RallyLite.app
          dist/RallyLite-macOS-universal-preview.zip
"""
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "rally-lite"
BUILD = ROOT / "build/rally-lite-macos"
DIST = ROOT / "dist"
NAME = "RallyLite-macOS-universal-preview"
APP_ID = "app.offwork.rallylite"
ARCHS = "arm64;x86_64"
DEPLOYMENT_TARGET = "12.0"

USAGE = """RallyLite 集会提醒 · macOS 通用版（arm64 + x86_64）预览

【首次打开】
本预览版未做 Apple 开发者签名与公证，系统会提示“无法验证开发者”。
任选一种方式放行：
  1. 在访达中右键 RallyLite.app → 打开 → 再点“打开”；
  2. 或终端执行：xattr -dr com.apple.quarantine /Applications/RallyLite.app

【安装】
把 RallyLite.app 拖进“应用程序”文件夹。启动后没有 Dock 图标，
请到屏幕右上角菜单栏找“RallyLite · 集会提醒”图标（菜单栏 App，LSUIElement）。

【使用】
1. 点击菜单栏图标 → 打开窗口，填昵称 + 下班搭子里的 8 位集会码 → 加入。
2. 默认快捷键：Control+Option+G 安全、Control+Option+Y 警戒、
   Control+Option+R 危险提醒，可在设置里改成别的组合。
3. 提醒只有屏幕边框和来源文字，没有声音和系统通知。
4. 关闭窗口只是隐藏，程序仍在菜单栏运行；彻底退出用菜单“退出 RallyLite”。

【说明】
- 需要联网（接入内置下班搭子服务，走现有中继协议），只加入已有集会，不创建集会。
- 不需要安装 Python 或下班搭子主程序。
- 集会码对应的身份令牌保存在本机“钥匙串”中，设置保存在
  ~/Library/Application Support/RallyLite/settings.json，不随安装包分发。
- 已在 macOS 12+ 上构建；Apple 芯片与 Intel 同一份即通用二进制。
- 未签名、未公证，仅内部预览使用。
"""


def run(*command, **kwargs):
    print("+", " ".join(str(item) for item in command), flush=True)
    return subprocess.run([str(item) for item in command], check=True, **kwargs)


def preflight():
    if sys.platform != "darwin":
        sys.exit(f"This packaging script must run on macOS, current platform: {sys.platform}")
    if subprocess.run(["xcode-select", "-p"], capture_output=True).returncode != 0:
        sys.exit("Missing Xcode Command Line Tools. Run: xcode-select --install")
    if shutil.which("cmake") is None:
        sys.exit("Missing CMake. Run: brew install cmake  (or install the official .dmg)")
    print(f"Host: macOS {platform.mac_ver()[0]} / CMake "
          f"{subprocess.run(['cmake', '--version'], capture_output=True, text=True).stdout.splitlines()[0]}")


def build():
    run("cmake", "-S", SOURCE, "-B", BUILD,
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_OSX_ARCHITECTURES={ARCHS}",
        f"-DCMAKE_OSX_DEPLOYMENT_TARGET={DEPLOYMENT_TARGET}")
    run("cmake", "--build", BUILD, "-j", str(os.cpu_count() or 4))
    run("ctest", "--test-dir", BUILD, "--output-on-failure")
    app = BUILD / "RallyLite.app"
    if not (app / "Contents/MacOS/RallyLite").is_file():
        sys.exit(f"Build finished but bundle is incomplete: {app}")
    return app


def find_json_license():
    for candidate in (BUILD / "_deps/nlohmann_json-src/LICENSE.MIT",
                      ROOT / "work/rally-deps/json/LICENSE.MIT"):
        if candidate.is_file():
            return candidate
    for candidate in BUILD.rglob("LICENSE.MIT"):
        return candidate
    raise FileNotFoundError("nlohmann/json LICENSE.MIT not found, cannot ship third-party notices")


def package(app, force):
    destination = DIST / NAME
    if destination.exists():
        if not force:
            sys.exit(f"{destination} already exists. Delete it, or rerun with --force")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    bundle = shutil.copytree(app, destination / "RallyLite.app", symlinks=True)
    license_dir = destination / "licenses"
    license_dir.mkdir()
    shutil.copy2(find_json_license(), license_dir / "nlohmann-json.txt")
    (destination / "使用说明.txt").write_text(USAGE, encoding="utf-8")

    run("plutil", "-lint", bundle / "Contents/Info.plist")
    run("codesign", "--force", "--sign", "-", "--identifier", APP_ID, bundle)
    run("codesign", "--verify", "--strict", "--verbose=2", bundle)
    run("lipo", "-info", bundle / "Contents/MacOS/RallyLite")

    archive = DIST / f"{NAME}.zip"
    if archive.exists():
        archive.unlink()
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", destination, archive)
    return archive


def main():
    force = "--force" in sys.argv[1:]
    preflight()
    app = build()
    archive = package(app, force)
    with zipfile.ZipFile(archive) as handle:
        broken = handle.testzip()
        if broken:
            sys.exit(f"Corrupt entry in archive: {broken}")
        app_entries = [name for name in handle.namelist() if "/RallyLite.app/" in name]
    print()
    print(archive)
    print(f"ZIP bytes: {archive.stat().st_size}")
    print(f"SHA256: {hashlib.sha256(archive.read_bytes()).hexdigest()}")
    print(f"Bundle entries: {len(app_entries)}")
    print("Ad-hoc signed: Gatekeeper still warns on other machines; "
          "recipients use right-click -> Open or xattr -dr com.apple.quarantine.")


if __name__ == "__main__":
    main()
