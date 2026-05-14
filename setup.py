#!/usr/bin/env python3
"""
Linux SSH 巡检工具 - 安装脚本
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="linux-ssh-inspection-tool",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="一个基于 PyQt6 和 Paramiko 的 Windows 桌面应用程序，用于连接 Linux 服务器进行系统巡检",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/linux-ssh-inspection-tool",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: Microsoft :: Windows",
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "Topic :: System :: Systems Administration",
    ],
    python_requires=">=3.9",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "ssh-inspection=main:main",
        ],
    },
)