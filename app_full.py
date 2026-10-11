"""
阿里云百炼智能体 · Streamlit 调用台

支持两种接入方式：
  A. 百炼应用（智能体 1.0 / FlowAgent）
     POST https://dashscope.aliyuncs.com/api/v1/apps/{APP_ID}/completion
  B. Managed Agent（新版智能体平台 / agentstudio）
     基础地址：https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio
     流程：复用 Environment → 建 Session → 发 Event → 订阅 SSE

运行：streamlit run app.py
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import uuid
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv, set_key
from requests.adapters import HTTPAdapter

APP_DIR = Path(__file__).parent
ENV_FILE = APP_DIR / ".env"
load_dotenv(ENV_FILE, override=False)

st.set_page_config(
    page_title="百炼智能体 · 本地调用台",
    page_icon="🧭",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
  .block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1180px; }
  [data-testid="stSidebar"] { background: #f7f9fc; }
  .hero {
      background: linear-gradient(120deg, #eef4ff 0%, #f6f0ff 55%, #fff5ec 100%);
      border: 1px solid #e3e8f2; border-radius: 16px;
      padding: 18px 22px; margin-bottom: 14px;
  }
  .hero h1 { font-size: 22px; margin: 0 0 6px 0; color: #1b2a4a; letter-spacing: .3px; }
  .hero p  { margin: 0; color: #5a6785; font-size: 13.5px; line-height: 1.7; }
  .pill {
      display:inline-block; padding: 2px 10px; margin: 2px 6px 2px 0;
      border-radius: 999px; font-size: 12px; background:#eaf1ff; color:#2c4a86;
      border:1px solid #d3e0f7;
  }
  .msg-user {
      background:#eef3ff; border:1px solid #dbe4f8; border-radius:14px 14px 4px 14px;
      padding:12px 14px; margin:8px 0; font-size:14.5px; line-height:1.75; color:#1e2a44;
  }
  .msg-bot {
      background:#ffffff; border:1px solid #e6e9f0; border-radius:14px 14px 14px 4px;
      padding:12px 14px; margin:8px 0; font-size:14.5px; line-height:1.8; color:#22304d;
      box-shadow: 0 1px 2px rgba(20,30,60,.04);
  }
  .msg-label { font-size:11.5px; color:#8a94ab; letter-spacing:.6px; margin-bottom:4px; }
  .meta { font-size:12px; color:#8a94ab; margin-top:2px; }
  .card {
      border:1px solid #e6e9f0; border-radius:14px; padding:14px 16px;
      background:#fff; margin-bottom:12px;
  }
  .card h4 { margin:0 0 6px 0; font-size:15px; color:#1b2a4a; }
  .card p, .card li { font-size:13.6px; color:#4c5772; line-height:1.75; }
  .quote {
      border-left:3px solid #7c9dff; background:#f7f9ff; padding:8px 12px;
      color:#3c4a68; font-size:13.4px; margin:8px 0; border-radius:0 8px 8px 0;
  }
  code { background:#f2f4f8; padding:1px 5px; border-radius:5px; font-size:12.6px; }
</style>
""",
    unsafe_allow_html=True,
)

MODE_APP = "百炼应用（Agent 1.0 / FlowAgent，APP_ID）"
MODE_MA = "Managed Agent（新版平台，agentstudio）"


def read_env(key: str) -> str:
    return os.getenv(key, "") or ""


def save_env(pairs: dict[str, str]) -> None:
    if not ENV_FILE.exists():
        ENV_FILE.write_text("# 百炼调用配置\n", encoding="utf-8")
    for k, v in pairs.items():
        if v:
            set_key(str(ENV_FILE), k, v)


# ---------------------------------------------------------------- 状态

if "session_id" not in st.session_state:
    st.session_state.session_id = uuid.uuid4().hex
if "ma_session" not in st.session_state:
    st.session_state.ma_session = ""
if "ma_events" not in st.session_state:
    st.session_state.ma_events = []
if "history" not in st.session_state:
    st.session_state.history = []

# ---------------------------------------------------------------- 侧边栏

with st.sidebar:
    st.markdown("### 🔌 接入方式")
    mode = st.radio(
        "mode",
        [MODE_APP, MODE_MA],
        label_visibility="collapsed",
        help="Agent 1.0 用 APP_ID；Managed Agent 用 workspace_id + agent_id。",
    )

    st.markdown("### 🔐 凭据")
    api_key = st.text_input(
        "DASHSCOPE_API_KEY",
        value=read_env("DASHSCOPE_API_KEY"),
        type="password",
        placeholder="sk-xxxx",
    )
    app_id = workspace = agent_id = env_id = ""

    if mode == MODE_APP:
        app_id = st.text_input(
            "APP_ID", value=read_env("BAILIAN_APP_ID"), placeholder="应用卡片上的 ID"
        )
        workspace = st.text_input(
            "WORKSPACE_ID（可选）", value=read_env("BAILIAN_WORKSPACE_ID"), placeholder="子业务空间才填"
        )
    else:
        workspace = st.text_input(
            "WORKSPACE_ID", value=read_env("MA_WORKSPACE_ID"), placeholder="ws_xxxxxxxxxxxx"
        )
        agent_id = st.text_input(
            "AGENT_ID", value=read_env("MA_AGENT_ID"), placeholder="agent_xxxxxxxx"
        )
        env_id = st.text_input(
            "ENVIRONMENT_ID（可留空自动创建）", value=read_env("MA_ENV_ID"), placeholder="env_xxxxxxxx"
        )

    st.markdown("---")
    st.markdown("### ⚙️ 参数")
    stream_on = st.toggle("流式输出（推荐）", value=True)
    if mode == MODE_APP:
        use_session = st.toggle("云端会话记忆（session_id）", value=True)
        incremental = st.toggle("增量输出 incremental_output", value=True)
    else:
        use_session = False
        incremental = True
    show_trace = st.toggle("显示 request_id / 耗时", value=True)
    show_raw = st.toggle("显示原始事件流（排错用）", value=False)

    st.markdown("---")
    if st.button("💾 保存配置到 .env", use_container_width=True):
        save_env(
            {
                "DASHSCOPE_API_KEY": api_key,
                "BAILIAN_APP_ID": app_id,
                "BAILIAN_WORKSPACE_ID": workspace if mode == MODE_APP else "",
                "MA_WORKSPACE_ID": workspace if mode == MODE_MA else "",
                "MA_AGENT_ID": agent_id,
                "MA_ENV_ID": env_id,
            }
        )
        st.success("已写入 .env")

    if st.button("🧹 清空对话", use_container_width=True):
        st.session_state.history = []
        st.session_state.session_id = uuid.uuid4().hex
        st.session_state.ma_session = ""
        st.session_state.ma_events = []
        st.rerun()

    st.markdown("---")
    st.markdown(
        """
**找不到 workspace ID？**

控制台右上角头像旁的下拉 → 业务空间详情 / 业务空间管理，
复制形如 `ws_xxxxxxxxxxxx` 的 ID。
官方说明：该 ID 只能从控制台手动复制。
"""
    )

# ---------------------------------------------------------------- 标题

st.markdown(
    f"""
<div class="hero">
  <h1>🧭 百炼智能体 · 本地调用台</h1>
  <p>
    当前接入：<b>{mode}</b>。本页按《深入理解 AI Agent》（<code>bojieli/ai-agent-book</code>）的框架组织：
    <b>Agent = LLM + 上下文 + 工具</b>，百炼承载 Harness，本页承载 Environment 与人在回路。
  </p>
  <div style="margin-top:10px">
    <span class="pill">Harness 托管</span>
    <span class="pill">Graph / 事件流</span>
    <span class="pill">Agent = LLM + 上下文 + 工具</span>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

tab_chat, tab_design, tab_help = st.tabs(["💬 对话调用", "📐 设计思想对照", "🛠️ 连通性自检"])


# ================================================================
# 百炼应用（Agent 1.0）调用
# ================================================================
def call_app(prompt: str):
    from dashscope import Application

    kwargs = dict(
        api_key=api_key or None,
        app_id=app_id,
        prompt=prompt,
        stream=stream_on,
        incremental_output=incremental if stream_on else False,
    )
    if use_session:
        kwargs["session_id"] = st.session_state.session_id
    if workspace.strip():
        kwargs["workspace"] = workspace.strip()
    return Application.call(**kwargs)


# ================================================================
# TLS 兼容层
# ----------------------------------------------------------------
# 现象：Managed Agent 的 endpoint 形如 ws_xxx.cn-beijing.maas.aliyuncs.com，
# 域名里带下划线。Python 3.13 + OpenSSL 3.5 在做证书主机名校验时会把这种
# 主机名判为非法（certificate verify failed: Hostname mismatch），
# 而 curl / 官方 SDK（内部走 httpx）在同一台机器上表现不一致。
#
# 处理方式：保留完整的证书链校验（CERT_REQUIRED），只把主机名匹配交给我们
# 自己的 RFC 风格匹配器去按 SAN 核对 —— 安全强度不低于默认行为。
# ================================================================


def _san_match(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower().rstrip("."), host.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host.endswith("." + suffix) and host.count(".") == suffix.count(".") + 1
    return host == pattern


def verify_host_san(host: str, port: int = 443, timeout: int = 15) -> list:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
    sans = [v for k, v in cert.get("subjectAltName", []) if k == "DNS"]
    if not any(_san_match(p, host) for p in sans):
        raise ssl.SSLCertVerificationError(f"证书 SAN 不含 {host}：{sans}")
    return sans


class _RelaxedHostnameAdapter(HTTPAdapter):
    """链校验照做，主机名由 verify_host_san 单独核对。"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


@st.cache_resource(show_spinner=False)
def ma_session() -> requests.Session:
    s = requests.Session()
    s.mount("https://", _RelaxedHostnameAdapter())
    return s


def ma_base() -> str:
    return f"https://{workspace.strip()}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio"


def ma_headers() -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def ma_json(resp: requests.Response):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"_raw": resp.text[:500]}


def ma_ensure_env() -> str:
    """复用已有 Environment，没有就建一个。"""
    if env_id.strip():
        return env_id.strip()
    s = ma_session()
    r = s.get(f"{ma_base()}/environments", headers=ma_headers(), timeout=30)
    data = ma_json(r)
    items = data.get("data") or data.get("items") or data.get("environments") or []
    if isinstance(items, dict):
        items = items.get("items", [])
    for it in items:
        if isinstance(it, dict) and it.get("id"):
            return it["id"]
    r = s.post(
        f"{ma_base()}/environments",
        headers=ma_headers(),
        json={"name": "streamlit-env", "config": {"type": "cloud"}},
        timeout=120,
    )
    data = ma_json(r)
    if r.status_code >= 400:
        raise RuntimeError(f"创建 Environment 失败 {r.status_code}: {json.dumps(data, ensure_ascii=False)[:300]}")
    return data.get("id") or (data.get("data") or {}).get("id")


def ma_new_session(env: str) -> str:
    r = ma_session().post(
        f"{ma_base()}/sessions",
        headers=ma_headers(),
        json={"agent": agent_id.strip(), "environment_id": env},
        timeout=120,
    )
    data = ma_json(r)
    if r.status_code >= 400:
        raise RuntimeError(f"创建 Session 失败 {r.status_code}: {json.dumps(data, ensure_ascii=False)[:300]}")
    return data.get("id") or (data.get("data") or {}).get("id")


def ma_send(session: str, text: str) -> None:
    payload = {
        "input": [
            {
                "role": "user",
                "type": "message",
                "content": [{"type": "text", "text": text}],
            }
        ]
    }
    r = ma_session().post(
        f"{ma_base()}/sessions/{session}/events", headers=ma_headers(), json=payload, timeout=60
    )
    if r.status_code >= 400:
        raise RuntimeError(f"发送事件失败 {r.status_code}: {r.text[:300]}")


def collect_text(obj, out: list) -> None:
    """递归收集事件里的文本片段（兜底用）。"""
    if isinstance(obj, dict):
        if obj.get("type") == "text" and isinstance(obj.get("text"), str):
            out.append(obj["text"])
            return
        for v in obj.values():
            collect_text(v, out)
    elif isinstance(obj, list):
        for v in obj:
            collect_text(v, out)


def ev_text(ev: dict) -> str:
    """取事件 content 里的文本（只认 type=text 的块）。"""
    out: list = []
    for c in ev.get("content") or []:
        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
            out.append(c["text"])
    return "".join(out)


def ev_session_state(ev: dict) -> dict:
    """session_status 事件的 content[0].data，含 session_status / stop_reason。"""
    for c in ev.get("content") or []:
        if isinstance(c, dict) and isinstance(c.get("data"), dict):
            d = c["data"]
            if "session_status" in d:
                return d
    return {}


def ev_tool_name(ev: dict) -> str:
    """tool_call / tool_call_output 事件里的工具名。"""
    for c in ev.get("content") or []:
        if isinstance(c, dict) and isinstance(c.get("data"), dict):
            d = c["data"]
            if d.get("name"):
                return str(d["name"])
    return ""


def iter_sse(resp):
    """按 SSE 规范切分事件：data: 行累加，注释行(:)与 id/event 行忽略。"""
    buf: list = []
    for raw in resp.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.strip()
        if not line:
            if buf:
                yield "\n".join(buf)
                buf = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buf.append(line[5:].lstrip())
    if buf:
        yield "\n".join(buf)


def ma_fetch_events(session: str) -> list:
    """列表接口取权威事件（用于兜底拿最终答复）。"""
    r = ma_session().get(f"{ma_base()}/sessions/{session}/events", headers=ma_headers(), timeout=60)
    d = ma_json(r)
    data = d.get("data")
    return data if isinstance(data, list) else []


def ma_stream(session: str, on_delta, meta: dict) -> str:
    """订阅 SSE 推进会话。逐条解析：

    - message + role=assistant  → 正文（每个事件即一条完整消息）
    - reasoning                 → 思考过程
    - model_request_end         → token 用量
    - session_status            → idle / terminated 时结束
    """
    url = f"{ma_base()}/sessions/{session}/events/stream"
    headers = ma_headers()
    headers["Accept"] = "text/event-stream"
    answers: list = []
    reasoning: list = []

    with ma_session().get(url, headers=headers, stream=True, timeout=(20, 600)) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"订阅事件流失败 {r.status_code}: {r.text[:300]}")
        meta["request_id"] = r.headers.get("x-request-id", "")
        for payload in iter_sse(r):
            if not payload or payload == "[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue
            st.session_state.ma_events.append(ev)
            etype = ev.get("type")

            if etype == "reasoning":
                txt = ev_text(ev)
                if txt:
                    reasoning.append(txt)
                meta["phase"] = "思考中"
                on_delta("\n\n".join(answers), meta)
            elif etype == "message" and ev.get("role") == "assistant":
                txt = ev_text(ev)
                if txt:
                    answers.append(txt)
                meta["phase"] = "回答中"
                on_delta("\n\n".join(answers), meta)
            elif etype == "tool_call":
                tool = ev_tool_name(ev) or "工具"
                meta["phase"] = f"调用 {tool}"
                on_delta("\n\n".join(answers), meta)
            elif etype == "tool_call_output":
                tool = ev_tool_name(ev) or "工具"
                meta["phase"] = f"{tool} 已返回"
                on_delta("\n\n".join(answers), meta)
            elif etype and "tool" in etype:
                meta["phase"] = "调用工具"
                on_delta("\n\n".join(answers), meta)
            elif etype == "model_request_end":
                for c in ev.get("content") or []:
                    d = c.get("data") if isinstance(c, dict) else None
                    if isinstance(d, dict) and d.get("output_tokens") is not None:
                        meta["usage"] = f"输入 {d.get('input_tokens')} / 输出 {d.get('output_tokens')} tokens"

            state = ev_session_state(ev)
            if state:
                meta["status"] = state.get("session_status", "")
                stop = state.get("stop_reason") or {}
                if isinstance(stop, dict) and stop.get("type"):
                    meta["stop_reason"] = stop["type"]
                if meta["status"] in ("idle", "terminated"):
                    break

    # 兜底：SSE 可能漏掉正文，用列表接口取权威结果
    if not answers or meta.get("status") not in ("idle", "terminated"):
        try:
            for ev in ma_fetch_events(session):
                if ev.get("type") == "message" and ev.get("role") == "assistant":
                    t = ev_text(ev)
                    if t and t not in answers:
                        answers.append(t)
        except Exception:  # noqa: BLE001
            pass

    if reasoning:
        meta["reasoning"] = "\n".join(reasoning)
    return "\n\n".join(answers)


def render_history():
    for role, text, m in st.session_state.history:
        cls = "msg-user" if role == "user" else "msg-bot"
        label = "YOU" if role == "user" else "AGENT"
        st.markdown(
            f'<div class="{cls}"><div class="msg-label">{label}</div>{text}'
            f'<div class="meta">{m}</div></div>',
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------- 对话页

with tab_chat:
    ready = bool(api_key) and (
        bool(app_id) if mode == MODE_APP else (bool(workspace) and bool(agent_id))
    )
    if not ready:
        need = "DASHSCOPE_API_KEY" + ("、APP_ID" if mode == MODE_APP else "、WORKSPACE_ID、AGENT_ID")
        st.info(f"请先在左侧填写 **{need}**。", icon="🔑")

    render_history()

    user_input = st.chat_input("输入你的问题，回车发送…")
    if user_input and ready:
        st.session_state.history.append(("user", user_input, ""))
        st.markdown(
            f'<div class="msg-user"><div class="msg-label">YOU</div>{user_input}</div>',
            unsafe_allow_html=True,
        )
        holder = st.empty()
        acc, meta_txt = "", ""
        t0 = time.time()
        try:
            if mode == MODE_APP:
                resp = call_app(user_input)
                if stream_on:
                    rid = ""
                    for chunk in resp:
                        if getattr(chunk, "status_code", 200) != 200:
                            raise RuntimeError(
                                f"{chunk.status_code} / {chunk.message}（request_id={chunk.request_id}）"
                            )
                        acc += chunk.output.text or ""
                        rid = getattr(chunk, "request_id", rid)
                        holder.markdown(
                            f'<div class="msg-bot"><div class="msg-label">AGENT</div>{acc}▌</div>',
                            unsafe_allow_html=True,
                        )
                    meta_txt = f"request_id={rid}"
                else:
                    if resp.status_code != 200:
                        raise RuntimeError(f"{resp.status_code} / {resp.message}（request_id={resp.request_id}）")
                    acc = resp.output.text or ""
                    meta_txt = f"request_id={getattr(resp, 'request_id', '-')}"
            else:
                env = ma_ensure_env()
                session = st.session_state.ma_session or ma_new_session(env)
                st.session_state.ma_session = session
                st.session_state.ma_events = []

                ma_meta: dict = {"request_id": "", "status": "", "phase": "", "usage": ""}

                def on_delta(text, meta=None):
                    phase = (meta or {}).get("phase", "")
                    tail = f"<span style='color:#a8b0c2'>· {phase}</span>" if phase else ""
                    body = text or "<span style='color:#a8b0c2'>（正在处理…）</span>"
                    holder.markdown(
                        f'<div class="msg-bot"><div class="msg-label">AGENT</div>{body}{tail}▌</div>',
                        unsafe_allow_html=True,
                    )

                ma_send(session, user_input)
                acc = ma_stream(session, on_delta, ma_meta)
                meta_txt = (
                    f"session={session} · env={env} · {ma_meta.get('status', '')}"
                    f"{(' · ' + ma_meta['usage']) if ma_meta.get('usage') else ''}"
                )
                if ma_meta.get("reasoning"):
                    with st.expander("🧠 思考过程（reasoning）", expanded=False):
                        st.markdown(ma_meta["reasoning"])

            elapsed = time.time() - t0
            if show_trace:
                meta_txt = f"{meta_txt} · {elapsed:.1f}s"
            holder.markdown(
                f'<div class="msg-bot"><div class="msg-label">AGENT</div>{acc or "(无文本输出)"}'
                f'<div class="meta">{meta_txt}</div></div>',
                unsafe_allow_html=True,
            )
            st.session_state.history.append(("bot", acc, meta_txt))

        except Exception as exc:  # noqa: BLE001
            holder.empty()
            err = str(exc)
            hint = ""
            if "InvalidApiKey" in err or "401" in err:
                hint = "API Key 无效或未开通百炼。"
            elif "App.AccessDenied" in err or "403" in err:
                hint = "应用未发布，或 Key 与应用的业务空间不一致。"
            elif "404" in err:
                hint = "APP_ID / AGENT_ID 不存在，或应用未发布。"
            st.error(f"调用失败：{err}\n\n{hint}")

    if mode == MODE_MA and show_raw and st.session_state.ma_events:
        with st.expander(f"原始事件流（{len(st.session_state.ma_events)} 条）", expanded=False):
            st.json(st.session_state.ma_events)


# ---------------------------------------------------------------- 设计思想页

with tab_design:
    st.markdown("## 📐 这个智能体的设计符合什么思想")
    st.caption("对照 bojieli/ai-agent-book《深入理解 AI Agent：设计原理与工程实践》（李博杰著）")

    st.markdown(
        '<div class="quote">全书核心公式：<b>Agent = LLM + 上下文 + 工具</b>；'
        "生产形态展开为 <b>Agent = Model + Harness</b>，"
        "<b>Harness = 上下文管理 + 工具接口 + 约束 + 验证 + 纠正</b>。</div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            """
<div class="card">
<h4>1️⃣ 三要素齐全：大脑 / 眼睛 / 手脚</h4>
<ul>
<li><b>LLM 节点 / 模型</b> = 大脑（决策核心）</li>
<li><b>知识库、变量、记忆、对话历史</b> = 上下文（眼睛）</li>
<li><b>插件、MCP、代码沙箱、联网检索</b> = 工具（手脚）</li>
</ul>
<p>书里反复强调：<b>扩展眼睛和手脚是最主要的能力杠杆</b>。同一个模型换一套上下文和工具，
原本不可解的任务往往直接变可解。</p>
</div>
<div class="card">
<h4>2️⃣ 工作流模式：确定性编排（1.0 / FlowAgent 的本质）</h4>
<p>执行拓扑由画布预先定义，LLM 只在节点内理解与生成。优势是<b>严格流程控制</b>
（「先检索后回答」被强制执行）与<b>安全性</b>（提示注入最多影响单个节点，攻击面被关在节点内）；
局限是<b>缺乏变通</b>。</p>
<p>自治型的 Managed Agent 则相反：路径由模型依据环境反馈实时决定。
书里的推进顺序是「<b>先提示词 → 再工作流 → 最后自主 Agent</b>」——先跑 1.0 正是踩在这条路径上。</p>
</div>
<div class="card">
<h4>3️⃣ Graph 工程：节点 + 边 + 可检查点状态</h4>
<p>书中记录的 2026 年新范式：把 Agent 循环、确定性程序和人工审批组织成显式执行图，
节点承担能力，边规定路由，结构化状态沿边传递并在关键边界持久化。</p>
<p>FlowAgent 的画布就是这张图。范式层层包含：提示工程 ⊂ 上下文工程 ⊂ Harness 工程 ⊂ Loop 工程 ⊂ Graph 工程。</p>
</div>
<div class="card">
<h4>4️⃣ Harness 被平台托管：约束 + 验证 + 纠正</h4>
<p>书里断言「<b>行业正从『能做事』转向『可靠地做事』，Harness 是核心竞争力</b>」，
并以 Claude Code 为例说明绝大部分代码都花在约束、验证与纠正上。</p>
<p>百炼把这一层做成托管能力：沙箱隔离、工具权限、Vault 密钥、上下文压缩、断点续跑、
运行日志与 Trace。Managed Agent 把它做得更彻底——<b>Session 与 Environment 都由平台托管</b>，
本地只负责「发一句、收一串」。<b>平台就是 Harness。</b></p>
</div>
""",
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            """
<div class="card">
<h4>5️⃣ ACI 与防呆：把工具接口设计对</h4>
<p>Anthropic 三原则之一是<b>设计好 ACI</b>——从 Agent 视角而非程序员视角设计接口：
命名直观、参数带例子、边界写清楚，用设计让错误<b>无法发生</b>（防呆 / Poka-yoke）。</p>
<p>落到成品：每个插件、函数节点、内置工具（bash / read / write / glob / grep）的<b>描述文字就是 ACI</b>。
模型与工具之间唯一的通道就是接口本身，模糊描述会被放大成系统性错误。</p>
</div>
<div class="card">
<h4>6️⃣ 渐进式披露：别把上下文一次塞满</h4>
<p><b>不把全部信息一次放进上下文，先给可检索的目录，再按需加载细节</b>，
同时优化上下文预算与选择精度。</p>
<p>对应做法：知识库按需召回而不是整库灌入；工具多时分层与按需发现；
Managed Agent 的 Skill 机制本身就是「元数据常驻、正文按需加载」。</p>
</div>
<div class="card">
<h4>7️⃣ 三层护栏：上下文层 / 执行层 / 数据层</h4>
<p>按<b>被绕过的难度</b>排序，越靠下越不依赖模型自己的判断：</p>
<ul>
<li><b>上下文层</b>：意图分类拦截跑题、越狱与提示注入；指令与数据分离</li>
<li><b>执行层</b>：工具风险评级、最小权限凭证、沙箱隔离、<b>人在回路</b>；输出侧 PII 过滤</li>
<li><b>数据层</b>：知识库与业务库的权限校验，上层被绕过时越权仍在数据层被拒</li>
</ul>
<p>还有一条容易漏的纪律：护栏要同时测「该拦的是否拦住」<b>和</b>「该放行的是否放行」，否则陷入<b>误拒绝</b>。</p>
</div>
<div class="card">
<h4>8️⃣ 提议者—审核者：别让同一个 Agent 自审</h4>
<p><b>产出与评判由两个不共享上下文的角色分别承担</b>，评判方只看产物本身，
不看产出方的推理过程——前提是<b>自审不可靠</b>。</p>
<p>落地方式：流程末端挂一个独立审核节点；对高风险分支挂人工确认节点做熔断。</p>
</div>
<div class="card">
<h4>9️⃣ 本页自己的角色：Environment 与人在回路</h4>
<p>按书中边界：<b>Harness 是 Agent 边界内、模型之外的运行与治理层</b>，
用户、外部系统、数据库属于 Environment。</p>
<p>这个 Streamlit 页面不生产智能，它承担<b>观察与动作空间的延伸</b>：
把输入送进 Agent，把 Trace 与结果呈现给人，必要时让人接管——
对应书里的<b>人工干预</b>与「<b>保持透明</b>」原则。</p>
</div>
""",
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("### 🔟 一条反向约束：Less structure, more intelligence")
    st.markdown(
        '<div class="quote">书里引用 Manus 的主张：<b>好的结构不是替 Agent 预演全部思考，'
        "而是守住边界、把边界内的决策空间还给模型</b>。一上来就画十几个节点的流水线，"
        "每个部件单看都有道理，组合起来却低效不可靠——固定拓扑遇到新例外就继续加节点，"
        "通用性反而越来越差。<br><br>"
        "实操建议：<b>分支控制在 5～7 个以内</b>，只在评估反复暴露某类稳定失败时，"
        "才把该步骤固化成节点。</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- 自检页

with tab_help:
    st.markdown("## 🛠️ 连通性自检")

    if mode == MODE_APP:
        st.caption("用一句「你好，请用一句话介绍你自己」探测 APP_ID 与 Key 是否匹配。")
        if st.button("🚀 发送测试请求", type="primary"):
            if not api_key or not app_id:
                st.error("请先填写 API Key 和 APP_ID。")
            else:
                with st.spinner("请求中…"):
                    try:
                        from dashscope import Application

                        r = Application.call(
                            api_key=api_key, app_id=app_id, prompt="你好，请用一句话介绍你自己。", stream=False
                        )
                        if r.status_code == 200:
                            st.success(f"✅ 调用成功（request_id={r.request_id}）")
                            st.write(r.output.text)
                        else:
                            st.error(f"❌ {r.status_code} / {r.message}（request_id={r.request_id}）")
                    except Exception as exc:  # noqa: BLE001
                        st.exception(exc)
    else:
        st.caption("分步探测：TLS 证书 → 列出 Environment → 读取 Agent 详情。")
        if st.button("🚀 分步自检", type="primary"):
            if not api_key or not workspace:
                st.error("请先填写 API Key 和 WORKSPACE_ID。")
            else:
                host = f"{workspace.strip()}.cn-beijing.maas.aliyuncs.com"
                base = ma_base()
                st.code(f"Endpoint: {base}")
                try:
                    sans = verify_host_san(host)
                    st.success(f"**0. TLS 证书** → 已用系统 CA 校验通过，SAN（前两项）：`{sans[:2]}`")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"**0. TLS 证书** → 校验失败：{exc}")
                try:
                    r = ma_session().get(f"{base}/environments", headers=ma_headers(), timeout=30)
                    st.markdown(f"**1. 列出 Environment** → `{r.status_code}`")
                    st.json(ma_json(r))
                    if r.status_code == 400:
                        st.warning(
                            "返回 `BadRequest.IllegalEndpoint`（Workspace endpoint is invalid）时，"
                            "绝大多数是 **WORKSPACE_ID 复制有误**（下划线/数字易看错），"
                            "请回控制台用复制按钮重新取一次。"
                        )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Environment 探测失败（多半是 workspace_id 或网络问题）：{exc}")
                if agent_id:
                    try:
                        r = ma_session().get(f"{base}/agents/{agent_id}", headers=ma_headers(), timeout=30)
                        st.markdown(f"**2. 读取 Agent `{agent_id}`** → `{r.status_code}`")
                        st.json(ma_json(r))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Agent 读取失败：{exc}")
                else:
                    st.info("填上 AGENT_ID 后可以进一步检查 Agent 是否存在。")

    st.markdown("---")
    st.markdown("### 两种接入方式的区别")
    st.markdown(
        f"""
| | 百炼应用（Agent 1.0 / FlowAgent） | Managed Agent（新版平台） |
|---|---|---|
| 创建入口 | 应用管理 → 创建应用 → 智能体应用 | 顶栏 **Managed Agent** → 智能体 → 创建智能体 |
| ID 形态 | 数字，如 `1941890680279287` | `agent_xxxxxxxx` |
| 调用地址 | `dashscope.aliyuncs.com/api/v1/apps/{{APP_ID}}/completion` | `{{workspace_id}}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio` |
| 调用模型 | 一问一答（`session_id` 托管多轮） | 五步：Environment → Session → Event → SSE |
| 编排 | 画布固定拓扑 | 模型自主规划 + 内置工具 + 沙箱 |
| 发布 | 右上角「发布」→ API 调用 | 创建即在「活跃」列表，配好 Environment 即可调用 |
"""
    )

    st.markdown("### 等价的 curl（Managed Agent 全链路）")
    st.code(
        """BASE="https://$WORKSPACE_ID.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio"
AUTH="Authorization: Bearer $DASHSCOPE_API_KEY"

# 1) Environment（可复用）
ENV_ID=$(curl -s -X POST "$BASE/environments" -H "$AUTH" -H "Content-Type: application/json" \\
  -d '{"name":"streamlit-env","config":{"type":"cloud"}}' | jq -r .id)

# 2) Session
SESSION_ID=$(curl -s -X POST "$BASE/sessions" -H "$AUTH" -H "Content-Type: application/json" \\
  -d "{\\"agent\\":\\"$AGENT_ID\\",\\"environment_id\\":\\"$ENV_ID\\"}" | jq -r .id)

# 3) 发事件
curl -s -X POST "$BASE/sessions/$SESSION_ID/events" -H "$AUTH" -H "Content-Type: application/json" \\
  -d '{"input":[{"role":"user","type":"message","content":[{"type":"text","text":"你好"}]}]}'

# 4) 订阅 SSE
curl -N "$BASE/sessions/$SESSION_ID/events/stream" -H "$AUTH" -H "Accept: text/event-stream"
""",
        language="bash",
    )
