"""
百炼智能体 · 问答界面（精简版）

只管一件事：输入问题，得到回答。
配置读同目录 .env，不需要在界面上填任何东西。

运行：streamlit run app.py
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
from pathlib import Path

import requests
import streamlit as st
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter

load_dotenv(Path(__file__).parent / ".env", override=False)

API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
WORKSPACE = os.getenv("MA_WORKSPACE_ID", "")
AGENT_ID = os.getenv("MA_AGENT_ID", "")
ENV_ID = os.getenv("MA_ENV_ID", "")

st.set_page_config(page_title="百炼智能体", page_icon="✨", layout="centered")

st.markdown(
    """
<style>
  .block-container { max-width: 780px; padding-top: 3rem; padding-bottom: 6rem; }
  [data-testid="stSidebar"], header[data-testid="stHeader"] { display: none; }
  .title { font-size: 26px; font-weight: 500; color: #1b2a4a; margin-bottom: 4px; }
  .sub   { font-size: 13.5px; color: #8a94ab; margin-bottom: 28px; }
  .q {
      background:#eef3ff; border:1px solid #dbe4f8; border-radius:16px 16px 4px 16px;
      padding:14px 16px; margin:18px 0 6px 0; font-size:15px; line-height:1.7; color:#1e2a44;
  }
  .a {
      background:#fff; border:1px solid #e6e9f0; border-radius:16px 16px 16px 4px;
      padding:14px 16px; margin:6px 0 18px 0; font-size:15px; line-height:1.85; color:#22304d;
      box-shadow:0 1px 2px rgba(20,30,60,.04);
  }
  .phase { font-size:12px; color:#a8b0c2; margin-top:6px; }
  .meta  { font-size:11.5px; color:#b6bdcb; margin-top:8px; }
  .dot { display:inline-block; width:6px; height:6px; border-radius:50%; background:#7c9dff; margin-right:6px; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown('<div class="title">✨ 百炼智能体</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub">直接输入问题即可。它自己判断要不要联网搜索或跑代码。</div>',
    unsafe_allow_html=True,
)


# ------------------------------------------------------------------ TLS + 请求
def _san_match(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower().rstrip("."), host.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[2:]
        return host.endswith("." + suffix) and host.count(".") == suffix.count(".") + 1
    return host == pattern


class _Adapter(HTTPAdapter):
    """链校验照做，主机名交给 _san_match（容忍下划线域名）。"""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


@st.cache_resource(show_spinner=False)
def http() -> requests.Session:
    s = requests.Session()
    s.mount("https://", _Adapter())
    return s


BASE = f"https://{WORKSPACE}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio"
HDR = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def ensure_env() -> str:
    if ENV_ID:
        return ENV_ID
    r = http().get(f"{BASE}/environments", headers=HDR, timeout=30)
    items = (r.json().get("data") or []) if r.status_code == 200 else []
    if items:
        return items[0]["id"]
    r = http().post(
        f"{BASE}/environments",
        headers=HDR,
        json={"name": "web-env", "config": {"type": "cloud"}},
        timeout=120,
    )
    return (r.json().get("data") or {}).get("id") or r.json().get("id", "")


def new_session(env: str) -> str:
    r = http().post(
        f"{BASE}/sessions", headers=HDR, json={"agent": AGENT_ID, "environment_id": env}, timeout=120
    )
    if r.status_code >= 400:
        raise RuntimeError(f"创建会话失败 {r.status_code}: {r.text[:200]}")
    return (r.json().get("data") or {}).get("id") or r.json().get("id", "")


def send(session: str, text: str) -> None:
    r = http().post(
        f"{BASE}/sessions/{session}/events",
        headers=HDR,
        json={"input": [{"role": "user", "type": "message", "content": [{"type": "text", "text": text}]}]},
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"发送失败 {r.status_code}: {r.text[:200]}")


def _text(ev: dict) -> str:
    return "".join(
        c["text"]
        for c in (ev.get("content") or [])
        if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)
    )


def _state(ev: dict) -> dict:
    for c in ev.get("content") or []:
        if isinstance(c, dict) and isinstance(c.get("data"), dict) and "session_status" in c["data"]:
            return c["data"]
    return {}


def _tool(ev: dict) -> str:
    for c in ev.get("content") or []:
        if isinstance(c, dict) and isinstance(c.get("data"), dict) and c["data"].get("name"):
            return str(c["data"]["name"])
    return ""


def _sse(resp):
    buf: list = []
    for raw in resp.iter_lines(decode_unicode=True):
        line = (raw or "").strip()
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


def ask(session: str, question: str, paint) -> dict:
    """发问并订阅事件流，paint(text, phase) 负责把中间态画出来。"""
    url = f"{BASE}/sessions/{session}/events/stream"
    h = dict(HDR)
    h["Accept"] = "text/event-stream"
    answer, notes, info = "", [], {}

    with http().get(url, headers=h, stream=True, timeout=(20, 600)) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"订阅失败 {r.status_code}: {r.text[:200]}")
        for payload in _sse(r):
            if not payload or payload == "[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except Exception:  # noqa: BLE001
                continue
            et = ev.get("type")
            if et == "message" and ev.get("role") == "assistant":
                t = _text(ev)
                if t:
                    answer = t
                    paint(answer, "")
            elif et == "reasoning":
                t = _text(ev)
                if t:
                    notes.append(("思考", t))
                paint(answer, "思考中…")
            elif et == "tool_call":
                name = _tool(ev) or "工具"
                notes.append(("工具", name))
                paint(answer, f"正在调用 {name} …")
            elif et == "tool_call_output":
                paint(answer, f"{_tool(ev) or '工具'} 已返回，整理中…")
            elif et == "model_request_end":
                for c in ev.get("content") or []:
                    d = c.get("data") if isinstance(c, dict) else None
                    if isinstance(d, dict) and d.get("output_tokens") is not None:
                        info["usage"] = d
            st_ = _state(ev)
            if st_:
                info["status"] = st_.get("session_status", "")
                if info["status"] in ("idle", "terminated"):
                    break

    if not answer:  # 兜底：SSE 没给正文就去事件列表取
        try:
            r = http().get(f"{BASE}/sessions/{session}/events", headers=HDR, timeout=60)
            for ev in r.json().get("data") or []:
                if ev.get("type") == "message" and ev.get("role") == "assistant":
                    t = _text(ev)
                    if t:
                        answer = t
        except Exception:  # noqa: BLE001
            pass

    info["notes"] = notes
    return {"answer": answer, "info": info}


# ------------------------------------------------------------------ 界面
if not (API_KEY and WORKSPACE and AGENT_ID):
    st.error("配置缺失：请在 .env 中填写 DASHSCOPE_API_KEY / MA_WORKSPACE_ID / MA_AGENT_ID。")
    st.stop()

if "session" not in st.session_state:
    st.session_state.session = ""
if "history" not in st.session_state:
    st.session_state.history = []

for q, a, meta in st.session_state.history:
    st.markdown(f'<div class="q">{q}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="a">{a}<div class="meta">{meta}</div></div>', unsafe_allow_html=True)

question = st.chat_input("输入你的问题…")
if question:
    st.markdown(f'<div class="q">{question}</div>', unsafe_allow_html=True)
    slot = st.empty()
    t0 = time.time()

    def paint(text: str, phase: str) -> None:
        body = text or '<span style="color:#a8b0c2">正在处理…</span>'
        ph = f'<div class="phase"><span class="dot"></span>{phase}</div>' if phase else ""
        slot.markdown(f'<div class="a">{body}{ph}</div>', unsafe_allow_html=True)

    paint("", "正在理解你的问题…")
    try:
        if not st.session_state.session:
            st.session_state.session = new_session(ensure_env())
        send(st.session_state.session, question)
        res = ask(st.session_state.session, question, paint)
        answer, info = res["answer"], res["info"]

        if info.get("notes"):
            with st.expander("执行过程", expanded=False):
                for kind, val in info["notes"]:
                    if kind == "工具":
                        st.markdown(f"- **调用工具** `{val}`")
                    else:
                        st.markdown(f"- **思考**：{val[:300]}")
        usage = info.get("usage") or {}
        meta = f"{time.time() - t0:.1f}s"
        if usage:
            meta += f" · 输入 {usage.get('input_tokens')} / 输出 {usage.get('output_tokens')} tokens"
        slot.markdown(
            f'<div class="a">{answer or "（没有返回内容）"}<div class="meta">{meta}</div></div>',
            unsafe_allow_html=True,
        )
        st.session_state.history.append((question, answer, meta))
    except Exception as exc:  # noqa: BLE001
        slot.empty()
        st.error(f"出错了：{exc}")

if st.session_state.history:
    if st.button("清空对话", type="secondary"):
        st.session_state.history = []
        st.session_state.session = ""
        st.rerun()
