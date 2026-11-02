"""Streamlit web UI for the research agent.  Run:  streamlit run app.py"""

import ollama
import streamlit as st

import agent

st.set_page_config(page_title="Local Research Agent", page_icon="🔎", layout="centered")

# ---------- Sidebar: settings + history ----------
with st.sidebar:
    st.header("Settings")
    models = agent.installed_models()
    if models:
        default = models.index(agent.DEFAULT_MODEL) if agent.DEFAULT_MODEL in models else 0
        model = st.selectbox("Model", models, index=default)
    else:
        st.warning("Can't reach Ollama. Start the Ollama app, then refresh.")
        model = st.text_input("Model", agent.DEFAULT_MODEL)

    n_results = st.slider("Search results", 3, 10, agent.DEFAULT_RESULTS)
    max_chars = st.select_slider(
        "Max characters per page", [2000, 4000, 6000, 8000, 12000], agent.DEFAULT_MAX_CHARS
    )
    num_ctx = st.select_slider("Context window (tokens)", [8192, 16384, 32768], agent.DEFAULT_NUM_CTX,
                               help="Bigger fits more page text but uses more RAM.")
    think = st.toggle("Let the model think first", value=False, help="Better reasoning, noticeably slower.")

    st.divider()
    st.header("Past research")
    past = agent.saved_reports()
    if not past:
        st.caption("Nothing yet.")
    for path in past[:20]:
        title, _, when = path.stem.rpartition(" - ")
        if st.button(title or path.stem, key=str(path), use_container_width=True, help=when or None):
            st.session_state.shown = (path.name, path.read_text(encoding="utf-8"))


def show_digest(name: str, digest: str):
    st.markdown(digest)
    st.download_button("Download Markdown", digest, file_name=name, mime="text/markdown")


# ---------- Main ----------
st.title("🔎 Local Research Agent")
st.caption("Searches the web, reads the top pages, and summarizes them with a model running on your machine.")

with st.form("ask"):
    query = st.text_input("What do you want to research?", placeholder="e.g. best AI products released this month")
    submitted = st.form_submit_button("Research", type="primary", use_container_width=True)

if submitted and query.strip():
    query = query.strip()
    with st.status("Researching…", expanded=True) as status:
        st.write("Searching DuckDuckGo…")
        try:
            sources = agent.search_web(query, n_results)
        except Exception as e:
            status.update(label="Search failed", state="error")
            st.error(f"Search failed: {e}. DuckDuckGo may be rate-limiting you; wait a minute and retry.")
            st.stop()
        if not sources:
            status.update(label="No results", state="error")
            st.stop()

        for s in agent.fetch_all(sources, max_chars):
            if s.ok:
                st.write(f"✅ [{s.n}] [{s.title or s.url}]({s.url}) · {len(s.text):,} chars")
            else:
                st.write(f"⚠️ [{s.n}] [{s.title or s.url}]({s.url}) · skipped ({s.error}), using snippet")
        readable = sum(s.ok for s in sources)
        status.update(label=f"Read {readable} of {len(sources)} sources", state="complete", expanded=False)

    try:
        with st.spinner(f"Writing digest with {model}…"):
            body = st.write_stream(agent.stream_digest(query, sources, model, num_ctx, think))
    except ConnectionError:
        st.error("Can't reach Ollama. Is the Ollama app running?")
        st.stop()
    except ollama.ResponseError as e:
        st.error(f"Ollama error: {e.error}. Try `ollama pull {model}`.")
        st.stop()

    digest = agent.format_digest(query, body, sources, model)
    path = agent.save_digest(query, digest)
    st.session_state.shown = (path.name, digest)
    st.rerun()  # re-render the saved digest cleanly (with sources + download)

elif "shown" in st.session_state:
    show_digest(*st.session_state.shown)
