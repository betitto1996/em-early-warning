"""Ask the analyst: grounded chat over the dashboard's data (spec §8.5)."""
import streamlit as st

from core import ai
from core import config as C
from ui.components import md_safe, page_header

page_header("Ask the analyst")
st.caption("Answers use only this dashboard's data.")

SUGGESTED = [
    "Why is Egypt on alert?",
    "Which of my positions is not paying for its risk?",
    "How much of my book is really in Africa?",
    "Nigeria or Egypt: which local bond looks better?",
]
MAX_TURNS = 8

history: list[dict] = st.session_state.setdefault("chat", [])

for turn in history:
    with st.chat_message("user" if turn["role"] == "user" else "assistant"):
        st.markdown(md_safe(turn["content"]))
        if turn.get("notice"):
            st.caption(turn["notice"])

question = None
with st.container(horizontal=True, gap="small"):
    for q in SUGGESTED:
        if st.button(q, key=f"sugg_{q}", type="secondary"):
            question = q

typed = st.chat_input("Ask about countries, markets or your clients' holdings…")
question = typed or question

if question:
    with st.chat_message("user"):
        st.markdown(md_safe(question))
    with st.chat_message("assistant"):
        stream = ai.stream_answer(history, question)
        st.write_stream(md_safe(chunk) for chunk in stream)
        if stream.notice:
            st.caption(stream.notice)
    history.append({"role": "user", "content": question})
    history.append({"role": "model", "content": stream.text.strip(), "notice": stream.notice})
    st.session_state["chat"] = history[-MAX_TURNS * 2:]

if history and st.button("Clear conversation", icon=":material/delete_sweep:", type="tertiary"):
    st.session_state["chat"] = []
    st.rerun()

if not C.get_settings().ai_enabled:
    st.caption("Gemini is off (no key), so answers come from built-in rules over the same data.")
