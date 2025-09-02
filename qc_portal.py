# qc_portal.py (modified layout, fixed indentation)
# - Admin sidebar still has Add Model / Add Criterion
# - Report New Abnormality moved into the sidebar (Admin area)
# - Main screen (after search) shows ONLY past findings (no count chart)

import os
import io
import uuid
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
from PIL import Image

APP_DIR = Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
IMG_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "qc_portal.sqlite3"

DATA_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(parents=True, exist_ok=True)

# ---------- DB ----------
SCHEMA = {
    "models": """
        CREATE TABLE IF NOT EXISTS models (
            model_no TEXT PRIMARY KEY,
            name TEXT
        );
    """,
    "criteria": """
        CREATE TABLE IF NOT EXISTS criteria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_no TEXT,
            title TEXT,
            description TEXT,
            severity TEXT,
            reference_url TEXT,
            example_image_path TEXT,
            FOREIGN KEY(model_no) REFERENCES models(model_no)
        );
    """,
    "findings": """
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            model_no TEXT,
            station TEXT,
            category TEXT,
            description TEXT,
            reporter TEXT,
            image_path TEXT,
            extra JSON
        );
    """
}

def get_conn():
    return sqlite3.connect(DB_PATH)

@st.cache_data(show_spinner=False)
def list_models():
    with get_conn() as c:
        return pd.read_sql_query("SELECT model_no, COALESCE(name,'') AS name FROM models ORDER BY model_no", c)

@st.cache_data(show_spinner=False)
def load_findings(model_no: str, days: int | None = None):
    q = "SELECT id, created_at, station, category, description, reporter, image_path FROM findings WHERE model_no=?"
    params = [model_no]
    if days:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        q += " AND created_at >= ?"
        params.append(since)
    q += " ORDER BY id DESC"
    with get_conn() as c:
        return pd.read_sql_query(q, c, params=params)

# ---------- setup ----------
def init_db():
    with get_conn() as c:
        for ddl in SCHEMA.values():
            c.execute(ddl)
        c.commit()

init_db()

# ---------- helpers ----------

def save_image(model_no: str, uploaded_file) -> str:
    model_folder = IMG_DIR / model_no
    model_folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    fname = f"{ts}_{uuid.uuid4().hex[:8]}.jpg"
    out_path = model_folder / fname
    img = Image.open(uploaded_file).convert("RGB")
    img.save(out_path, format="JPEG", quality=90)
    return str(out_path.relative_to(DATA_DIR))


def save_finding(model_no: str, station: str, category: str, description: str, reporter: str, image_rel_path: str, extra: dict | None = None):
    with get_conn() as c:
        c.execute(
            "INSERT INTO findings(created_at, model_no, station, category, description, reporter, image_path, extra) VALUES(?,?,?,?,?,?,?,?)",
            (
                datetime.utcnow().isoformat(),
                model_no,
                station.strip(),
                category.strip(),
                description.strip(),
                reporter.strip(),
                image_rel_path,
                json.dumps(extra or {}),
            ),
        )
        c.commit()


def upsert_model(model_no: str, name: str = ""):
    with get_conn() as c:
        c.execute("INSERT INTO models(model_no, name) VALUES(?, ?) ON CONFLICT(model_no) DO UPDATE SET name=excluded.name", (model_no.strip(), name.strip()))
        c.commit()

# ---------- UI ----------
st.set_page_config(page_title="QC Portal", layout="wide")
st.title("🔎 QC Portal – Search model & past findings")

# Sidebar: Admin & Reporting
with st.sidebar:
    st.header("Admin")
    with st.expander("Add/Update Model"):
        m_no = st.text_input("Model number", key="mno_admin")
        m_name = st.text_input("Name / Nickname (optional)")
        if st.button("Save model") and m_no.strip():
            upsert_model(m_no, m_name)
            st.success("Model saved")
            list_models.clear()

    with st.expander("Report New Abnormality / Finding"):
        rep_model = st.text_input("Model number", key="rep_model")
        up_station = st.text_input("Station / Process")
        up_category = st.text_input("Category")
        up_desc = st.text_area("Description")
        up_reporter = st.text_input("Reporter")
        up_img = st.file_uploader("Upload photo(s)", type=["jpg","jpeg","png","bmp","heic"], accept_multiple_files=True)

        if st.button("Save finding", key="save_finding_btn"):
            if not rep_model.strip():
                st.error("Please provide a model number")
            elif not up_img:
                st.error("Please attach at least one photo")
            else:
                saved_paths = []
                for f in up_img:
                    rel = save_image(rep_model.strip(), f)
                    saved_paths.append(rel)
                extra = {"images": saved_paths}
                save_finding(rep_model.strip(), up_station, up_category, up_desc, up_reporter, saved_paths[0], extra)
                st.success("Finding saved")
                load_findings.clear()

# Main search & past findings only
models_df = list_models()

query = st.text_input("Search model number", placeholder="Type model number…")
days_filter = st.selectbox("Show findings from", ["All", "7 days", "30 days", "90 days"])
selected_days = None if days_filter == "All" else int(days_filter.split()[0])

if query:
    model_no = query.strip()
    if model_no not in models_df["model_no"].tolist():
        upsert_model(model_no)
        list_models.clear()

    st.subheader("🗂️ Past Findings")
    fdf = load_findings(model_no, selected_days)
    if fdf.empty:
        st.info("No findings yet for this model.")
    else:
        for _, r in fdf.iterrows():
            with st.container(border=True):
                cols = st.columns([1, 3])
                with cols[0]:
                    img_path = DATA_DIR / str(r["image_path"]) if r["image_path"] else None
                    if img_path and img_path.exists():
                        st.image(str(img_path), use_container_width=True)
                with cols[1]:
                    st.markdown(f"**{r['category']}** · {r['station']}  ")
                    st.caption(f"{r['created_at']} · Reporter: {r['reporter']}")
                    st.write(r["description"])
else:
    st.info("Type a model number above to view history.")
