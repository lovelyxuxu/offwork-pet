# 下班搭子 · Offwork Pet

一只待在 Windows 桌面角落的小猫，陪你等下班。

## 当前功能

- 透明无边框悬浮窗，手绘小猫呼吸、眨眼、摇尾巴，下班和休息日会笑。
- 每秒更新下班倒计时，下班后显示“已下班”，工作日午夜自动重新计时。
- 自定义每周工作日、下班时间，以及仅当天生效的临时时间。
- 奶油橘 / 薄荷绿 / 云朵蓝配色，70%–150% 大小，气泡可隐藏。
- 拖动移动并记住位置，置顶、鼠标穿透、系统托盘、单实例保护。
- 提前 30 分钟、10 分钟和到点提醒；声音默认关闭。
- 可选登录 Windows 时自动启动，默认关闭。
- 配置保存到 `%APPDATA%\OffworkPet\config.json`，不上传个人设置。

## 运行

Windows 10/11，Python 3.10+。推荐创建独立环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

也可以双击 `start.bat`（需先安装依赖）。默认周一到周五 18:00 下班。

拖动小猫移动，双击打开设置，右键打开菜单。鼠标穿透开启后，通过系统托盘右键关闭，或选择“显示小猫 / 恢复位置”。托盘双击也能打开设置。

## 打包为 Windows 应用

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

生成 `dist\OffworkPet\OffworkPet.exe`。分发时复制整个 `OffworkPet` 文件夹；目标电脑无需安装 Python。构建依赖和产物不提交 Git。

## 测试

```powershell
python -m unittest discover -s tests -v
```

提醒依据运行期间跨过阈值触发，启动时不会补发已过期提醒；休眠恢复后仅提醒最近跨过的一个节点。系统时间回拨不会重复触发同一节点。临时时间也可以把非工作日设为当天工作日。

## 边界与后续

当前按本机时间计算同一天的下班时间；暂不支持跨夜班、法定节假日自动同步、轮班日历和导入宠物素材。Windows 通知是否出现受系统通知设置和勿扰模式影响。

如果改动源代码或移动应用目录，请重新开启一次“登录 Windows 时自动启动”，以更新启动路径。卸载前先关闭该选项。

界面使用 PySide6 / Qt，遵循其适用许可；分发应保留随包 Qt/PySide6 许可文件。项目尚未指定开源许可证。
