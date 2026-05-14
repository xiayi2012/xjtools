# Linux SSH 巡检工具

一个基于 PyQt6 和 Paramiko 的 Windows 桌面应用程序，用于连接 Linux 服务器进行系统巡检。

## 功能特点

- **SSH 连接管理**: 支持密码和密钥认证，连接信息本地保存
- **多产品支持**: 办公智盾、准入、EDR、零信任四款产品专用巡检脚本
- **脚本执行**: 支持 Bash、Python 等多语言自定义脚本
- **实时输出**: GUI 内嵌终端实时显示执行过程
- **报告导出**: 支持 HTML、PDF、CSV 多格式报告生成

## 系统要求

- Windows 10/11
- Python 3.9+
- PyQt6
- Paramiko

## 安装

```bash
# 克隆仓库
git clone <repository-url>

# 安装依赖
pip install -r requirements.txt

# 运行程序
python src/main.py
```

## 项目结构

```
linux-ssh-inspection-tool/
├── src/                    # 源代码目录
│   ├── main.py            # 主入口
│   ├── ui/                # 用户界面
│   ├── core/              # 核心功能
│   ├── scripts/           # 巡检脚本
│   └── utils/             # 工具函数
├── tests/                 # 测试文件
├── config/                # 配置文件
├── requirements.txt       # 依赖文件
└── README.md             # 项目说明
```

## 使用说明

1. 启动程序后，在连接页面输入服务器信息
2. 连接成功后，选择要巡检的产品
3. 选择巡检项或上传自定义脚本
4. 点击执行，实时查看输出
5. 完成后导出巡检报告

## 开发

```bash
# 安装开发依赖
pip install -r requirements.txt

# 运行测试
pytest

# 代码格式化
black src/ tests/

# 代码检查
flake8 src/ tests/
```

## 许可证

MIT License