"""Create, validate, export and preview data-only character packages."""
import argparse
import json
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageSequence
from offwork_pet.packages import PackageLibrary, PackageError, load_package, identifier


from offwork_pet.pack_builder import write_json, frames_from_image, make_template, add_action


def main():
    parser = argparse.ArgumentParser(description='角色包制作与预览')
    sub = parser.add_subparsers(dest='command', required=True)
    template = sub.add_parser('from-image', help='从 PNG/WebP/GIF 创建最小角色源目录')
    template.add_argument('image', type=Path)
    template.add_argument('--id', required=True)
    template.add_argument('--name', required=True)
    template.add_argument('--author', required=True)
    template.add_argument('--license', default='未声明，请由创作者补充实际授权')
    template.add_argument('--output', type=Path, required=True)
    action = sub.add_parser('add-action', help='将已有 GIF/图片动画添加为菜单动作')
    action.add_argument('folder', type=Path)
    action.add_argument('image', type=Path)
    action.add_argument('--id', required=True)
    action.add_argument('--label', required=True)
    action.add_argument('--remote', action='store_true')
    check = sub.add_parser('check', help='检查源目录或角色包')
    check.add_argument('source', type=Path)
    build = sub.add_parser('build', help='从源目录导出角色包')
    build.add_argument('source', type=Path)
    build.add_argument('--output', type=Path, required=True)
    preview = sub.add_parser('preview', help='用正式播放器与交互逻辑预览角色包')
    preview.add_argument('source', type=Path)
    preview.add_argument('--snapshot', type=Path)
    args = parser.parse_args()
    if args.command == 'from-image':
        make_template(args)
        return
    if args.command == 'add-action':
        add_action(args)
        return
    with TemporaryDirectory() as temp:
        library = PackageLibrary(Path(temp) / 'library')
        if args.source.is_dir():
            package = load_package(args.source)
        else:
            package = library.install(args.source)
        if args.command == 'check':
            print(f'有效：{package.name} · {package.manifest["version"]} · {len(package.behavior.get("actions", {}))} 个动作')
        elif args.command == 'build':
            target = args.output.resolve()
            if target.suffix.lower() != '.petpack':
                raise PackageError('输出文件须使用 .petpack 扩展名')
            if target.is_relative_to(package.root.resolve()):
                raise PackageError('输出文件须放在角色源目录之外')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + '.tmp')
            try:
                with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                    for path in sorted(package.root.rglob('*')):
                        if path.is_file():
                            archive.write(path, path.relative_to(package.root).as_posix())
                # The same importer used by the product verifies the exported archive.
                library.install(temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
            print(f'已导出：{target}')
        else:
            from PySide6.QtCore import QTimer
            from PySide6.QtGui import QFont, QFontDatabase
            from PySide6.QtWidgets import QApplication
            from offwork_pet.app import Pet
            from offwork_pet.core import Config
            if args.source.is_dir():
                archive_path = Path(temp) / 'preview.petpack'
                with ZipFile(archive_path, 'w', ZIP_DEFLATED) as archive:
                    for path in package.root.rglob('*'):
                        if path.is_file():
                            archive.write(path, path.relative_to(package.root).as_posix())
                package = library.install(archive_path)
            library.index['seeded'] = True
            library._save()
            app = QApplication.instance() or QApplication([])
            font = Path('C:/Windows/Fonts/msyh.ttc')
            if font.exists():
                families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))
                if families:
                    app.setFont(QFont(families[0], 10))
            pet = Pet(Config(character=package.id), persist=False, package_directory=library.root, seed_starters=False)
            if args.snapshot:
                app.processEvents()
                args.snapshot.parent.mkdir(parents=True, exist_ok=True)
                if not pet.grab().save(str(args.snapshot)):
                    raise PackageError('无法保存预览图片')
                pet.quit()
                print(args.snapshot.resolve())
            else:
                app.aboutToQuit.connect(pet.buddy.stop)
                app.exec()


if __name__ == '__main__':
    try:
        main()
    except (PackageError, OSError, ValueError) as error:
        print(f'失败：{error}', file=sys.stderr)
        raise SystemExit(1)
