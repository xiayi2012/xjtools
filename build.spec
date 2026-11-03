# -*- mode: python ; coding: utf-8 -*-
"""
AI智能运维助手 - PyInstaller 打包配置
"""

import os

block_cipher = None

# 获取项目根目录
project_root = os.getcwd()

a = Analysis(
    ['src/main.py'],
    pathex=[project_root, os.path.join(project_root, 'src')],
    binaries=[],
    datas=[
        ('src/ui', 'ui'),
        ('src/core', 'core'),
        ('src/scripts', 'scripts'),
        ('src/utils', 'utils'),
        ('src/FAQ2SH', 'FAQ2SH'),
        ('icons', 'icons'),
        ('src/scripts', 'src/scripts'),
    ],
    hiddenimports=[
        'PyQt6',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
        'paramiko',
        'cryptography',
        'cryptography.fernet',
        'cryptography.hazmat',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.kdf',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'qfluentwidgets',
        'utils',
        'requests',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大型库
        'numpy',
        'scipy',
        'matplotlib',
        'pytest',
        'pandas',
        'tkinter',
        'unittest',
        'pydoc',
        'doctest',
        'distutils',
        'setuptools',
        'pip',
        'wheel',
        'IPython',
        'jupyter',
        'notebook',
        'sphinx',
        'docutils',
        'sqlite3',
        'dbm',
        'gdbm',
        'bsddb',
        'poplib',
        'imaplib',
        'nntplib',
        'smtplib',
        'smtpd',
        'telnetlib',
        'cgi',
        'cgitb',
        'wsgiref',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='AI智能运维助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(project_root, 'icons', 'app.ico'),
    version='version_info.txt',  # 版本信息
)
