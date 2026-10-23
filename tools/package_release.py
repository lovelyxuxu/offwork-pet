"""Build a versioned desktop ZIP with user notes and no project docs or identities."""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
version = re.search(r'__version__ = "([^"]+)"', (ROOT / 'offwork_pet/__init__.py').read_text(encoding='utf-8'))[1]
tag = f'v{version}-CharacterPacks'
folder = ROOT / 'dist' / tag / 'OffworkPet'
archive_path = ROOT / 'dist' / f'OffworkPet-{tag}.zip'


def release_notes():
    return f'''下班搭子 v{version} · 角色包框架试用版
发布日期：2026-10-22

本版新增
主动探索可达窗口边缘：观察、移动或跳跃、落脚休息；可选识别前台窗口内部面板边界。
新增桌面小球、下班前十分钟及到点仪式、搭子咖啡/小花/星星礼物、可暂停的专注计时。
按角色保存本机昵称和互动偏好；角色制作室支持图片/GIF、动作、区域圈选、触发规则、预览及导出。
右键动作面板提供“玩小球 / 下班彩排 / 去逛逛”，下方“专注”进入专注计时。
角色管理中的“制作自己的角色”打开制作室。内部面板识别默认关闭，可在偏好设置开启。

此前更新能力
支持文件级增量更新，点击“更新并重启”后优先下载适用于当前版本的小包。
本地文件与增量包逐项校验，不适用时自动回退完整更新；取消下载后清理本次临时文件。
继续保留签名校验、启动确认和失败回滚，以及用户设置、角色选择和搭子关系。
旧版用户首次升级到支持增量更新的版本仍需完整下载，之后可自动使用增量更新。

此前版本
噜噜（noah）补齐左右张望，支持 16 向鼠标跟随；主动与搭子动作优先。
新增网站原始角色“月薪猫”，作者 vjokaye；9 组源动作、7 个搭子互动入口，保留舒缓节奏。
软件更新每 4 小时检查、失败后 30 分钟重试，支持取消下载和临时文件清理。
新增网站原始角色“噜妹（草莓睡衣）”，作者 FullStackHan；升级后自动导入，右键面板下方“换角色”即可选择。
默认仍为噜噜（noah），保留用户已有选择。

已有功能
1. 右键“角色管理…”可导入、切换和删除 .petpack 角色包，无需手动解压。
2. 噜噜和噜妹已转成普通角色包，两者均可删除；删除最后一个角色后可重新导入。
3. 动作、区域、规则、本地菜单和远程互动由包定义，不要求所有角色拥有相同动作。
4. 按字节管理动画缓存，切换时清理旧角色的动作和队列。
5. 内置产品服务器，用户不填写 IP 或其他联网信息。
6. 默认使用网页上的“噜噜（noah）”角色包；原有角色仍可在角色管理中选择。
7. 全新浅色卡片界面：日程、外观和偏好分组，统一角色与搭子设置。
8. 右键宠物打开常驻动作面板；“我的动作 / 搭子互动”切换，点击后保持打开，右上角可关闭。
9. 连接前检查服务版本；旧版服务与网页误转发会给出明确提示，停止无效重连。
10. 自动检查软件更新；发现新版后提示，一键下载、校验、替换并重启，新版启动失败自动恢复旧版。

使用方式
完整解压压缩包，双击 OffworkPet.exe。保留 _internal 文件夹。
适用于 Windows 10/11，不需要安装 Python 或 Blender。
升级前先从旧宠物右键菜单选择“退出下班搭子”，再启动新版本，避免单实例提示。
首次默认使用噜噜（noah）；已有配置会保留。右键打开面板，在下方“换角色”选择。
程序仍使用本机原有配置与搭子身份，不要把个人 config.json 或 identity 文件发给别人。
本版支持增量更新；已经支持自动更新的旧版可直接在程序内升级，此次仍为完整下载。
升级后有适配当前版本的增量包时自动下载小包，否则使用完整包。

双人连接
两人电脑均在“搭子…”中开启联网，应用自动连接内置的搭子服务。
无需自行部署服务，也无需填写 IP、端口或网络地址。
一方“生成邀请”，把 8 位邀请码发给另一方；另一方输入并点击“连接搭子”。
邀请码 10 分钟有效。新版动态互动需要服务器支持 v2 协议，旧版服务需由产品维护者升级。

角色互动
单击和长按的反应由角色包定义。Alt + 按住鼠标来回移动可触发包中声明的抚摸反应。
普通左键拖动仍移动窗口；双击打开设置，右键打开动作与互动面板。
面板下方提供换角色、搭子设置、系统设置及更多操作；靠近屏幕底部时自动移到侧边。

保留功能
下班倒计时、工作日/临时时间、气泡模式、大小/透明度、拖动、置顶、鼠标穿透、
托盘、提醒、配对、暂停/恢复、断线重连和解绑。
Ctrl + 滚轮调大小，Ctrl + Shift + 滚轮调透明度；双击打开设置，右键打开菜单。

试用范围
支持已有 PNG/WebP 静态帧与图集；素材决定角色实际能够表现的动作。
制作室编排已有素材，不自动生成缺失动作。原始噜噜、二维噜妹支持下班/礼物/专注反应；
噜噜（noah）与草莓睡衣噜妹支持探索跳跃和玩球，月薪猫支持水平玩球和漫步。
内部 UI 识别依赖应用提供控件边界，不保证识别任意自绘卡片；失败时仍使用窗口外框。
本版不包含在线工坊网站、实时 3D 或自动生成动作的模型。

素材署名
原模型：水豚噜噜，作者 Spencer0131。
来源：https://sketchfab.com/3d-models/a5747f74f0034470a2029a85f6e9c11d
许可标记：CC BY 4.0，https://creativecommons.org/licenses/by/4.0/
修改包括配色、减面、头部摆正、眼睛与浅笑、基础动画和透明渲染。
本项目为同人适配，不声称取得原角色 IP 方的官方授权。
“噜噜（noah）”图集作者：noah。
来源：https://codex-pets.net/#/pets/noah-lulu
“噜妹（草莓睡衣）”图集作者：FullStackHan。
来源：https://codex-pets.net/#/pets/lumei
“月薪猫”图集作者：vjokaye。
来源：https://codex-pets.net/#/pets/monthly-salary-cat
源页面与素材包未提供具体许可证；保留原作者及来源，不声明为本项目 MIT 授权。
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
    from importlib.metadata import distribution
    for package in ('PySide6_Essentials', 'PySide6_Addons', 'shiboken6'):
        metadata = distribution(package)
        for entry in metadata.files or []:
            if 'licenses' in (part.lower() for part in entry.parts) or entry.name.upper().startswith('LICENSE'):
                source = Path(metadata.locate_file(entry))
                if source.is_file():
                    target = destination / package / Path(*entry.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    pillow = distribution('Pillow')
    for entry in pillow.files or []:
        if entry.name == 'LICENSE' and 'licenses' in entry.parts:
            source = Path(pillow.locate_file(entry))
            target = destination / 'Pillow'
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target / 'LICENSE')
    crypto = distribution('cryptography')
    for entry in crypto.files or []:
        if entry.name.startswith('LICENSE') and 'licenses' in entry.parts:
            source = Path(crypto.locate_file(entry))
            target = destination / 'cryptography' / entry.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def validate_assets():
    from tempfile import TemporaryDirectory
    sys.path.insert(0, str(ROOT))
    from offwork_pet.packages import PackageLibrary
    assets = sorted((ROOT / 'offwork_pet/assets/starter-packs').glob('*.petpack'))
    if not assets:
        raise RuntimeError('No starter character packages')
    with TemporaryDirectory() as temporary:
        library = PackageLibrary(temporary)
        for archive in assets:
            library.install(archive)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip-only', action='store_true')
    parser.add_argument('--sign-key', type=Path)
    parser.add_argument('--base-archive', type=Path, action='append', default=None,
        help='Full release ZIP to generate an incremental update from (repeatable).')
    args = parser.parse_args()
    if not args.zip_only and importlib.util.find_spec('numpy') is not None:
        raise RuntimeError('Use a clean release venv without NumPy; see requirements-release.txt.')
    executable = folder / 'OffworkPet.exe'
    if not args.zip_only and executable.exists():
        try:
            with executable.open('r+b'):
                pass
        except PermissionError as error:
            raise RuntimeError('Target release is running or locked. Increase the version or close that release before rebuilding.') from error
    validate_assets()
    if not args.zip_only:
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onefile',
            '--name', 'OffworkPetUpdater', '--paths', str(ROOT),
            '--distpath', str(ROOT / 'build' / tag / 'helper'),
            '--workpath', str(ROOT / 'build' / tag / 'helper-work'),
            '--specpath', str(ROOT / 'work' / 'package' / tag),
            str(ROOT / 'tools/run_updater.py'),
        ], cwd=ROOT, check=True)
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
            '--name', 'OffworkPet', '--distpath', str(folder.parent),
            '--workpath', str(ROOT / 'build' / tag), '--specpath', str(ROOT / 'work' / 'package' / tag),
            '--add-data', str(ROOT / 'offwork_pet/assets/starter-packs') + ';offwork_pet/assets/starter-packs',
            '--add-data', str(ROOT / 'offwork_pet/assets/ui') + ';offwork_pet/assets/ui',
            '--additional-hooks-dir', str(ROOT / 'tools/pyinstaller_hooks'),
            '--exclude-module', 'PySide6.QtWebEngineCore',
            '--exclude-module', 'PySide6.QtWebEngineWidgets',
            '--exclude-module', 'numpy', '--exclude-module', 'scipy',
            '--exclude-module', 'PySide6.QtMultimedia',
            '--exclude-module', 'PySide6.QtMultimediaWidgets',
            str(ROOT / 'main.py'),
        ], cwd=ROOT, check=True)
        shutil.copy2(ROOT / 'build' / tag / 'helper/OffworkPetUpdater.exe', folder / 'OffworkPetUpdater.exe')
    if not (folder / 'OffworkPet.exe').is_file():
        raise RuntimeError('Packaged executable not found')
    # The product UI is Chinese; retain Chinese and English Qt messages.
    translations = folder / '_internal/PySide6/translations'
    for translation in translations.glob('*.qm'):
        if not translation.stem.endswith(('_zh_CN', '_en')):
            translation.unlink()
    (folder / '版本说明.txt').write_text(release_notes(), encoding='utf-8-sig')
    for notice in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / notice, folder / notice)
    copy_licenses()
    managed = sorted(path.relative_to(folder).as_posix() for path in folder.rglob('*')
        if path.is_file() and path.name != 'release-info.json')
    (folder / 'release-info.json').write_text(json.dumps({'appId': 'offwork-pet', 'version': version,
        'files': managed + ['release-info.json']}, ensure_ascii=False, indent=2), encoding='utf-8')
    files = sorted(path for path in folder.rglob('*') if path.is_file())
    for path in files:
        relative = path.relative_to(folder)
        if path.name.lower().startswith('mkl') or any(part in ('numpy', 'scipy') for part in relative.parts):
            raise RuntimeError('Unexpected scientific computing dependency in desktop release: ' + str(relative))
        if path.name.lower() in ('qt6quick.dll', 'qt6qml.dll', 'qt6pdf.dll'):
            raise RuntimeError('Unexpected unused Qt module: ' + str(relative))
        if any(part.lower() in ('docs', 'tests', '__pycache__', '.git') for part in relative.parts):
            raise RuntimeError(f'Unexpected docs in release: {relative}')
        if path.name.lower() in ('config.json', 'server-access.md', '.env') or path.suffix.lower() in ('.clixml', '.sqlite3', '.blend', '.glb', '.key', '.pem', '.pfx', '.p12', '.log', '.bak', '.tmp', '.pdb') or path.name.lower().startswith('identity'):
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
    components = defaultdict(int)
    for path in files:
        relative = path.relative_to(folder)
        group = '/'.join(relative.parts[:2]) if relative.parts[0] == '_internal' else relative.parts[0]
        components[group] += path.stat().st_size
    with archive_path.open('rb') as stream:
        archive_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (ROOT / 'dist' / f'release-audit-v{version}.json').write_text(json.dumps({
        'version': version, 'zipBytes': archive_path.stat().st_size,
        'installedBytes': sum(path.stat().st_size for path in files), 'sha256': archive_digest,
        'fileCount': len(files), 'components': dict(sorted(components.items(), key=lambda entry: -entry[1]))}, indent=2), encoding='utf-8')
    if args.sign_key:
        base_arguments = [argument for base in args.base_archive or [] for argument in ('--base-archive', str(base))]
        subprocess.run([sys.executable, str(ROOT / 'tools/release_manifest.py'), '--key', str(args.sign_key),
            '--archive', str(archive_path), '--output', str(ROOT / 'dist/latest.json'), *base_arguments], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
