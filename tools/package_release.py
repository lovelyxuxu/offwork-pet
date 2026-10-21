"""Build a versioned desktop ZIP with user notes and no project docs or identities."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
version = re.search(r'__version__ = "([^"]+)"', (ROOT / 'offwork_pet/__init__.py').read_text(encoding='utf-8'))[1]
tag = f'v{version}-3D'
folder = ROOT / 'dist' / tag / 'OffworkPet'
archive_path = ROOT / 'dist' / f'OffworkPet-{tag}.zip'


def release_notes():
    return f'''下班搭子 v{version} · 3D 噜噜试用版
发布日期：2026-10-21

本版新增
1. 噜噜使用已确认的 v5 立体形象：头顶橘子、小圆眼睛、闭嘴浅笑。
2. 加入透明背景的 3D 预渲染动画：待机呼吸/眨眼、摸头歪头、拖动摇摆，
   以及发送/接收捏脸、轻捶时的回应。
3. 设置中标明“噜噜（3D 动画）”和“噜妹（二维）”，方便确认形象版本。
4. 窗口标题和设置标题显示版本号。
5. 发布包不附带 docs、建模工程、服务器配置或个人配置；ZIP 名称包含版本号。

使用方式
完整解压压缩包，双击 OffworkPet.exe。保留 _internal 文件夹。
适用于 Windows 10/11，不需要安装 Python 或 Blender。
升级前先从旧宠物右键菜单选择“退出下班搭子”，再启动新版本，避免单实例提示。
首次默认使用噜噜；已有配置会保留。若看到噜妹，双击宠物，在设置中选“噜噜（3D 动画）”。
程序仍使用本机原有配置与搭子身份，不要把个人 config.json 或 identity 文件发给别人。

双人连接
先自行部署搭子服务，再在两人电脑的“搭子…”中填写同一服务根地址并开启联网。
例如 http://192.0.2.10:8765（文档示例地址，需替换为自己的服务地址）。
一方“生成邀请”，把 8 位邀请码发给另一方；另一方输入并点击“连接搭子”。
邀请码 10 分钟有效。HTTP 传输未加密；HTTPS/WSS 可通过反向代理配置。

保留功能
下班倒计时、工作日/临时时间、气泡模式、大小/透明度、拖动、置顶、鼠标穿透、
托盘、提醒、配对、暂停/恢复、断线重连和解绑。
Ctrl + 滚轮调大小，Ctrl + Shift + 滚轮调透明度；双击打开设置，右键打开菜单。

试用范围
本次升级的是噜噜，噜妹保持二维形象。
3D 外观以透明动画播放，不支持自由旋转视角或实时 3D 光照。
捏脸和轻捶使用基础头部/身体回应，尚无独立手指或完整面部骨骼。

素材署名
原模型：水豚噜噜，作者 Spencer0131。
来源：https://sketchfab.com/3d-models/a5747f74f0034470a2029a85f6e9c11d
许可标记：CC BY 4.0，https://creativecommons.org/licenses/by/4.0/
修改包括配色、减面、头部摆正、眼睛与浅笑、基础动画和透明渲染。
本项目为同人适配，不声称取得原角色 IP 方的官方授权。
Qt / PySide6 等运行库的许可说明见包内第三方许可文件。
'''


def copy_licenses():
    # Conda installs keep upstream notices next to each cached package.
    cache = Path(sys.prefix) / 'pkgs'
    destination = folder / '第三方许可'
    for package in ('pyside6', 'qtbase', 'qtdeclarative', 'qtimageformats', 'shiboken6'):
        for source in sorted(cache.glob(package + '-*')) if cache.exists() else []:
            licenses = source / 'info' / 'licenses'
            if licenses.is_dir():
                shutil.copytree(licenses, destination / source.name, dirs_exist_ok=True)


def validate_assets():
    import json
    assets = ROOT / 'offwork_pet/assets/lulu3d'
    manifest = json.loads((assets / 'manifest.json').read_text(encoding='utf-8'))
    for clip, spec in manifest['clips'].items():
        for index in range(spec['frames']):
            path = assets / clip / f'{index:03d}.png'
            if not path.is_file():
                raise RuntimeError(f'Missing animation frame: {path}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip-only', action='store_true')
    args = parser.parse_args()
    validate_assets()
    if not args.zip_only:
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir',
            '--name', 'OffworkPet', '--distpath', str(folder.parent),
            '--workpath', str(ROOT / 'build' / tag), '--specpath', str(ROOT / 'work' / 'package' / tag),
            '--add-data', str(ROOT / 'offwork_pet/assets') + ';offwork_pet/assets',
            '--exclude-module', 'PySide6.QtWebEngineCore',
            '--exclude-module', 'PySide6.QtWebEngineWidgets', str(ROOT / 'main.py'),
        ], cwd=ROOT, check=True)
    if not (folder / 'OffworkPet.exe').is_file():
        raise RuntimeError('Packaged executable not found')
    (folder / '版本说明.txt').write_text(release_notes(), encoding='utf-8-sig')
    for notice in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / notice, folder / notice)
    copy_licenses()
    files = sorted(path for path in folder.rglob('*') if path.is_file())
    for path in files:
        relative = path.relative_to(folder)
        if any(part.lower() == 'docs' for part in relative.parts):
            raise RuntimeError(f'Unexpected docs in release: {relative}')
        if path.name.lower() in ('config.json', 'server-access.md', '.env') or path.suffix.lower() in ('.clixml', '.sqlite3', '.blend', '.glb') or path.name.lower().startswith('identity'):
            raise RuntimeError(f'Unexpected private or source file in release: {relative}')
    with ZipFile(archive_path, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, path.relative_to(folder.parent))
    with ZipFile(archive_path) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f'Corrupt ZIP entry: {bad}')
    print(f'READY {archive_path}', flush=True)
    print(f'ZIP {archive_path.stat().st_size / 1048576:.1f} MB; extracted {sum(path.stat().st_size for path in files) / 1048576:.1f} MB', flush=True)


if __name__ == '__main__':
    main()
