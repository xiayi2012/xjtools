"""
产品脚本加载器
负责加载不同产品的特定巡检脚本和检查项
"""

import json
from pathlib import Path
from typing import Dict, List, Optional
from .inspection_catalog import get_builtin_checks


class ProductScriptLoader:
    """产品脚本加载器"""
    
    # 产品ID到产品名称的映射
    PRODUCT_NAMES = {
        "office_shield": "办公智盾",
        "access_control": "准入系统",
        "edr": "EDR",
        "zero_trust": "零信任"
    }
    
    # Canonical read-only baseline, also used by the desktop and scheduler.
    COMMON_CHECKS = {}
    for _item in get_builtin_checks():
        COMMON_CHECKS.setdefault(_item['category'], {})[_item['id']] = dict(_item, name=_item['Name'])
    # Vendor commands must be supplied through the script library using real service names.
    PRODUCT_CHECKS = {}

    def __init__(self):
        """初始化加载器"""
        self.scripts_dir = Path(__file__).parent
    
    def get_product_name(self, product_id: str) -> str:
        """获取产品名称"""
        return self.PRODUCT_NAMES.get(product_id, product_id)
    
    def get_common_checks(self) -> Dict:
        """获取通用检查项"""
        return self.COMMON_CHECKS
    
    def get_product_checks(self, product_id: str) -> Dict:
        """获取产品特定检查项"""
        return self.PRODUCT_CHECKS.get(product_id, {})
    
    def get_all_checks_for_product(self, product_id: str) -> Dict:
        """获取指定产品的所有检查项（通用 + 特定）"""
        all_checks = {}
        
        # 添加通用检查项
        for category, checks in self.COMMON_CHECKS.items():
            all_checks[category] = checks.copy()
        
        # 添加产品特定检查项
        product_checks = self.get_product_checks(product_id)
        if product_checks:
            all_checks["product_specific"] = product_checks
        
        return all_checks
    
    def get_checks_as_list(self, product_id: str, selected_keys: Optional[List[str]] = None) -> List[Dict]:
        """
        获取检查项列表（用于执行）
        
        Args:
            product_id: 产品ID
            selected_keys: 选中的检查项键列表，如果为None则返回所有
        
        Returns:
            检查项列表，每个元素包含 name, command, description
        """
        all_checks = self.get_all_checks_for_product(product_id)
        result = []
        
        for category, checks in all_checks.items():
            for key, check_info in checks.items():
                if selected_keys is None or f"{category}.{key}" in selected_keys:
                    result.append({
                        "key": f"{category}.{key}",
                        "name": check_info["name"],
                        "command": check_info["command"],
                        "description": check_info.get("description", ""),
                        "id": check_info.get("id"),
                        "checked": check_info.get("checked", True),
                        "timeout": check_info.get("timeout", 300),
                        "requires_completion": check_info.get("requires_completion", False),
                        "category": category
                    })
        
        return result


# 全局加载器实例
_loader = ProductScriptLoader()


def get_loader() -> ProductScriptLoader:
    """获取全局加载器实例"""
    return _loader
