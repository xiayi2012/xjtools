from scripts.inspection_catalog import get_builtin_checks
"""
选择巡检项页面模块
实现巡检项和自定义脚本的选择功能
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QAbstractItemView, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush
import json
import os
from pathlib import Path

from utils import get_config_dir as _get_config_dir

from scripts.loader import get_loader


class SelectInspectionItemsPage(QWidget):
    """选择巡检项页面"""
    
    # 信号定义
    proceed_to_inspection = pyqtSignal(dict)  # 继续到巡检信号
    
    def __init__(self):
        """初始化选择巡检项页面"""
        super().__init__()
        self.custom_scripts = []
        self.product_scripts = []  # 产品特定检查项（如 UES 基线检查）
        self.current_product = ""  # 当前产品名称
        self.current_product_id = ""  # 当前产品ID
        self._all_items = []  # 所有巡检项 {name, command, checked}
        self.init_ui()
        self._populate_inspect_list()
    
    def set_product(self, product_name: str, product_id: str = ""):
        """设置当前产品（从主窗口传入）"""
        self.current_product = product_name
        self.current_product_id = product_id
        
        # 更新 UI
        if hasattr(self, 'product_label'):
            self.product_label.setText(f"当前产品: {product_name}")
        
        # 加载产品特定检查项（如 UES 基线检查）
        self._load_product_scripts()
        
        # 加载该产品的自定义脚本
        self._load_custom_scripts_for_product()
    
    def _load_product_scripts(self):
        """从 loader.py 加载产品特定检查项"""
        self.product_scripts = []
        
        if not self.current_product_id:
            # 尝试从产品名称映射到 ID
            loader = get_loader()
            for pid, name in loader.PRODUCT_NAMES.items():
                if name == self.current_product:
                    self.current_product_id = pid
                    break
        
        if self.current_product_id:
            loader = get_loader()
            checks = loader.get_product_checks(self.current_product_id)
            
            for check_key, check_info in checks.items():
                self.product_scripts.append({
                    "name": check_info.get("name", check_key),
                    "command": check_info.get("command", ""),
                    "description": check_info.get("description", ""),
                    "category": "产品脚本",
                    "is_product_script": True,
                    "script_id": check_key
                })
        
        # 更新列表显示
        self._update_product_scripts_ui()
    
    def _load_custom_scripts_for_product(self):
        """从 script_library 存储加载该产品的自定义脚本"""
        scripts_dir = Path(_get_config_dir()) / "scripts"
        index_file = scripts_dir / "index.json"
        
        loaded_scripts = []
        if index_file.exists():
            try:
                with open(index_file, 'r', encoding='utf-8') as f:
                    all_scripts = json.load(f)
                
                # 筛选当前产品的脚本
                for script in all_scripts:
                    script_product = script.get('product', '通用')
                    # 匹配：产品名 或 产品ID
                    if script_product == self.current_product or script_product == self.current_product_id:
                        loaded_scripts.append(script)
            except Exception as e:
                print(f"加载自定义脚本失败: {e}")
        
        # 设置自定义脚本（会调用 _update_custom_scripts_ui）
        self.set_custom_scripts(loaded_scripts)
    
    def init_ui(self):
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("选择巡检项")
        title_label.setObjectName("pageTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        
        # 提示信息
        self.hint_label = QLabel("勾选需要执行的巡检项目和各产品专用检查脚本（可拖动排序）")
        self.hint_label.setObjectName("hintLabel")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)
        
        # 产品标签
        self.product_label = QLabel("当前产品: 未选择")
        self.product_label.setStyleSheet("font-size: 14px; font-weight: bold; color: #50C9C3; background: #F0FAFA; padding: 8px; border-radius: 4px;")
        self.product_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.product_label)
        
        # 全局批量操作按钮
        global_btn_layout = QHBoxLayout()
        global_btn_layout.setSpacing(10)
        
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setObjectName("selectAllGlobalBtn")
        self.select_all_btn.setMinimumHeight(40)
        self.select_all_btn.setMinimumWidth(100)
        self.select_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.select_all_btn.clicked.connect(self._select_all)
        global_btn_layout.addWidget(self.select_all_btn)
        
        self.deselect_all_btn = QPushButton("取消全选")
        self.deselect_all_btn.setObjectName("deselectAllGlobalBtn")
        self.deselect_all_btn.setMinimumHeight(40)
        self.deselect_all_btn.setMinimumWidth(100)
        self.deselect_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.deselect_all_btn.clicked.connect(self._deselect_all)
        global_btn_layout.addWidget(self.deselect_all_btn)
        
        global_btn_layout.addStretch()
        layout.addLayout(global_btn_layout)
        
        # 巡检项列表（可拖动排序）
        self.inspect_list = QListWidget()
        self.inspect_list.setObjectName("inspectList")
        self.inspect_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.inspect_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.inspect_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.inspect_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.inspect_list, 1)
        
        # 底部按钮
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(15)
        
        bottom_layout.addStretch()
        
        self.proceed_btn = QPushButton("下一步")
        self.proceed_btn.setObjectName("proceedButton")
        self.proceed_btn.setMinimumHeight(50)
        self.proceed_btn.setMinimumWidth(140)
        self.proceed_btn.clicked.connect(self.proceed_to_execution)
        bottom_layout.addWidget(self.proceed_btn)
        
        layout.addLayout(bottom_layout)
        
        # 应用样式
        self.apply_styles()
    
    def _populate_inspect_list(self):
        """填充巡检项列表"""
        self._all_items = [dict(item, name=item['Name']) for item in get_builtin_checks()]

        for item_data in self._all_items:
            item = QListWidgetItem(f"{item_data['name']} [{item_data['category']}]")
            item.setData(Qt.ItemDataRole.UserRole, item_data)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if item_data['checked'] else Qt.CheckState.Unchecked)
            item.setForeground(QBrush(QColor("#333333")))
            self.inspect_list.addItem(item)
    
    def _on_item_changed(self, item: QListWidgetItem):
        """列表项状态改变"""
        item_data = item.data(Qt.ItemDataRole.UserRole)
        if item_data:
            item_data['checked'] = (item.checkState() == Qt.CheckState.Checked)
            # 更新显示文本（使用原生复选框，不再需要emoji）
            name = item_data['name']
            category = item_data['category']
            item.setText(f"{name} [{category}]")
    
    def _select_all(self):
        """全局全选"""
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item.setCheckState(Qt.CheckState.Checked)
    
    def _deselect_all(self):
        """全局取消全选"""
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)
    
    def set_custom_scripts(self, scripts: list):
        """设置自定义脚本（从自定义脚本页面传入）"""
        self.custom_scripts = scripts or []
        self._update_custom_scripts_ui()
    
    def _update_custom_scripts_ui(self):
        """更新自定义脚本 UI - 使用批量更新避免UI卡顿"""
        # 阻止重复快速调用
        if not hasattr(self, '_custom_scripts_update_in_progress'):
            self._custom_scripts_update_in_progress = False
        if self._custom_scripts_update_in_progress:
            return
        self._custom_scripts_update_in_progress = True
        
        try:
            # 收集需要保留的非自定义脚本项数据
            preserved_items = []
            for i in range(self.inspect_list.count()):
                item = self.inspect_list.item(i)
                item_data = item.data(Qt.ItemDataRole.UserRole)
                if not item_data or not item_data.get('is_custom_script'):
                    preserved_items.append({
                        'checked': item.checkState() == Qt.CheckState.Checked,
                        'data': item_data
                    })
            
            # 清除所有项
            self.inspect_list.blockSignals(True)  # 阻止itemChanged信号
            self.inspect_list.clear()
            
            # 恢复保留的项
            for item_info in preserved_items:
                item_data = item_info['data']
                name = item_data.get('name', '未知')
                category = item_data.get('category', '')
                item = QListWidgetItem(f"{name} [{category}]")
                item.setData(Qt.ItemDataRole.UserRole, item_data)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                item.setCheckState(Qt.CheckState.Checked if item_info['checked'] else Qt.CheckState.Unchecked)
                item.setForeground(QBrush(QColor("#333333")))
                self.inspect_list.addItem(item)
            
            # 添加自定义脚本项
            for script in self.custom_scripts:
                name = script.get('name', '未知脚本')
                item_data = {
                    "name": name,
                    "command": "",  # 自定义脚本命令在 get_selected_custom_scripts 中处理
                    "checked": True,
                    "category": "自定义",
                    "is_custom_script": True,
                    "script_content": script.get('content', ''),
                    "script_name": name
                }
                item = QListWidgetItem(f"{name} [自定义]")
                item.setData(Qt.ItemDataRole.UserRole, item_data)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                item.setCheckState(Qt.CheckState.Checked)
                item.setForeground(QBrush(QColor("#333333")))
                self.inspect_list.addItem(item)
            
            self.inspect_list.blockSignals(False)
        finally:
            self._custom_scripts_update_in_progress = False
    
    def _update_product_scripts_ui(self):
        """更新产品特定检查项 UI（如 UES 基线检查）"""
        # 阻止重复快速调用
        if not hasattr(self, '_product_scripts_update_in_progress'):
            self._product_scripts_update_in_progress = False
        if self._product_scripts_update_in_progress:
            return
        self._product_scripts_update_in_progress = True
        
        try:
            # 收集需要保留的项数据（保留非产品脚本项）
            preserved_items = []
            for i in range(self.inspect_list.count()):
                item = self.inspect_list.item(i)
                item_data = item.data(Qt.ItemDataRole.UserRole)
                if not item_data or not item_data.get('is_product_script'):
                    preserved_items.append({
                        'checked': item.checkState() == Qt.CheckState.Checked,
                        'data': item_data
                    })
            
            # 清除所有项
            self.inspect_list.blockSignals(True)
            self.inspect_list.clear()
            
            # 恢复保留的项
            for item_info in preserved_items:
                item_data = item_info['data']
                if not item_data:
                    continue
                name = item_data.get('name', '未知')
                category = item_data.get('category', '')
                item = QListWidgetItem(f"{name} [{category}]")
                item.setData(Qt.ItemDataRole.UserRole, item_data)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                item.setCheckState(Qt.CheckState.Checked if item_info['checked'] else Qt.CheckState.Unchecked)
                item.setForeground(QBrush(QColor("#333333")))
                self.inspect_list.addItem(item)
            
            # 添加产品特定检查项（如 UES 基线检查）
            for script in self.product_scripts:
                name = script.get('name', '未知脚本')
                item_data = {
                    "name": name,
                    "command": script.get('command', ''),
                    "checked": True,
                    "category": "产品脚本",
                    "is_product_script": True,
                    "script_id": script.get('script_id', '')
                }
                item = QListWidgetItem(f"{name} [产品脚本]")
                item.setData(Qt.ItemDataRole.UserRole, item_data)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                item.setCheckState(Qt.CheckState.Checked)
                item.setForeground(QBrush(QColor("#50C9C3")))  # 使用主题色
                self.inspect_list.addItem(item)
            
            # 添加自定义脚本项
            for script in self.custom_scripts:
                name = script.get('name', '未知脚本')
                item_data = {
                    "name": name,
                    "command": "",  # 自定义脚本命令在 get_selected_custom_scripts 中处理
                    "checked": True,
                    "category": "自定义",
                    "is_custom_script": True,
                    "script_content": script.get('content', ''),
                    "script_name": name
                }
                item = QListWidgetItem(f"{name} [自定义]")
                item.setData(Qt.ItemDataRole.UserRole, item_data)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
                item.setCheckState(Qt.CheckState.Checked)
                item.setForeground(QBrush(QColor("#333333")))
                self.inspect_list.addItem(item)
            
            self.inspect_list.blockSignals(False)
        finally:
            self._product_scripts_update_in_progress = False
    
    def get_selected_checks(self) -> list:
        """获取选中的检查项（按列表顺序）"""
        scripts = []
        
        # 办公智盾需要排除的检查项
        office_shield_exclude = ["系统用户列表"]
        
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            
            if not item_data:
                continue
            
            # 跳过自定义脚本（它们通过 get_selected_custom_scripts 处理）
            if item_data.get('is_custom_script'):
                continue
            
            # 跳过产品脚本（它们通过 get_selected_product_scripts 处理）
            if item_data.get('is_product_script'):
                continue
            
            item_name = item_data.get('name', '')
            
            # 办公智盾产品，排除用户列表相关检查项
            if self.current_product_id == "office_shield" and item_name in office_shield_exclude:
                continue
            
            # 直接从 Qt 读取勾选状态，避免 item_data 引用问题
            is_checked = item.checkState() == Qt.CheckState.Checked
            if is_checked:
                scripts.append({
                    "name": item_data['name'],
                    "command": item_data['command'],
                    "id": item_data.get('id'),
                    "timeout": item_data.get('timeout', 300),
                    "requires_completion": item_data.get('requires_completion', False)
                })
        
        return scripts
    
    def get_selected_custom_scripts(self) -> list:
        """获取选中的自定义脚本"""
        import base64
        selected = []
        
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            
            if not item_data or not item_data.get('is_custom_script'):
                continue
            
            # 直接从 Qt 读取勾选状态，避免 item_data 引用问题
            if item.checkState() != Qt.CheckState.Checked:
                continue
            
            script_content = item_data.get('script_content', '')
            script_name = item_data.get('script_name', '')
            
            # 使用 base64 编码避免引号和特殊字符问题
            encoded_content = base64.b64encode(script_content.encode('utf-8')).decode('utf-8')
            
            if script_name.endswith('.py'):
                # Python 脚本：解码后通过 python3 执行
                command = f"echo '{encoded_content}' | base64 -d | python3"
            else:
                # Shell 脚本：解码后通过 bash 执行
                command = f"echo '{encoded_content}' | base64 -d | bash"
            
            selected.append({"name": script_name, "command": command, "content": script_content})
        
        return selected
    
    def get_selected_product_scripts(self) -> list:
        """获取选中的产品特定检查项（如 UES 基线检查）"""
        selected = []
        
        for i in range(self.inspect_list.count()):
            item = self.inspect_list.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            
            if not item_data or not item_data.get('is_product_script'):
                continue
            
            # 直接从 Qt 读取勾选状态
            if item.checkState() != Qt.CheckState.Checked:
                continue
            
            script_name = item_data.get('name', '')
            script_id = item_data.get('script_id', '')
            command = item_data.get('command', '')
            
            # 如果命令为空，尝试从 product_scripts 中获取
            if not command:
                for ps in self.product_scripts:
                    if ps.get('script_id') == script_id or ps.get('name') == script_name:
                        command = ps.get('command', '')
                        break
            
            selected.append({
                "name": script_name,
                "command": command,
                "_type": "product_script",
                "script_id": script_id
            })
        
        return selected
    
    def proceed_to_execution(self):
        """继续到巡检执行页面"""
        inspection_config = {
            "scripts": self.get_selected_checks(),
            "custom_scripts": self.get_selected_custom_scripts(),
            "product_scripts": self.get_selected_product_scripts()
        }
        self.proceed_to_inspection.emit(inspection_config)
    
    def apply_styles(self):
        """应用样式"""
        self.setStyleSheet("""
            /* 全局全选/取消按钮 */
            QPushButton#selectAllGlobalBtn {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#selectAllGlobalBtn:hover {
                background: #3BA8A2;
            }
            QPushButton#selectAllGlobalBtn:pressed {
                background: #2D8A85;
            }
            
            QPushButton#deselectAllGlobalBtn {
                background: #5B5B5B;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 8px 16px;
            }
            QPushButton#deselectAllGlobalBtn:hover {
                background: #3D3D3D;
            }
            QPushButton#deselectAllGlobalBtn:pressed {
                background: #2D2D2D;
            }
            
            /* 页面标题 */
            QLabel#pageTitle {
                font-size: 28px;
                font-weight: bold;
                color: #50C9C3;
                padding: 10px;
            }
            
            QLabel#hintLabel {
                font-size: 14px;
                color: #5B5B5B;
                padding: 5px;
                margin-bottom: 10px;
            }
            
            /* 巡检项列表 */
            QListWidget#inspectList {
                border: 1px solid #E1E1E1;
                border-radius: 8px;
                background: white;
                padding: 5px;
                outline: none;
            }
            QListWidget#inspectList::item {
                padding: 10px 8px 10px 35px;
                border: 1px solid #E0E0E0;
                border-radius: 6px;
                font-size: 13px;
                color: #333333;
                background: white;
                margin: 4px 2px;
            }
            QListWidget#inspectList::item:hover {
                background: #B2DFDB;
                color: #333333;
            }
            QListWidget#inspectList::item:selected {
                background: #80CBC4;
                color: #333333;
            }
            QListWidget#inspectList::item:checked {
                background: white;
                color: #333333;
            }
            QListWidget#inspectList::item:unchecked {
                background: #FAFAFA;
                color: #999999;
            }
            
            /* 列表项复选框指示器 */
            QListWidget#inspectList::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 2px solid #A0A0A0;
                background: white;
                margin-left: 5px;
            }
            QListWidget#inspectList::indicator:checked {
                border: 2px solid #50C9C3;
                background: #50C9C3;
                image: none;
            }
            QListWidget#inspectList::indicator:unchecked {
                border: 2px solid #A0A0A0;
                background: white;
            }
            QListWidget#inspectList::indicator:hover {
                border-color: #50C9C3;
            }
            
            /* 滚动条 */
            QListWidget#inspectList QScrollBar:vertical {
                border: none;
                background: #F0F0F0;
                width: 8px;
                margin: 0;
            }
            QListWidget#inspectList QScrollBar::handle {
                background: #C0C0C0;
                border-radius: 4px;
                min-height: 20px;
            }
            QListWidget#inspectList QScrollBar::handle:hover {
                background: #A0A0A0;
            }
            
            /* 下一步按钮 */
            QPushButton#proceedButton {
                background: #50C9C3;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 14px;
                font-weight: bold;
                padding: 12px 30px;
                min-width: 140px;
            }
            QPushButton#proceedButton:hover {
                background: #3BA8A2;
            }
        """)
