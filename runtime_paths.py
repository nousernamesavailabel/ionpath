"""Keep public visitors' private files inside their own Streamlit session."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent


def public_mode():
    return os.environ.get('IONPATH_PUBLIC', '').lower() in ('1', 'true', 'yes')


def private_path(relative):
    if not public_mode():
        return ROOT / relative
    import streamlit as st
    key = '_ionpath_private_directory'
    if key not in st.session_state:
        st.session_state[key] = TemporaryDirectory(prefix='ionpath-session-')
    path = Path(st.session_state[key].name) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
