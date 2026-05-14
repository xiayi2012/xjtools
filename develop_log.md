# AI智能运维助手 - 开发日志

---

## v3.4 (2026-05-10)

### 新增功能

1. **AI排障确认流程**：匹配知识库后先展示FAQ名称和待执行命令，用户确认后再执行排障，未匹配到时输出兜底回复"请联系产品组处理"
2. **排障卡片式消息**：新增匹配信息卡片（蓝色边框）、结论卡片（绿色/橙色边框）、兜底回复卡片（灰色边框），替代原有纯文本消息
3. **自定义确认对话框**：排障确认使用自定义样式对话框，展示问题/原因/命令，蓝色"确认执行"按钮
4. **AI超时时间配置**：AI大模型配置页面新增"超时时间"设置项（QSpinBox，30~600秒，默认120秒），用户可自行调整
5. **AI连接测试后台化**：连接测试改为QThread后台线程执行，不再阻塞UI

### Bug修复

1. **AI排障超时崩溃**：`_evaluate_match`中API超时从硬编码60秒改为读取api_timeout配置，默认120秒
2. **API超时配置不生效**：全项目统一使用api_config中的api_timeout，修复inspection_page/ai_troubleshoot_page中多处硬编码超时
3. **bold_replace输出None**：修复Markdown转HTML时`__text__`匹配的group(2)未处理，输出None的问题
4. **ExportReportWorker不支持本地模型**：添加_call_local_api方法和local/ollama路由分支
5. **AIAnalysisWorker三个API方法无异常处理**：为_call_claude_api/_call_azure_api/_call_local_api添加try/except
6. **save_faqs覆盖其他产品FAQ**：改为读取现有数据、过滤当前产品后合并保存
7. **关键词匹配总返回结果**：_find_best_faq_by_keywords末尾从return best_faq改为return None
8. **self.faqs未初始化**：AIAnalysisPage.__init__中添加self.faqs = []
9. **MessageBox未调用exec()**：两处MessageBox创建后未调用.exec()，对话框不显示
10. **HTML全局转义破坏表格**：删除对整个result_html的HTML转义，避免破坏已生成的表格标签
11. **巡检报告重复统计**：AI提示词禁止输出"检查项统计"章节，代码增加嵌套HTML清理和统计章节删除逻辑
12. **巡检报告截断**：max_tokens从4000增大到16000，添加截断HTML标签自动补全逻辑
13. **巡检报告嵌套HTML**：AI返回完整HTML文档嵌套在主报告中，增加递归剥离嵌套HTML文档结构
14. **os.startfile跨平台**：report_page.py的open_folder添加macOS/Linux支持
15. **AI连接测试UI冻结**：从QTimer.singleShot主线程执行改为QThread后台线程执行

### 优化改进

- **AI提示词重构**：巡检报告提示词改为只输出HTML内容片段（div/section），使用内联style，不再输出完整HTML文档
- **排障结论更明确**：AI排障完成后输出确切结论卡片，符合时显示问题+原因，不符合时提示联系产品组
- **API超时统一化**：inspection_page.py所有Worker使用self._api_timeout，同步方法使用api_config.get('api_timeout')
- **QMessageBox顶层导入**：ai_troubleshoot_page.py将QMessageBox加入顶层导入，避免方法内重复导入

---

## v3.3 (2026-05-10)

### 新增功能

1. **步骤式进度展示**：AI分析时显示步骤式进度弹窗，包含当前步骤名称、进度条、累计用时、停止按钮，替代原有的转圈圈loading动画
2. **停止按钮修复**：停止按钮可真正中断AI分析任务，Worker线程在每个检查项前检查停止标志位，优雅退出并提示已完成项数
3. **AI连接测试**：AI大模型配置页面新增"🔗 连接测试"按钮，发送测试请求验证API配置是否正确，显示成功/失败详情
4. **配置文件便携化**：所有配置文件从 `~/.ssh_inspection/` 迁移至 `{exe所在目录}/config/`，复制文件夹即可在其他电脑使用
5. **关键词+文件名双重匹配**：AI排障搜索同时匹配question字段和keywords字段，question权重50%，keywords权重50%

### Bug修复

1. **API错误信息不明确**：细化API调用错误类型，区分认证失败(401/403)、接口不存在(404)、服务器错误(500)、连接超时、SSL证书错误等
2. **知识库详情显示不全**：修复详情对话框小屏幕显示不全问题，改用整体QScrollArea，去掉各字段maxHeight限制
3. **巡检项状态误判**：修复异常/警告项目实际状态正常的问题，改为从"状态:"行提取状态，避免将命令输出中的关键词误判为异常
4. **系统用户列表格式错误**：修复报告中系统用户列表混入其他信息的问题，AI提示词增加明确的用户列表格式要求
5. **服务进程TOP20截断**：修复进程列表最后一项格式异常的问题，AI提示词增加表格完整性要求
6. **报告排版回退**：修复AI生成内容缺少CSS样式导致排版混乱的问题，添加完整的.card/.info-grid/.stat-card等样式定义
7. **报告包含推测性内容**：修复AI报告中出现"推测"、"估计"等主观判断的问题，提示词明确要求无数据时标注"未获取到相关数据"
8. **报告重复统计概览**：移除报告末尾的"巡检统计概览"章节，避免与顶部统计重复

### 优化改进

- **统计卡片颜色**：检测项目卡片使用紫色渐变背景，与正常/警告/异常卡片有明显区分
- **AI提示词增强**：报告新增系统用户列表、近期登录记录、容器/服务重启检测、资源使用TOP进程（CPU前5/内存前5/服务进程TOP20）
- **AI分析按钮位置**：AI分析按钮移至标题栏左侧醒目位置（蓝色PrimaryPushButton），本地搜索按钮改为灰色普通按钮
- **加密密钥便携化**：加密密钥基于计算机名生成而非用户主目录，确保配置文件复制后可正常解密
- **Windows兼容性**：移除os.chmod调用，统一使用os.path.join路径拼接，所有文件操作使用utf-8编码

### 数据存储变更

- 配置文件：`{exe所在目录}/config/`
  - `connections.json` - SSH连接配置
  - `servers.json` - 服务器列表
  - `faqs.json` - 知识库数据
  - `ai_config.json` - AI模型配置
  - `.key` - 加密密钥
  - `scripts/` - 自定义巡检脚本
  - `reports/` - 历史巡检报告

---

## v3.2 (2026-04-30)

### 新增功能

1. **知识库详情查看**：配置管理界面点击"查看详情"按钮可查看完整的FAQ内容（问题、原因、诊断命令、结果判断、关键词、所属产品）

### Bug修复

1. **知识库commands字段为空**：修复docx导入时AI生成的脚本commands字段为空的问题，增加从content中自动提取命令的备用机制
2. **导入后不刷新显示**：修复导入知识库/脚本后需要重启程序才能显示的问题，添加信号机制自动刷新界面

### 优化改进

- **AI提示词安全确认**：增加安全确认步骤，要求大模型输出前再次检查所有命令，确保不会对客户生产环境造成影响
- **FAQ2SH提示词优化**：放宽命令限制，明确允许的只读查询命令类型

---

## v3.1 (2026-04-28)

### Bug修复

1. **API配置反引号问题**：修复用户复制粘贴时带入反引号导致API调用失败的问题
2. **报告导出崩溃**：修复Markdown转HTML时无限循环导致程序崩溃的问题
3. **报告HTML显示异常**：修复AI返回的HTML内容被错误转义的问题
4. **知识库显示0条**：修复按产品过滤知识库的逻辑问题
5. **知识库覆盖问题**：修复导入知识库时覆盖其他产品FAQ的问题

### 优化改进

- 报告样式优化：添加AI返回内容的样式兼容，报告更美观
- AI提示词优化：明确输出格式要求，减少代码块包裹问题
- 错误处理增强：添加详细日志和异常捕获，便于问题定位
- 打包体积优化：从103MB优化至48MB，减少53%
- UI文本优化：
  - 菜单栏：AI排障 -> AI 排障
  - 菜单栏：AI巡检 -> AI 巡检
  - 菜单栏：AI大模型配置 -> AI 大模型配置
  - 标题栏字体增大至18px
  - AI排障界面：智能问答 -> AI分析结果
  - AI巡检界面：导出巡检报告 -> AI分析并导出

### 技术栈

- GUI框架：PyQt6 + PyQt-Fluent-Widgets
- SSH连接：Paramiko
- 加密：cryptography
- 报告生成：reportlab + Jinja2
- AI集成：OpenAI API兼容接口

### 数据存储

- 配置文件：`~/.ssh_inspection/`
  - `connections.json` - SSH连接配置
  - `servers.json` - 服务器列表
  - `faqs.json` - 知识库数据
  - `ai_config.json` - AI模型配置

---

## v3.0 (2026-04-27)

### 新增功能

- AI巡检功能：支持自动化巡检脚本执行，AI智能分析巡检结果并生成HTML报告
- AI排障功能：基于知识库的智能故障诊断，支持FAQ匹配和命令执行
- AI大模型配置：支持OpenAI兼容API配置，可接入多种大模型
- 多产品支持：办公智盾、准入系统、EDR、零信任四款产品
- SSH连接管理：支持密码/密钥认证，OTP动态密钥认证
- 知识库管理：支持JSON/DOCX/TXT等多种格式导入，按产品分类管理

---

## 项目代码结构

```
xjtools/
├── src/                            # 源代码
│   ├── main.py                     # 程序入口，初始化QApplication和主窗口
│   ├── core/                       # 核心功能模块
│   │   └── ssh_connector.py        # SSH连接器（密码/密钥/OTP认证）
│   ├── scripts/                    # 巡检脚本系统
│   │   ├── loader.py               # 脚本加载器，通用检查项定义
│   │   └── products/               # 产品专用脚本
│   │       ├── office_shield/      # 办公智盾（UES）
│   │       ├── access_control/     # 准入系统
│   │       ├── edr/                # EDR
│   │       └── zero_trust/         # 零信任
│   ├── ui/                         # 用户界面
│   │   ├── main_window.py          # 主窗口（导航栏、子页面管理）
│   │   ├── product_page.py         # 配置管理（服务器增删改查）
│   │   ├── connection_page.py      # SSH连接状态管理
│   │   ├── inspection_page.py      # AI巡检（脚本执行、AI分析、报告导出）
│   │   ├── ai_troubleshoot_page.py # AI排障（FAQ匹配、命令执行、智能问答）
│   │   ├── ai_config_page.py       # AI大模型配置（API类型、Key、URL）
│   │   ├── ai_analysis_page.py     # AI分析对话框
│   │   ├── chat_widget.py          # 聊天气泡组件
│   │   ├── report_page.py          # 历史报告管理
│   │   ├── script_library.py       # 脚本库管理
│   │   └── select_inspection_items_page.py  # 巡检项选择页面
│   ├── utils/                      # 工具函数
│   └── FAQ2SH/                     # FAQ转Shell脚本工具
│       └── faq2sh.py               # AI提示词与转换逻辑
├── config/                         # 配置文件
│   └── ai_config.json              # AI默认配置
├── icons/                          # SVG图标资源
│   ├── AI.svg, xunjian.svg, connect.svg
│   ├── product.svg, jiaoben.svg, baogao.svg
│   └── xunjian2.svg
├── tests/                          # 单元测试
│   └── test_ssh_connector.py
├── dist/                           # 打包输出
│   └── AI智能运维助手.exe          # 可执行文件（48MB）
├── build.spec                      # PyInstaller打包配置
├── requirements.txt                # Python依赖
├── setup.py                        # 安装配置
├── version_info.txt               # 版本信息
├── develop_log.md                  # 开发日志（本文件）
└── README.md                       # 项目说明
```

### 架构分层

| 层级 | 模块 | 职责 |
|------|------|------|
| **表现层** | `ui/` 下所有页面 | 用户交互、数据展示 |
| **业务逻辑层** | `core/`, `scripts/`, `FAQ2SH/` | SSH通信、脚本执行、AI转换 |
| **数据层** | `config/` (exe同目录) | JSON文件持久化存储 |

### 核心数据流

```
用户操作 → UI页面 → 业务处理 → SSH服务器/大模型API → 结果展示
                              ↓
                         JSON文件存储
```
