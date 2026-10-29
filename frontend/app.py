"""Streamlit chat UI for TarnishedBot.

Run from the project root:
    uv run streamlit run frontend/app.py
"""

import os

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="TarnishedBot", page_icon="🗡️")
st.title("TarnishedBot")
st.caption("Your guide to Limgrave.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask about Limgrave..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    try:
        resp = httpx.post(f"{BACKEND_URL}/chat", json={"message": prompt}, timeout=60)
        resp.raise_for_status()
        reply = resp.json()["reply"]
    except httpx.HTTPError as exc:
        reply = f"Couldn't reach the backend at {BACKEND_URL}: {exc}"

    st.session_state.messages.append({"role": "assistant", "content": reply})
    with st.chat_message("assistant"):
        st.markdown(reply)
