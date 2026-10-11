# bailian-agent-chat

**阿里云百炼智能体的本地 Streamlit 问答界面。**

配好三个值，打开浏览器就是一个输入框 —— 问一句，答一句。没有侧边栏、没有页签、不需要在界面上填任何配置。

界面背后有两个文档里没写、但一定会卡住你的坑（下划线域名搞崩 Python 的 TLS 校验、
Workspace ID 是连字符不是下划线），本文档附带可直接复用的解法。

---

## 界面长什么样

```
┌──────────────────────────────────────────────┐
│                                              │
│   ✨ 百炼智能体                                │
│   直接输入问题即可。它自己判断要不要联网搜索或跑代码。  │
│                                              │
│   ┌──────────────────────────────────────┐   │
│   │ 帮我在网上搜一下今天北京的天气           │   │
│   └──────────────────────────────────────┘   │
│   ┌──────────────────────────────────────┐   │
│   │ 根据搜索结果，2026年9月29日北京天气如下： │   │
│   │ | 天气状况 | 白天多云…                │   │
│   │ 3.2s · 输入 5642 / 输出 210 tokens    │   │
│   └──────────────────────────────────────┘   │
│                                              │
│   ▸ 执行过程                                  │
│                                              │
└──────────────────────────────────────────────┘
```

- 提问时实时显示进度：「正在理解你的问题…」→「正在调用 web_search …」→「web_search 已返回，整理中…」
- 回答下方一行小字：耗时 + token 用量
- 可折叠的「执行过程」：看得到它思考了什么、调了哪个工具
- 底部「清空对话」，清空会同时开一个新会话

---

## 快速开始

```bash
git clone https://github.com/wzk-12138/bailian-agent-chat.git
cd bailian-agent-chat
pip install -r requirements.txt

cp .env.example .env        # Windows: copy .env.example .env
# 编辑 .env，填入下面三个值
streamlit run app.py
```

浏览器打开 <http://localhost:8501> 即可。

### `.env` 要三个值

| 变量 | 从哪儿拿 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼控制台 → 密钥管理，形如 `sk-xxx` |
| `MA_WORKSPACE_ID` | 控制台右上角头像旁的下拉 → 业务空间详情，形如 `ws-a1b2c3d4e5f6` |
| `MA_AGENT_ID` | Managed Agent → 智能体 → 详情页，形如 `agent_01M3PH58...` |

`.env` 已写进 `.gitignore`，不会被提交。

---

## 接入时的两个坑

### 坑一：下划线域名会让 Python 的 TLS 校验直接失败

如果 workspace ID 带下划线，域名就是 `ws_xxx.cn-beijing.maas.aliyuncs.com`。
**同一台机器上**：

| 调用方式 | 结果 |
|---|---|
| curl | ✅ 正常 |
| Python `requests` | ❌ `CERTIFICATE_VERIFY_FAILED: Hostname mismatch` |
| 官方 `dashscope.agentstudio` SDK | ❌ 同样失败（内部走 httpx） |

原因是 Python 3.13 + OpenSSL 3.5 做证书主机名校验时，把含下划线的主机名判为非法。

`app.py` 里的解法**保留了完整的证书链校验**，只把主机名匹配换成自己按 SAN 核对
（`_Adapter` + `_san_match`），安全强度不低于默认行为 —— 所以这个界面**不依赖官方 SDK**。

### 坑二：Workspace ID 是连字符，不是下划线

控制台截图里很容易看成 `ws_a1b2c3d4e5f6`，真实值其实是 **`ws-a1b2c3d4e5f6`**（连字符）。
`1`/`l`、`0`/`o` 同理。用错会得到：

```
400 {"code":"BadRequest.IllegalEndpoint","message":"Workspace endpoint is invalid."}
```

**这个错误对伪造的 ID 返回一模一样**，所以别拿它判断对错。取权威值只需 API Key：

```bash
npm install -g bailian-cli
bl auth login --api-key sk-xxx
bl quota list --output json | findstr workspace_id
```

---

## 调用链路

一次问答是四步（Environment 只需建一次）：

```
1) Environment（复用）        POST /environments
2) Session（每次任务新建）     POST /sessions          {agent, environment_id}
3) 发事件                     POST /sessions/{sid}/events
4) 订阅 SSE 收结果            GET  /sessions/{sid}/events/stream
```

端点：`https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio`

### 事件结构（实测）

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

两个容易踩的细节：

- SSE 是标准多行格式（`id:` / `event:` / `data:` 分行，中间夹 `:keepalive` 注释行），
  只累加 `data:` 行、忽略注释行
- `session_status` 藏在 `content[0].data.session_status`，**不在事件顶层**
- 控制台里工具标着「总需允许」，但通过 API 发起的会话里工具**会自动执行**，
  不需要客户端回传审批

---

## 文件

```
app.py            # 全部逻辑：界面 + 调用 + 事件解析，单文件
requirements.txt  # streamlit / requests / python-dotenv
.env.example      # 配置模板
LICENSE           # MIT
```

---

## 说明

- 界面针对**百炼 Managed Agent（新版智能体平台）**。百炼还有一套 Agent 1.0 / FlowAgent
  （应用管理 → 应用卡片 → `APP_ID` → `/api/v1/apps/{APP_ID}/completion`），接口完全不同，别混用。
- 验证环境：Windows + Python 3.13.14，streamlit 1.64.0。
