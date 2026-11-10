import hashlib
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SDK_SHA256 = "35074cafacc99a47e87c25641f98e0952576487fe29607103f83d61540ad0523"


def main():
    sdk = ROOT / "work/rally-runtime/microsoft.windows.sdk.cpp.10.0.19041.5.nupkg"
    if hashlib.sha256(sdk.read_bytes()).hexdigest() != SDK_SHA256:
        raise RuntimeError("Windows SDK package checksum mismatch")
    executable = ROOT / "build/rally-lite/RallyLite.exe"
    licenses = {
        "Microsoft-Windows-SDK.rtf": ROOT / "work/rally-runtime/sdk_license.rtf",
        "curl.txt": ROOT / "work/rally-deps/curl-8.23.0/COPYING",
        "nlohmann-json.txt": ROOT / "work/rally-deps/json/LICENSE.MIT",
    }
    for source in [executable, *licenses.values()]:
        if not source.is_file():
            raise FileNotFoundError(source)
    destination = ROOT / "dist/RallyLite-Windows-x64-preview"
    destination.mkdir(exist_ok=False)
    shutil.copy2(executable, destination / executable.name)
    with zipfile.ZipFile(sdk) as archive:
        prefix = "c/Redist/10.0.19041.0/ucrt/DLLs/x64/"
        entries = [name for name in archive.namelist() if name.startswith(prefix) and name.endswith(".dll")]
        if len(entries) != 42:
            raise RuntimeError("Unexpected SDK runtime contents")
        for name in entries:
            (destination / Path(name).name).write_bytes(archive.read(name))
    license_dir = destination / "licenses"
    license_dir.mkdir()
    for name, source in licenses.items():
        shutil.copy2(source, license_dir / name)
    (destination / "使用说明.txt").write_text(
        "RallyLite 集会提醒 · Windows x64 预览版\n\n"
        "1. 完整解压 ZIP，再双击 RallyLite.exe；不要单独移动 EXE 或删除 DLL。\n"
        "2. 输入昵称及下班搭子中的 8 位集会码，点击加入。\n"
        "3. Ctrl+Alt+G 发送安全、Ctrl+Alt+Y 发送警戒、Ctrl+Alt+R 发送危险提醒。\n"
        "4. 可修改快捷键并保存；如被其他软件占用，请改用其他组合。\n"
        "5. 关闭窗口后仍在托盘运行。彻底退出请使用退出按钮或托盘菜单。\n"
        "6. 提醒只有屏幕边框和来源文字，没有声音或系统通知。\n\n"
        "接入内置下班搭子服务，需要联网。本工具只加入已有集会，不创建集会。\n"
        "不需要安装 Python 或下班搭子主程序。身份保存在本机用户目录，不随本包分发。\n"
        "已在 Windows 10 x64 验证；目标 Windows 7 SP1 x64 尚未实机验证，不支持 32 位。\n"
        "macOS 不能使用本包。此预览版未代码签名。\n",
        encoding="utf-8-sig",
    )
    result = shutil.make_archive(str(destination), "zip", destination.parent, destination.name)
    print(result)
    print(f"ZIP bytes: {Path(result).stat().st_size}")
    print(f"SHA256: {hashlib.sha256(Path(result).read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
