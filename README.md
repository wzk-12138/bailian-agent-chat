# bailian-agent-chat

**阿里云百炼智能体的本地 Streamlit 问答界面** —— 建好智能体，填几个 ID，就能在浏览器里用一个干净的输入框跟它对话。

它不是一个「聊天模板」，而是一份**踩坑记录**：接入百炼 Managed Agent（agentstudio）时会遇到两个
文档里没写、但一定会卡住你的问题，这个仓库存了可直接复用的解法。

- 🔌 **接口是手写的**：不依赖官方 SDK —— 因为官方 `dashscope.agentstudio` SDK 在部分 Python/OpenSSL
  组合下**根本调不通**这个接口（原因见「坑一」）
- 🎯 **两个界面**：`app.py` 是纯问答界面，`app_full.py` 是带调试能力的完整台
- ✅ **所有结论都实测过**，不是照抄文档

想直接跑起来？跳到 [快速开始](#快速开始)。

```
bailian-agent/
├── app.py            # ⭐ 精简问答界面：只有「输入问题 → 给出回答」（默认使用）
├── app_full.py       # 完整版：双接入模式 + 设计思想对照 + 连通性自检（留档）
├── requirements.txt
├── .env.example      # 复制为 .env 后填入密钥
├── LICENSE
└── README.md
```

---

## 快速开始

```bash
git clone https://github.com/wzk-12138/bailian-agent-chat.git
cd bailian-agent-chat
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# 编辑 .env，填入下面的值
streamlit run app.py
```

两个界面共用同一份 `.env`：

```bash
streamlit run app.py          # 精简问答（默认）
streamlit run app_full.py     # 完整版调试台
```

`.env` 需要的值：

| 变量 | 从哪儿拿 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼控制台 → 密钥管理 |
| `MA_WORKSPACE_ID` | 见「坑二」，**别靠肉眼抄** |
| `MA_AGENT_ID` | Managed Agent → 智能体 → 详情页，形如 `agent_01M3PH58YTYAX...` |
| `MA_ENV_ID` | 可留空；留空时程序自动创建一个并复用 |

### 精简版长什么样

- 只有标题、一个输入框、回答区域 —— 没有侧边栏、没有页签、不需要填任何配置
- 提问时先显示「正在理解你的问题…」，调工具时显示「正在调用 web_search …」
- 回答下方一行小字：耗时 + token 用量
- 可折叠的「执行过程」，看得到它思考了什么、调了哪个工具
- 底部一个「清空对话」按钮

---

## 两个坑（本文档存在的意义）

### 坑一：下划线域名会让 Python 的 TLS 校验直接失败

如果 workspace ID 里带下划线，域名就是 `ws_xxx.cn-beijing.maas.aliyuncs.com`。
**同一台机器上**：

| 调用方式 | 结果 |
|---|---|
| curl | ✅ 正常 |
| Python `requests` | ❌ `CERTIFICATE_VERIFY_FAILED: Hostname mismatch` |
| 官方 `dashscope.agentstudio` SDK | ❌ 同样失败（内部走 httpx） |

原因是 Python 3.13 + OpenSSL 3.5 在做证书主机名校验时，把含下划线的主机名判为非法。

解法（见 `app.py` 的 `_Adapter` / `_san_match`）：**证书链照旧用系统 CA 严格校验**，
只把主机名匹配换成自己按 SAN 核对 —— 安全强度不低于默认行为。

### 坑二：Workspace ID 是连字符，不是下划线

控制台截图里很容易看成 `ws_a1b2c3d4e5f6`，真实值其实是 **`ws-a1b2c3d4e5f6`**（连字符）。
短横线和下划线在截图里几乎无法区分，`1`/`l`、`0`/`o` 同理。用错会得到：

```
400 {"code":"BadRequest.IllegalEndpoint","message":"Workspace endpoint is invalid."}
```

**这个错误对伪造的 ID 返回一模一样**，所以别拿它判断对错。取权威值只需 API Key：

```bash
npm install -g bailian-cli          # 需要 Node ≥ 18.17
bl auth login --api-key sk-xxx
bl quota list --output json | findstr workspace_id
```

`quota list` 会返回当前 API Key 所属工作空间的权威 ID，比看截图可靠得多。
（`bl app list` / `bl workspace list` 需要 `bl auth login --console` 浏览器登录后才能用。）

---

## Managed Agent 五步链路

### 五步调用链

```
1) Environment（复用，不必每次建）  POST /environments
2) Session（每次任务新建）           POST /sessions        {agent, environment_id}
3) 发事件                            POST /sessions/{sid}/events
4) 订阅 SSE 收结果                   GET  /sessions/{sid}/events/stream
```

端点：`https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio`

### 事件类型与字段

问「你好，请用一句话介绍你自己」→ 2.7 秒返回；
问「帮我在网上搜一下今天北京的天气」→ Agent **自动调用 `web_search`** 并给出带数据来源的完整回答。

实测的事件序列：

```
model_request_start → reasoning → model_request_end
                    → tool_call → tool_call_output     （有工具调用时）
                    → model_request_start → reasoning → model_request_end
                    → message (role=assistant)          ← 最终答复在这里
                    → session_status (idle)
```

| 要拿的东西 | 位置 |
|---|---|
| 最终答复 | `type=="message"` 且 `role=="assistant"` → `content[].text` |
| 思考过程 | `type=="reasoning"` → `content[].text` |
| 调了哪个工具 | `type=="tool_call"` → `content[].data.name` |
| token 用量 | `type=="model_request_end"` → `data.input_tokens / output_tokens` |
| 何时结束 | `type=="session_status"` → `content[].data.session_status in (idle, terminated)` |

### 两个容易踩的细节

**SSE 是多行格式**：真实流按标准 SSE 分行（`id:` / `event:` / `data:`，中间夹 `:keepalive`
注释行），只累加 `data:` 行、忽略注释行即可。`GET /sessions/{id}/events` 可作为兜底取权威结果。

**工具会自动执行**：控制台里工具标着「总需允许」，但通过 API 发起的会话里工具**会自动执行**，
不需要客户端回传审批 —— 所以客户端不用实现审批交互。（本地页面里那个可折叠的「执行过程」，
展示的是执行记录，不是审批入口。）

---

## 两种智能体对照（背景）

百炼现在有两套完全不同的智能体体系，**创建入口、ID 形态、调用接口都不一样**，别混用：

| | 百炼应用（智能体 1.0 / FlowAgent） | Managed Agent（新版平台） |
|---|---|---|
| 创建入口 | 应用管理 → 创建应用 → **智能体应用（Agent 1.0）** | 顶栏 **Managed Agent** → 智能体 → **创建智能体** |
| 编排方式 | 画布固定拓扑，确定性工作流 | 模型自主规划 + 内置工具 + 云沙箱 |
| ID 形态 | 16 位数字，如 `1941890680279287` | `agent_xxxxxxxx` |
| 调用地址 | `https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion` | `https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio` |
| 调用模型 | 一问一答，`session_id` 托管多轮 | 五步：Environment → Session → Event → 订阅 SSE |
| 发布 | 右上角「发布」（不发布调用报 **403 App.AccessDenied**） | 创建即在「活跃」列表，配好 Environment 即可调用 |
| 需要的凭据 | API Key + APP_ID | API Key + **Workspace ID** + Agent ID |

> 补充：`Flow Agent` 是百炼对整套低代码应用开发能力的品牌名（零代码智能体 / 可视化工作流 / 高代码应用），
> 落到具体应用上就是 **Agent 1.0**。2025-08-12「智能体编排应用」下线，2025-12-26 上线 Agent 2.0。

**本仓库默认走 Managed Agent**（`app.py`）。另一条路线见文末附录。

---

## 这个智能体的设计符合什么思想

> 依据：`bojieli/ai-agent-book`，全书围绕 **Agent = LLM + 上下文 + 工具** 展开，
> 生产形态展开为 **Agent = Model + Harness**，
> **Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正**。
> 下面前两条偏 FlowAgent 的编排视角，其余几条两条路线都适用。

### 1. 三要素齐全：大脑 / 眼睛 / 手脚

| 书的公式 | 智能体里的对应物 |
|---|---|
| **LLM（大脑）** | 大模型节点 / 模型配置 |
| **上下文（眼睛）** | 系统提示词、知识库检索结果、变量、长期记忆、对话历史 |
| **工具（手脚）** | 插件、MCP 工具、代码沙箱、联网检索、API 节点 |

书中强调：**扩展眼睛和手脚是最主要的能力杠杆**。模型固定时，重新定义它能看到什么、能做什么，
往往直接把不可解的任务变成可解——这正是我们给智能体挂知识库和插件的理由。

### 2. 工作流模式：确定性编排

书中把编排分成两级：

- **工作流**：执行路径由代码（画布）预先定义，LLM 只在节点内部理解与生成。
  优势是**严格流程控制**（「先检索后回答」「未命中才联网」被强制执行）与
  **安全性**（提示注入最多影响单个节点，攻击面被关在节点内）；局限是**缺乏变通**。
- **自主 Agent**：路径由模型依据环境反馈实时决定，即 ReAct 循环，适合开放式问题，
  但成本更高、复合错误风险更大。

书里给的推进顺序是 **「先提示词 → 再工作流 → 最后自主 Agent」**。
FlowAgent 属于前者；Managed Agent 属于后者——它自己决定什么时候联网、什么时候跑代码。

### 3. Graph 工程：节点 + 边 + 可检查点状态

书中记录了 2026 年出现的新范式 **Graph 工程**：把 Agent 循环、确定性程序和人工审批
组织成显式执行图——**节点承担能力，边规定路由与依赖，结构化状态沿边传递并在关键边界处持久化**。

FlowAgent 的画布就是这张图。而工程范式是层层包含的：
提示工程 ⊂ 上下文工程 ⊂ Harness 工程 ⊂ Loop 工程 ⊂ Graph 工程，
单个 Agent 循环正是执行图中的一个节点。

### 4. Harness 被平台托管：约束 + 验证 + 纠正

书中断言：**「行业正在从『能做事』向『可靠地做事』转变，Harness 是 Agent 系统的核心竞争力」**，
并以 Claude Code 为例说明——绝大部分代码是约束、验证与纠正，而不是工具本身。

| Harness 五要素 | 百炼上的落点 |
|---|---|
| 上下文管理 | 提示词、知识库、变量、上下文压缩 |
| 工具接口 | 插件 / MCP / 代码沙箱 |
| 约束 | 工具权限、沙箱隔离、Vault 密钥管理 |
| 验证 | 结构化校验、输出检查、运行日志与 Trace 链路 |
| 纠正 | 断点续跑、失败重试、人工接管 |

结论：**平台就是 Harness**。Managed Agent 把这一点做得更彻底——连 Session 与 Environment
都由平台托管，本地只负责「发一句、收一串」。

### 5. ACI 与防呆：把工具接口设计对

Anthropic 的三原则之一是 **设计好 ACI（Agent-Computer Interface）**——从 Agent 视角而非
程序员视角设计接口：命名直观、参数带例子、边界写清楚，用设计让错误**无法发生**（防呆 / Poka-yoke）。

落到成品：**每个插件、函数节点、内置工具（bash / read / write / glob / grep）的描述文字就是 ACI**。
模型与工具之间唯一的沟通通道就是接口本身，模糊的描述会被模型放大成系统性错误。

### 6. 渐进式披露：别把上下文一次塞满

书中定义：不把全部信息一次放进上下文，**先给可检索的目录，再按需加载细节**，
同时优化上下文预算与选择精度。

对应做法：知识库用检索节点**按需召回**；工具数量多时做分层与按需发现，
只把当前分支需要的工具暴露给模型（对应书中「工具太多怎么办」一节）。
Managed Agent 的 Skill 机制本身就是「元数据常驻、正文按需加载」。

### 7. 三层护栏：上下文层 / 执行层 / 数据层

按**被绕过的难度**排序，越靠下越不依赖模型自己的判断：

- **上下文层**（管它看到什么）：意图分类拦截跑题、越狱与提示注入；指令与数据分离
- **执行层**（管它能做什么）：工具风险评级、最小权限凭证、沙箱隔离、**人在回路**；输出侧 PII 过滤
- **数据层**（管世界最终被改成什么样）：知识库与业务库权限校验，上层被绕过时越权仍在数据层被拒

还有一条容易漏的纪律：护栏要同时测「该拦的是否拦住」**和**「该放行的是否放行」，
否则会陷入**误拒绝**。

### 8. 提议者—审核者：别让同一个 Agent 自审

全书反复使用的设计模式：**产出与评判由两个不共享上下文的角色分别承担**，
评判方只看产物本身（渲染结果、测试输出、结构化参数），不看产出方的推理过程。
前提是**自审不可靠**——同一上下文里的模型很难发现自己的盲区。

落地方式：在流程末端加一个**独立审核节点**；对高风险分支挂**人工确认节点**做熔断。

### 9. 本页自己的角色：Environment 与人在回路

按书中的边界划分，**Harness 是「Agent 边界内、模型之外」的运行与治理层**，
用户、外部系统、数据库属于 Environment。

这个 Streamlit 页面不生产智能，它承担的是**观察与动作空间的延伸**：
把用户输入送进 Agent，把 Trace 和结果呈现给人，必要时让人接管——
对应书中的 **人工干预（Human in the loop）** 与 **保持透明** 原则。

### 10. 一条反向约束：Less structure, more intelligence

书里引用 Manus 的主张提醒不要过度设计：**好的结构不是替 Agent 预演全部思考，
而是守住边界，把边界内的决策空间还给模型**。

反面例子：一上来就画十几个节点的流水线，每个部件单看都有道理，组合起来却低效不可靠——
因为固定拓扑遇到新例外就继续加节点，越来越复杂、通用性却越来越差，
模型本来能根据上下文处理的语义判断反而被工作流写死了。

**实操建议：分支控制在 5～7 个以内，只在评估反复暴露某类稳定失败时，才把该步骤固化成节点。**

---

## 附录：另一条路线 —— 百炼应用（Agent 1.0 / FlowAgent）

> 本仓库默认不用这条路线，保留作参考。创建应用只能在控制台完成（需要你的阿里云账号登录）。

**第 1 步：开通百炼 + 创建 API Key**

1. 打开 <https://bailian.console.aliyun.com/>，用阿里云账号登录并开通服务。
   地域选 **华北 2（北京）**——新人开通会发放免费 Token 额度。
2. 右上角头像 → **API-KEY 管理** → **创建 API Key**，复制保存（形如 `sk-xxxxxx`，只显示一次）。

**第 2 步：新建智能体**

左侧 **应用管理** → **新增应用** → 选择 **智能体应用（Agent 1.0）**。
1.0 是流程编排型（拓扑在画布上定义），2.0 是自主规划型。

**第 3 步：编排流程（推荐拓扑）**

```
开始
 └─ 意图分类（LLM 节点）
      ├─ 知识问答 → 知识库检索节点 ─┐
      ├─ 时事信息 → 联网搜索插件 ──┤
      ├─ 计算处理 → 代码沙箱节点 ──┼─→ 结果汇总（LLM）→ 结束
      ├─ 内容生成 → 生成节点 ─────┤
      └─ 兜底对话 → 通用对话节点 ─┘
```

| 配置项 | 建议 |
|---|---|
| 模型 | 选**带思考（Reasoning）能力**的模型，如 `qwen-plus` / `qwen-max` |
| 意图分类节点 | 输出**结构化类别**（JSON），不要自由发挥，这样路由才是确定的 |
| 知识库 | 按需检索，不要把整库塞进提示词 |
| 插件 / MCP | 描述写清楚「什么时候用 / 什么时候别用 / 参数示例」 |
| 结束节点 | 确认**每个分支都汇入结束节点**，否则会出现返回空文本 |

**第 4 步：发布并拿到 APP_ID**

右上角 **发布**（不发布调用会报 `403 App.AccessDenied`）→ 回应用管理复制 **APP_ID**。
调用地址：`POST https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion`

```bash
curl -X POST https://dashscope.aliyuncs.com/api/v1/apps/$APP_ID/completion \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
  -H "Content-Type: application/json" \
  -H "X-DashScope-SSE: enable" \
  -d '{"input":{"prompt":"你好"},"parameters":{"incremental_output":true}}'
```

`app_full.py` 支持这条路线的调用（侧边栏切换接入方式，填 APP_ID 即可）。

---

## 参考

- 书仓库：<https://github.com/bojieli/ai-agent-book>　在线阅读：<https://bojieli.github.io/ai-agent-book>
- Managed Agents API：<https://help.aliyun.com/zh/model-studio/managed-agents-api-overview>
- 应用调用 API（Agent 1.0）：<https://help.aliyun.com/document_detail/3003869.html>
- 百炼控制台：<https://bailian.console.aliyun.com/>
