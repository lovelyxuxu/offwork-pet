# 下班搭子 · Offwork Pet

Windows 桌面角色、下班倒计时和搭子互动。Linux 或其他支持 Docker 的系统可运行独立联网服务。

| 模块 | 源码 |
| --- | --- |
| Windows 桌宠 | [offwork_pet](offwork_pet) |
| 搭子、集会、聊天、角色资源及管理 API | [server](server) |
| macOS 集会接收端 RallyLite | [rally-lite](rally-lite) · [预览版下载](https://github.com/lovelyxuxu/offwork-pet/releases/tag/macos-preview) |
| 官网与角色目录 API | [offwork-pet-web](https://github.com/lovelyxuxu/offwork-pet-web) |
| Android 接收端 | [offwork-pet-app](https://github.com/lovelyxuxu/offwork-pet-app) · [下载](https://github.com/lovelyxuxu/offwork-pet-app/releases) |
| 管理前端（Vue） | [offwork-pet-admin-web](https://github.com/lovelyxuxu/offwork-pet-admin-web) |
| 角色制作工具与示例 | [offwork-pet-models](https://github.com/lovelyxuxu/offwork-pet-models) |

## Windows 桌宠

[下载 Windows v0.6.0-preview.13 预览版](https://github.com/lovelyxuxu/offwork-pet/releases/tag/v0.6.0-preview.13)。解压后运行 `OffworkPet.exe`。

从源码运行时，安装 Python 3.10 或以上版本，在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

仓库包含原创示例角色“小芽”。右键打开“外观与设置 → 角色管理”可导入自行制作或已获授权的 `.petpack`。见 [角色包制作与使用](docs/角色包制作与使用.md)。联网功能需在“搭子 → 服务器”中填写地址并开启。

## 自托管

```sh
python tools/init_selfhost.py
docker compose up -d --build
docker compose ps
```

默认监听 `127.0.0.1:8765`。访问 `http://127.0.0.1:8765/admin/hosting` 可配置接入码及实例；管理界面位于 `/admin/`。Compose 包含 PostgreSQL，HTTPS、持久化、迁移和备份见 [自托管部署](docs/自托管部署.md)。

本地仅运行 Python API 可执行 `python -m pip install -r server/requirements.txt` 和 `python -m server --port 8765`。

## 测试与许可

开发环境依赖见 `requirements-dev.txt`。后端可运行 `python -m unittest discover -s tests -p "test_selfhost*.py"`。部分桌面回归使用原角色包或台词包作为素材夹具，运行这些测试前需要自行准备相应授权和素材。

自有程序、文档和示例角色采用 [MIT](LICENSE)。第三方组件保留其自身许可，历史素材的来源详见 [第三方说明](THIRD_PARTY_NOTICES.md)。
