# 机柜设备管理实施计划

目标：按已确认设计实现机柜可视化、上架和绑定目标设备的运维入口。

技术：Python / PyQt6；原有单 SSH 会话与巡检规则；无新增运行依赖。

## 实施顺序

- [x] 1. `src/core/rack_inventory.py` 与 `tests/test_rack_inventory.py`：先测试区间重叠、边界、推荐空位、历史配置兼容、原子保存与备份，再实现 `placement_error`、`recommend_slot`、`partition_devices`、`InventoryStore`。起始 U 表示底部；1–60U 容量；无位置记录待上架，异常记录待修复；保存失败保留原文件。
- [x] 2. `src/ui/rack_view.py`：实现可滚动机柜、设备面板、详情与机柜编辑弹窗。深灰金属、浅灰铭牌、青绿连接灯；U 编号从底部递增；UI 信号携带设备或机柜 ID。详情展示连接状态和最近巡检，齿轮复用现有设备操作。
- [x] 3. `src/ui/product_page.py` 与 `tests/test_rack_ui.py`：添加三视图、搜索、机房筛选、机柜管理、设备位置表单。设备选择与视图持久化；拒绝冲突、非空柜删除、缩容越界和已连接设备变更连接身份。
- [x] 4. `src/ui/device_operations.py`、`src/ui/main_window.py` 与 `tests/test_device_operation_routing.py`：单会话目标路由、连接请求令牌、任务忙碌拦截、跨设备确认、失败清理与一次性动作；成功后才巡检，排障和日志仅导航。
- [x] 5. `src/ui/inspection_page.py`：冻结任务设备、避免重复启动、设备历史摘要与报告路径。新会话清除上一台设备结果；运行任务期间禁止更换设备。历史索引仅存设备 ID / 时间 / 统计 / 实际报告路径。
- [x] 6. 运行完整 pytest、compileall、diff 检查；使用示例数据进行 1200×800 Qt 截图检查与配置重启测试；保持示例数据和真实配置隔离。补充用户操作文档并启动源码版本。

验证命令：`./venv/Scripts/python.exe -X utf8 -m pytest tests -q`。没有真实 Linux 主机验证时，在交付中说明边界。

执行在当前会话完成，复用现有工作区，不另建线程或重复请求已确认的设计批准。

## 验证结果

- 92 项 pytest 测试通过；compileall 与 git diff --check 通过。
- 使用隔离的示例数据检查 12U / 42U、2U 面板、长机柜滚动与详情操作；未写入真实设备示例。
- 模拟 SSH 完成连接回调、目标校验、巡检线程、历史摘要与原始报告导出流程。
- 独立审查发现的连接中资产修改、旧异常逐条修复、旧冲突绕过缩容检查均已补测试修复。
- 已重新启动源码版，窗口标题正确且进程响应正常；本次未连接真实 Linux 主机。
