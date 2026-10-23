"""Keep the Qt GUI hook while omitting unused PDF and QML virtual-keyboard plugins."""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [(source, target) for source, target in binaries
    if 'qpdf' not in source.lower() and 'qtvirtualkeyboard' not in source.lower()]
