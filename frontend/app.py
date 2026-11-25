"""Streamlit chat UI for TarnishedBot.

Run from the project root:
    uv run streamlit run frontend/app.py
"""

import json
import os

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
REQUEST_TIMEOUT = 120

st.set_page_config(page_title="TarnishedBot", page_icon="🗡️")
st.title("TarnishedBot")
st.caption("Your guide to Limgrave. Game data: Elden Ring 1.17.2 / regulation 1.17.3.")

VOICES = {"default": "Default mode", "in_character": "In-character"}

if "messages" not in st.session_state:
    st.session_state.messages = []

# Switching voice keeps the chat: messages live in session_state, and the voice
# only changes how the next reply is written.
with st.sidebar:
    voice = (
        st.segmented_control(
            "Voice",
            options=list(VOICES),
            format_func=VOICES.get,
            default="default",
            key="voice",
            help="Changes how replies are written. Your chat history stays.",
        )
        or "default"
    )


def show_tool_calls(calls: list[dict]) -> None:
    if not calls:
        return
    with st.expander(f"Tools used ({len(calls)})"):
        for call in calls:
            label = "error" if call.get("is_error") else "ok"
            st.markdown(f"**{call['name']}** ({label})")
            st.code(json.dumps(call["input"], indent=2), language="json")
            if call.get("is_error"):
                st.caption(call.get("result", ""))


def error_detail(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except ValueError:
        return resp.text


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        show_tool_calls(msg.get("tool_calls", []))

if prompt := st.chat_input("Ask about Limgrave..."):
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages
        if m.get("ok", True)
    ]
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    tool_calls: list[dict] = []
    ok = False
    with st.chat_message("assistant"), st.spinner("Thinking..."):
        try:
            resp = httpx.post(
                f"{BACKEND_URL}/chat",
                json={"message": prompt, "history": history, "voice": voice},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.is_success:
                data = resp.json()
                reply, tool_calls, ok = data["reply"], data.get("tool_calls", []), True
            else:
                reply = f"Backend error ({resp.status_code}): {error_detail(resp)}"
        except httpx.HTTPError as exc:
            reply = f"Couldn't reach the backend at {BACKEND_URL}: {exc}"
        st.markdown(reply)
        show_tool_calls(tool_calls)

    # Failed turns stay on screen but aren't sent back as history.
    if not ok:
        st.session_state.messages[-1]["ok"] = False
    st.session_state.messages.append(
        {"role": "assistant", "content": reply, "tool_calls": tool_calls, "ok": ok}
    )
