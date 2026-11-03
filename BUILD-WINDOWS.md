# Windows 构建

使用 Python 3.12，在项目根目录执行：

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -X utf8 -m pip install -r requirements-build.txt
.\venv\Scripts\python.exe -X utf8 -m PyInstaller --noconfirm --clean build.spec
```

生成文件：`dist/AI智能运维助手.exe`，可直接双击运行，无需另装 Python。

源码启动：

```powershell
.\venv\Scripts\python.exe -X utf8 src/main.py
```

服务器连接信息和大模型 API 需要在程序界面中配置。可执行文件运行后会在其旁边生成 `config` 目录，请将程序放在可写目录中。

`requirements-build.txt` 补充了界面组件、打包工具，以及 Word/PDF/Excel 导入依赖，并固定 Qt 版本。
