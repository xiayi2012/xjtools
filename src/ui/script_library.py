"""
脚本库对话框模块
提供脚本的上传、添加、编辑、删除和管理功能
支持按产品分组显示脚本
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QTextEdit, QLineEdit, QComboBox, QMessageBox,
    QWidget, QScrollArea, QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize, QDateTime
from PyQt6.QtGui import QFont
from qfluentwidgets import PrimaryPushButton, ComboBox, SearchLineEdit
import json
import os
from pathlib import Path

from utils import get_config_dir as _get_config_dir
from .dialog_icons import dialog_icon


# 产品列表
PRODUCTS = ["准入系统", "EDR", "零信任"]


class ScriptLibraryDialog(QDialog):
    """脚本库对话框 - 管理和选择脚本"""
    
    script_selected = pyqtSignal(str)  # 选择脚本信号
    scripts_updated = pyqtSignal()  # 脚本库更新信号
    
    def __init__(self, current_product: str = None, parent=None):
        super().__init__(parent)
        self.current_product = current_product or "通用"
        self.all_scripts = []
        self.filtered_scripts = []
        self.init_ui()
        self.load_scripts()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("脚本库")
        self.setWindowIcon(dialog_icon("jiaoben.svg"))
        self.setMinimumSize(850, 600)
        self.setStyleSheet("""
            QDialog {
                background: #f8fafc;
            }
            QLabel#dialogTitle {
                font-size: 22px;
                font-weight: 700;
                color: #0f172a;
            }
            QLabel#productBadge {
                background: #f0fdfa;
                color: #0f766e;
                border: 1px solid #99f6e4;
                padding: 5px 14px;
                border-radius: 14px;
                font-size: 13px;
                font-weight: 700;
            }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(20, 20, 20, 20)
        
        # 头部区域
        header_widget = QWidget()
        header_widget.setObjectName("headerWidget")
        header_widget.setStyleSheet(f"""
            QWidget#headerWidget {{
                background: #ffffff;
                border: 1px solid #dbe3ee;
                border-left: 4px solid #50C9C3;
                border-radius: 10px;
            }}
        """)
        header_layout = QVBoxLayout(header_widget)
        header_layout.setSpacing(12)
        
        # 标题行
        title_row = QHBoxLayout()
        
        title_label = QLabel("脚本库")
        title_label.setObjectName("dialogTitle")
        title_row.addWidget(title_label)
        
        title_row.addStretch()
        
        # 当前产品标签（只读显示，不允许切换）
        product_badge = QLabel(f"[{self.current_product}]")
        product_badge.setObjectName("productBadge")
        title_row.addWidget(product_badge)
        
        header_layout.addLayout(title_row)
        
        # 搜索行
        search_row = QHBoxLayout()
        search_row.setSpacing(12)
        
        self.search_input = SearchLineEdit()
        self.search_input.setPlaceholderText("搜索脚本名称...")
        self.search_input.setFixedHeight(36)
        self.search_input.setStyleSheet("""
           QLineEdit {
                background: white;
                border: none;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 14px;
            }
        """)
        self.search_input.textChanged.connect(self.on_search_changed)
        search_row.addWidget(self.search_input, 1)
        
        header_layout.addLayout(search_row)
        
        main_layout.addWidget(header_widget)
        
        # 主内容区域
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setSpacing(0)
        content_layout.setContentsMargins(0, 20, 0, 0)
        
        # 列表标题（带背景）
        header_container = QWidget()
        header_container.setStyleSheet("""
            background: #f9fafb;
            border: 1px solid #e5e7eb;
            border-bottom: none;
            border-radius: 12px 12px 0 0;
        """)
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(16, 12, 16, 12)
        header_layout.setSpacing(0)
        
        name_header = QLabel("脚本名称")
        name_header.setStyleSheet("font-size: 13px; font-weight: bold; color: #6b7280;")
        name_header.setMinimumWidth(280)
        header_layout.addWidget(name_header)
        
        type_header = QLabel("类型")
        type_header.setStyleSheet("font-size: 13px; font-weight: bold; color: #6b7280;")
        type_header.setMinimumWidth(120)
        header_layout.addWidget(type_header)
        
        time_header = QLabel("添加时间")
        time_header.setStyleSheet("font-size: 13px; font-weight: bold; color: #6b7280;")
        time_header.setMinimumWidth(160)
        header_layout.addWidget(time_header)
        
        header_layout.addStretch()
        
        content_layout.addWidget(header_container)
        
        # 脚本列表（紧贴标题）
        list_container = QWidget()
        list_container.setObjectName("listContainer")
        list_container.setStyleSheet("""
            QWidget#listContainer {
                background: white;
                border: 1px solid #e5e7eb;
                border-top: none;
                border-radius: 0 0 10px 10px;
            }
        """)
        list_layout = QVBoxLayout(list_container)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(0)
        
        self.script_list = QListWidget()
        self.script_list.setObjectName("scriptList")
        self.script_list.setSpacing(6)
        self.script_list.itemClicked.connect(self.on_script_selected)
        self.script_list.setStyleSheet("""
            QListWidget#scriptList {
                background: white;
                border: none;
                outline: none;
                padding: 10px;
            }
            QListWidget#scriptList::item {
                height: 58px;
                background: white;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 0;
                margin: 0;
            }
            QListWidget#scriptList::item:selected {
                background: #50C9C3;
                border-color: #50C9C3;
            }
            QListWidget#scriptList::item:selected * {
                color: white;
            }
            QListWidget#scriptList::item:!selected:hover {
                background: #f3f4f6;
            }
        """)
        list_layout.addWidget(self.script_list)
        
        # 空状态提示（覆盖在列表上）
        self.empty_label = QLabel("暂无脚本，请上传或添加脚本")
        self.empty_label.setObjectName("emptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("""
            QLabel#emptyLabel {
                font-size: 16px;
                color: #9ca3af;
                padding: 60px;
                background: transparent;
            }
        """)
        self.empty_label.setVisible(False)
        list_layout.addWidget(self.empty_label)
        
        content_layout.addWidget(list_container, 1)
        
        main_layout.addWidget(content_widget, 1)
        
        # 底部按钮区域
        bottom_widget = QWidget()
        bottom_widget.setObjectName("bottomWidget")
        bottom_widget.setStyleSheet("""
            QWidget#bottomWidget {
                background: white;
                border: 1px solid #dbe3ee;
                border-radius: 10px;
                padding: 16px 20px;
            }
        """)
        bottom_layout = QHBoxLayout(bottom_widget)
        bottom_layout.setSpacing(12)
        
        # 左侧上传/添加按钮
        self.upload_btn = QPushButton("上传脚本")
        self.upload_btn.setObjectName("uploadBtn")
        self.upload_btn.setFixedHeight(40)
        self.upload_btn.setFixedWidth(120)
        self.upload_btn.setStyleSheet("""
            QPushButton#uploadBtn {
                background: #f3f4f6;
                color: #374151;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#uploadBtn:hover {
                background: #e5e7eb;
                border-color: #50C9C3;
            }
        """)
        self.upload_btn.clicked.connect(self.upload_script)
        bottom_layout.addWidget(self.upload_btn)
        
        self.add_btn = PrimaryPushButton("添加脚本")
        self.add_btn.setObjectName("addBtn")
        self.add_btn.setFixedHeight(40)
        self.add_btn.setFixedWidth(120)
        self.add_btn.clicked.connect(self.add_script)
        bottom_layout.addWidget(self.add_btn)
        
        # 编辑和删除按钮（初始禁用）
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.setObjectName("editBtn")
        self.edit_btn.setFixedHeight(40)
        self.edit_btn.setFixedWidth(100)
        self.edit_btn.setEnabled(False)
        self.edit_btn.setStyleSheet("""
            QPushButton#editBtn {
                background: #f3f4f6;
                color: #9ca3af;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#editBtn:enabled {
                background: #f3f4f6;
                color: #374151;
                border: 1px solid #e5e7eb;
            }
            QPushButton#editBtn:enabled:hover {
                background: #e5e7eb;
            }
        """)
        self.edit_btn.clicked.connect(self.on_edit_clicked)
        bottom_layout.addWidget(self.edit_btn)
        
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("deleteBtn")
        self.delete_btn.setFixedHeight(40)
        self.delete_btn.setFixedWidth(100)
        self.delete_btn.setEnabled(False)
        self.delete_btn.setStyleSheet("""
            QPushButton#deleteBtn {
                background: #fef2f2;
                color: #9ca3af;
                border: 1px solid #fecaca;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#deleteBtn:enabled {
                background: #fef2f2;
                color: #ef4444;
                border: 1px solid #fecaca;
            }
            QPushButton#deleteBtn:enabled:hover {
                background: #fee2e2;
            }
        """)
        self.delete_btn.clicked.connect(self.on_delete_clicked)
        bottom_layout.addWidget(self.delete_btn)
        
        bottom_layout.addStretch()
        
        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedHeight(40)
        close_btn.setFixedWidth(100)
        close_btn.setStyleSheet("""
            QPushButton#closeBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#closeBtn:hover {
                background: #3eb8b3;
            }
        """)
        close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(close_btn)

        common_script_btn_style = """
            QPushButton, PrimaryPushButton {
                background: #ffffff;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover, PrimaryPushButton:hover {
                background: #f8fafc;
                border-color: #50C9C3;
                color: #0f766e;
            }
            QPushButton:disabled, PrimaryPushButton:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border-color: #e2e8f0;
            }
        """
        for button in (self.upload_btn, self.add_btn, self.edit_btn, close_btn):
            button.setStyleSheet(common_script_btn_style)
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background: #ffffff;
                color: #b91c1c;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #fff7f7;
                border-color: #fca5a5;
            }
            QPushButton:disabled {
                background: #f8fafc;
                color: #94a3b8;
                border-color: #e2e8f0;
            }
        """)
        
        main_layout.addWidget(bottom_widget)
    
    def get_scripts_dir(self) -> Path:
        """获取脚本存储目录"""
        scripts_dir = Path(_get_config_dir()) / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return scripts_dir
    
    def load_scripts(self):
        """加载已保存的脚本"""
        self.all_scripts = []
        scripts_dir = self.get_scripts_dir()
        
        # 加载索引文件
        index_file = scripts_dir / "index.json"
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    self.all_scripts = json.load(f)
            except:
                self.all_scripts = []
        
        # 确保所有脚本都有product字段和created_time字段
        for script in self.all_scripts:
            if 'product' not in script:
                script['product'] = '通用'
            if 'created_time' not in script:
                # 使用文件修改时间作为创建时间
                path = script.get('path', '')
                if path and os.path.exists(path):
                    script['created_time'] = os.path.getmtime(path)
                else:
                    script['created_time'] = 0
        
        self.apply_filter()
    
    def save_scripts(self):
        """保存脚本索引"""
        scripts_dir = self.get_scripts_dir()
        index_file = scripts_dir / "index.json"
        
        try:
            with open(index_file, 'w', encoding='utf-8') as f:
                json.dump(self.all_scripts, f, indent=2, ensure_ascii=False)
            # 发出更新信号
            self.scripts_updated.emit()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存脚本索引: {str(e)}")
    
    def apply_filter(self):
        """应用筛选条件"""
        search_text = self.search_input.text().lower()
        
        self.filtered_scripts = []
        
        for script in self.all_scripts:
            # 产品筛选 - 只显示当前产品的脚本
            if script.get('product', '通用') != self.current_product:
                continue
            
            # 搜索筛选
            if search_text and search_text not in script.get('name', '').lower():
                continue
            
            self.filtered_scripts.append(script)
        
        # 按添加时间排序（最新在前）
        self.filtered_scripts.sort(key=lambda x: x.get('created_time', 0), reverse=True)
        
        self.update_script_list()
    
    def on_search_changed(self, text: str):
        """搜索文本变化"""
        self.apply_filter()
    
    def update_script_list(self):
        """更新脚本列表"""
        self.script_list.clear()
        
        # 显示/隐藏空状态
        if not self.filtered_scripts:
            self.empty_label.setVisible(True)
        else:
            self.empty_label.setVisible(False)
            
            # 创建列表项
            for script in self.filtered_scripts:
                # 确定类型图标
                script_type = script.get('type', 'Shell')
                if 'Python' in script_type:
                    icon = '[Py]'
                elif 'Shell' in script_type:
                    icon = '[Sh]'
                else:
                    icon = '[*]'
                
                # 格式化时间
                created_time = script.get('created_time', 0)
                if created_time:
                    dt = QDateTime.fromSecsSinceEpoch(int(created_time))
                    time_str = dt.toString("yyyy-MM-dd hh:mm")
                else:
                    time_str = '未知'
                
                # 创建列表项（不设置文本，只用widget显示）
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, script)
                item.setSizeHint(QSize(0, 58))  # 设置固定行高
                
                # 创建自定义widget显示详情
                widget = QWidget()
                widget.setStyleSheet("background: transparent;")
                widget_layout = QHBoxLayout(widget)
                widget_layout.setContentsMargins(18, 0, 18, 0)
                widget_layout.setSpacing(8)
                
                # 名称
                name_label = QLabel(f"{icon} {script.get('name', '未命名')}")
                name_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #1f2937; background: transparent;")
                name_label.setMinimumWidth(280)
                widget_layout.addWidget(name_label)
                
                # 类型
                type_label = QLabel(script.get('type', 'Shell'))
                type_label.setStyleSheet("font-size: 13px; color: #6b7280; background: transparent;")
                type_label.setMinimumWidth(120)
                widget_layout.addWidget(type_label)
                
                # 时间
                time_label = QLabel(time_str)
                time_label.setStyleSheet("font-size: 13px; color: #9ca3af; background: transparent;")
                time_label.setMinimumWidth(160)
                widget_layout.addWidget(time_label)
                
                widget_layout.addStretch()
                
                self.script_list.addItem(item)
                self.script_list.setItemWidget(item, widget)
        
        # 禁用编辑和删除按钮
        self.edit_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)
    
    def on_script_selected(self, item: QListWidgetItem):
        """脚本被选中"""
        script = item.data(Qt.ItemDataRole.UserRole)
        if script:
            # 启用编辑和删除按钮
            self.edit_btn.setEnabled(True)
            self.delete_btn.setEnabled(True)
            self.current_edit_script = script
            self.current_delete_script = script
    
    def on_edit_clicked(self):
        """编辑按钮点击"""
        if hasattr(self, 'current_edit_script') and self.current_edit_script:
            self.edit_script(self.current_edit_script)
    
    def on_delete_clicked(self):
        """删除按钮点击"""
        if hasattr(self, 'current_delete_script') and self.current_delete_script:
            self.delete_script(self.current_delete_script)
    
    def upload_script(self):
        """上传脚本文件"""
        from PyQt6.QtWidgets import QFileDialog
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择脚本文件",
            "",
            "脚本文件 (*.sh *.py);;Shell 脚本 (*.sh);;Python 脚本 (*.py)"
        )
        
        if not file_path:
            return
        
        file_name = os.path.basename(file_path)
        
        # 验证文件类型
        if not (file_name.endswith('.sh') or file_name.endswith('.py')):
            QMessageBox.warning(self, "不支持的文件类型", "脚本库只支持 .sh 和 .py 格式的脚本文件")
            return
        
        try:
            # 确定脚本类型
            if file_name.endswith('.sh'):
                script_type = "Shell"
            else:
                script_type = "Python"
            
            # 获取产品
            product = self.current_product
            
            # 复制到脚本目录（按产品子目录）
            scripts_dir = self.get_scripts_dir() / product
            scripts_dir.mkdir(parents=True, exist_ok=True)
            
            dest_path = scripts_dir / file_name
            
            # 处理重名
            counter = 1
            base_name = file_name.rsplit('.', 1)[0] if '.' in file_name else file_name
            ext = '.' + file_name.rsplit('.', 1)[1] if '.' in file_name else ''
            while dest_path.exists():
                dest_path = scripts_dir / f"{base_name}_{counter}{ext}"
                counter += 1
            
            import shutil
            shutil.copy2(file_path, dest_path)
            
            # 添加到索引
            script = {
                'name': dest_path.name,
                'type': script_type,
                'path': str(dest_path),
                'product': product,
                'created_time': os.path.getmtime(dest_path)
            }
            self.all_scripts.append(script)
            self.save_scripts()
            self.apply_filter()
            
            QMessageBox.information(self, "上传成功", f"脚本 {dest_path.name} 已上传到 {product}")
            
        except Exception as e:
            QMessageBox.warning(self, "上传失败", f"上传脚本时出错: {str(e)}")
    
    def add_script(self):
        """添加新脚本"""
        dialog = ScriptInputDialog(self.current_product, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            script_name = dialog.script_name
            script_content = dialog.script_content
            script_type = dialog.script_type
            
            if not script_name or not script_content:
                return
            
            try:
                scripts_dir = self.get_scripts_dir() / self.current_product
                scripts_dir.mkdir(parents=True, exist_ok=True)
                
                script_path = scripts_dir / script_name
                
                # 保存脚本内容
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write(script_content)
                
                created_time = os.path.getmtime(script_path)
                
                # 添加到索引
                script = {
                    'name': script_name,
                    'type': script_type,
                    'path': str(script_path),
                    'product': self.current_product,
                    'created_time': created_time
                }
                self.all_scripts.append(script)
                self.save_scripts()
                self.apply_filter()
                
                QMessageBox.information(self, "添加成功", f"脚本 {script_name} 已添加")
                
            except Exception as e:
                QMessageBox.warning(self, "添加失败", f"添加脚本时出错: {str(e)}")
    
    def edit_script(self, script: dict):
        """编辑脚本"""
        script_path = script.get('path', '')
        
        # 检查文件是否存在
        if not script_path:
            QMessageBox.warning(self, "错误", "脚本路径为空")
            return
        
        if not os.path.exists(script_path):
            QMessageBox.warning(self, "错误", f"脚本文件不存在: {script_path}")
            # 尝试修复路径 - 可能在产品的通用目录
            product = script.get('product', '通用')
            name = script.get('name', '')
            alt_path = str(self.get_scripts_dir() / product / name)
            if os.path.exists(alt_path):
                script_path = alt_path
                script['path'] = alt_path
            else:
                # 尝试在通用目录找
                alt_path2 = str(self.get_scripts_dir() / '通用' / name)
                if os.path.exists(alt_path2):
                    script_path = alt_path2
                    script['path'] = alt_path2
                else:
                    return
        
        # 读取现有内容
        try:
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法读取脚本文件: {str(e)}")
            content = ""
        
        # 打开编辑对话框
        dialog = ScriptInputDialog(script.get('product', self.current_product), self)
        dialog.set_script(script.get('name', ''), content, script.get('type', ''))
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            script_name = dialog.script_name
            script_content = dialog.script_content
            script_type = dialog.script_type
            
            try:
                # 如果名称改变了，重命名文件
                if script_name != script.get('name', ''):
                    old_path = Path(script_path)
                    new_path = old_path.parent / script_name
                    
                    if new_path.exists() and str(new_path) != script_path:
                        QMessageBox.warning(self, "错误", "文件名已存在")
                        return
                    
                    os.remove(script_path)
                    script_path = new_path
                
                # 写入新内容
                with open(script_path, 'w', encoding='utf-8') as f:
                    f.write(script_content)
                
                # 更新索引
                for i, s in enumerate(self.all_scripts):
                    # 使用path或name+product来匹配
                    if s.get('name') == script.get('name') and s.get('product') == script.get('product'):
                        self.all_scripts[i] = {
                            'name': script_name,
                            'type': script_type,
                            'path': str(script_path),
                            'product': script.get('product', self.current_product),
                            'created_time': s.get('created_time', os.path.getmtime(script_path))
                        }
                        break
                
                self.save_scripts()
                self.apply_filter()
                
                QMessageBox.information(self, "编辑成功", f"脚本 {script_name} 已更新")
                
            except Exception as e:
                QMessageBox.warning(self, "编辑失败", f"编辑脚本时出错: {str(e)}")
    
    def delete_script(self, script: dict):
        """删除脚本"""
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除脚本 {script.get('name', '')} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # 删除文件
                script_path = script.get('path', '')
                if script_path and os.path.exists(script_path):
                    os.remove(script_path)
                
                # 从索引中移除 - 使用name和product匹配
                self.all_scripts = [
                    s for s in self.all_scripts 
                    if not (s.get('name') == script.get('name') and s.get('product') == script.get('product'))
                ]
                self.save_scripts()
                self.apply_filter()
                
                QMessageBox.information(self, "删除成功", "脚本已删除")
                
            except Exception as e:
                QMessageBox.warning(self, "删除失败", f"删除脚本时出错: {str(e)}")


class ScriptInputDialog(QDialog):
    """脚本输入对话框"""
    
    def __init__(self, product: str = "通用", parent=None):
        super().__init__(parent)
        self.product = product
        self.script_name = ""
        self.script_content = ""
        self.script_type = "Shell"
        self.init_ui()
    
    def set_script(self, name: str, content: str, script_type: str):
        """设置脚本内容（用于编辑）"""
        self.name_input.setText(name)
        self.content_edit.setPlainText(content)
        if "Python" in script_type:
            self.type_combo.setCurrentIndex(1)
        else:
            self.type_combo.setCurrentIndex(0)
    
    def init_ui(self):
        self.setWindowTitle("添加脚本")
        self.setWindowIcon(dialog_icon("jiaoben.svg"))
        self.setMinimumSize(750, 550)
        self.setStyleSheet("""
            QDialog {
                background: #f8fafc;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)
        
        # 标题
        title_label = QLabel("添加新脚本")
        title_label.setStyleSheet("""
            font-size: 20px;
            font-weight: bold;
            color: #1f2937;
            padding-bottom: 8px;
        """)
        layout.addWidget(title_label)
        
        # 表单区域
        form_widget = QWidget()
        form_widget.setObjectName("formWidget")
        form_widget.setStyleSheet("""
            QWidget#formWidget {
                background: white;
                border-radius: 12px;
                padding: 20px;
            }
        """)
        form_layout = QVBoxLayout(form_widget)
        form_layout.setSpacing(16)
        
        # 脚本名称和类型
        name_layout = QHBoxLayout()
        name_layout.setSpacing(12)
        
        name_label = QLabel("脚本名称:")
        name_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; min-width: 80px;")
        name_layout.addWidget(name_label)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("例如: server_check.sh")
        self.name_input.setMinimumHeight(40)
        self.name_input.setStyleSheet("""
            QLineEdit {
                background: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #50C9C3;
            }
        """)
        name_layout.addWidget(self.name_input, 1)
        
        type_label = QLabel("类型:")
        type_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; min-width: 50px;")
        name_layout.addWidget(type_label)
        
        self.type_combo = ComboBox()
        self.type_combo.addItems(["Shell 脚本 (.sh)", "Python 脚本 (.py)"])
        self.type_combo.setMinimumHeight(40)
        self.type_combo.setFixedWidth(150)
        self.type_combo.setStyleSheet("""
            QComboBox {
                background: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 0 12px;
                font-size: 14px;
            }
        """)
        name_layout.addWidget(self.type_combo)
        
        form_layout.addLayout(name_layout)
        
        # 产品标签（只读显示）
        product_layout = QHBoxLayout()
        product_label = QLabel("所属产品:")
        product_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151; min-width: 80px;")
        product_layout.addWidget(product_label)
        
        product_value = QLabel(self.product)
        product_value.setStyleSheet("""
            font-size: 14px;
            color: #50C9C3;
            font-weight: bold;
            background: #f0fdf4;
            padding: 8px 16px;
            border-radius: 8px;
            border: 1px solid #50C9C3;
        """)
        product_layout.addWidget(product_value, 1)
        
        form_layout.addLayout(product_layout)
        
        # 脚本内容输入
        content_label = QLabel("脚本内容:")
        content_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #374151;")
        form_layout.addWidget(content_label)
        
        self.content_edit = QTextEdit()
        self.content_edit.setFont(QFont("Consolas", 12))
        self.content_edit.setPlaceholderText("在此输入脚本内容...\n\n例如:\n#!/bin/bash\necho 'Hello World'")
        self.content_edit.setStyleSheet("""
            QTextEdit {
                background: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 12px;
                font-family: Consolas, monospace;
                font-size: 12px;
                line-height: 1.5;
            }
            QTextEdit:focus {
                border-color: #50C9C3;
            }
        """)
        form_layout.addWidget(self.content_edit, 1)
        
        layout.addWidget(form_widget)
        
        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("cancelBtn")
        cancel_btn.setMinimumHeight(44)
        cancel_btn.setMinimumWidth(120)
        cancel_btn.setStyleSheet("""
            QPushButton#cancelBtn {
                background: #f3f4f6;
                color: #374151;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#cancelBtn:hover {
                background: #e5e7eb;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        save_btn = PrimaryPushButton("保存")
        save_btn.setObjectName("saveBtn")
        save_btn.setMinimumHeight(44)
        save_btn.setMinimumWidth(120)
        save_btn.clicked.connect(self.save_and_accept)
        button_layout.addWidget(save_btn)
        
        layout.addLayout(button_layout)
    
    def save_and_accept(self):
        """保存并接受"""
        self.script_name = self.name_input.text().strip()
        self.script_content = self.content_edit.toPlainText().strip()
        self.script_type = self.type_combo.currentText()
        
        if not self.script_name:
            QMessageBox.warning(self, "输入错误", "请输入脚本名称")
            return
        
        if not self.script_content:
            QMessageBox.warning(self, "输入错误", "请输入脚本内容")
            return
        
        # 确保文件扩展名正确
        if self.type_combo.currentIndex() == 0 and not self.script_name.endswith('.sh'):
            self.script_name += '.sh'
        elif self.type_combo.currentIndex() == 1 and not self.script_name.endswith('.py'):
            self.script_name += '.py'
        
        self.accept()
