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

@st.cache_data(show_spinner=False)
def model_stats():
    with get_conn() as c:
        return pd.read_sql_query(
            "SELECT model_no, COUNT(*) AS findings, MAX(created_at) AS last_seen FROM findings GROUP BY model_no",
            c,
        )

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
# ---------- management helpers (edit/delete) ----------

def get_finding(fid: int):
    with get_conn() as c:
        df = pd.read_sql_query("SELECT * FROM findings WHERE id=?", c, params=(fid,))
        return df.to_dict("records")[0] if not df.empty else None

def update_finding(fid: int, payload: dict):
    if not payload:
        return
    fields = list(payload.keys())
    sets = ", ".join([f"{k}=?" for k in fields])
    values = [payload[k] for k in fields] + [fid]
    with get_conn() as c:
        c.execute(f"UPDATE findings SET {sets} WHERE id=?", values)
        c.commit()

def delete_finding(fid: int, delete_images: bool = False):
    image_path = None
    extra_json = None
    with get_conn() as c:
        row = c.execute("SELECT image_path, extra FROM findings WHERE id=?", (fid,)).fetchone()
        if not row:
            return
        image_path, extra_json = row
        c.execute("DELETE FROM findings WHERE id=?", (fid,))
        c.commit()
    if delete_images:
        paths = []
        if image_path:
            paths.append(DATA_DIR / str(image_path))
        try:
            j = json.loads(extra_json or "{}")
            for rel in j.get("images", []):
                paths.append(DATA_DIR / str(rel))
        except Exception:
            pass
        for p in paths:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass

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

# 📚 Models index (browse all models and see image directory)
st.subheader("📚 Models index")
stats_df = model_stats()
if (not models_df.empty) or (not stats_df.empty):
    merged = models_df.merge(stats_df, on="model_no", how="left")
    if "findings" not in merged.columns:
        merged["findings"] = 0
    merged["findings"] = merged["findings"].fillna(0).astype(int)
    merged["last_seen"] = merged.get("last_seen", pd.Series(dtype=str))
    merged["image_dir"] = merged["model_no"].apply(lambda m: str((IMG_DIR / m).resolve()))

    filt = st.text_input("Filter models", "", key="filter_models")
    view_df = merged
    if filt.strip():
        s = filt.strip().lower()
        view_df = merged[
            merged["model_no"].str.lower().str.contains(s) | merged["name"].str.lower().str.contains(s)
        ]
    show_cols = ["model_no", "name", "findings", "last_seen", "image_dir"]
    view_df = view_df[show_cols].sort_values(by=["findings", "model_no"], ascending=[False, True])
    st.dataframe(view_df, use_container_width=True)

    c_m1, c_m2, c_m3 = st.columns([3,1,1])
    with c_m1:
        model_pick = st.selectbox("Quick open model", [""] + view_df["model_no"].tolist(), index=0)
    with c_m2:
        if st.button("Open", key="open_model_btn") and model_pick:
            st.session_state["search_model"] = model_pick
            st.rerun()
    with c_m3:
        if st.button("Export index CSV", key="export_models"):
            out = APP_DIR / f"models_index_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            view_df.to_csv(out, index=False)
            st.success(f"Exported: {out}")
else:
    st.caption("No models yet. Add one in the sidebar or create findings to populate this list.")

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

# --- Manage buttons ---
rid = int(r["id"])
b1, b2 = st.columns([1,1])
with b1:
    if st.button("✏️ Edit", key=f"edit_{rid}"):
        st.session_state["edit_id"] = rid
with b2:
    if st.button("🗑️ Delete", key=f"del_{rid}"):
        st.session_state[f"confirm_del_{rid}"] = True

# Delete confirm
if st.session_state.get(f"confirm_del_{rid}"):
    with st.expander("Confirm delete?", expanded=True):
        del_imgs = st.checkbox("Also delete image files", value=False, key=f"delimgs_{rid}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Yes, delete", key=f"yesdel_{rid}"):
                delete_finding(rid, delete_images=del_imgs)
                st.session_state.pop(f"confirm_del_{rid}", None)
                load_findings.clear()
                st.rerun()
        with c2:
            if st.button("Cancel", key=f"canceldel_{rid}"):
                st.session_state.pop(f"confirm_del_{rid}", None)

# Edit form
if st.session_state.get("edit_id") == rid:
    with st.form(key=f"edit_form_{rid}", clear_on_submit=False):
        e_stage = st.selectbox("Stage", STAGES, index=(STAGES.index(r['stage']) if r.get('stage') in STAGES else 0), key=f"e_stage_{rid}")
        station_choices = [""] + STATION_LIST if STATION_LIST else ["DIP-A","DIP-B","DIP-C","SMT","ATE","Packing","Repair","AOI","SPI"]
        try:
            idx_station = ([""] + STATION_LIST).index(r.get('station','')) if STATION_LIST else station_choices.index(r.get('station',''))
        except ValueError:
            idx_station = 0
        e_station = st.selectbox("Station", station_choices, index=idx_station, key=f"e_station_{rid}")
        e_line = st.text_input("Line", value=str(r.get('line','')), key=f"e_line_{rid}")
        e_shift = st.text_input("Shift", value=str(r.get('shift','')), key=f"e_shift_{rid}")

        cat_names = [f"{c['name']} ({c['code']})" for c in CATEGORIES]
        try:
            default_idx = next(i for i,c in enumerate(CATEGORIES) if c['code'] == str(r.get('defect_code','')))
        except StopIteration:
            default_idx = 0
        e_cat_sel = st.selectbox("Category", cat_names, index=default_idx, key=f"e_cat_{rid}")
        _cat_obj = CATEGORIES[cat_names.index(e_cat_sel)]
        e_category = _cat_obj['name']
        e_defcode = _cat_obj['code']

        sev_choices = ["Minor","Major","Critical"]
        try:
            idx_sev = sev_choices.index(r.get('severity','Major'))
        except ValueError:
            idx_sev = 1
        e_sev = st.selectbox("Severity", sev_choices, index=idx_sev, key=f"e_sev_{rid}")

        e_mo = st.text_input("MO No.", value=str(r.get('mo_no','')), key=f"e_mo_{rid}")
        e_lot = st.text_input("Lot/Batch", value=str(r.get('lot_no','')), key=f"e_lot_{rid}")
        e_sn = st.text_input("SN / Barcode", value=str(r.get('sn','')), key=f"e_sn_{rid}")
        e_reporter = st.text_input("Reporter", value=str(r.get('reporter','')), key=f"e_reporter_{rid}")
        e_desc = st.text_area("Description", value=str(r.get('description','')), key=f"e_desc_{rid}")

        submitted = st.form_submit_button("Save changes")
        if submitted:
            payload = {
                "stage": e_stage, "station": e_station, "line": e_line, "shift": e_shift,
                "category": e_category, "defect_code": e_defcode, "severity": e_sev,
                "mo_no": e_mo, "lot_no": e_lot, "sn": e_sn, "reporter": e_reporter,
                "description": e_desc
            }
            update_finding(rid, payload)
            st.success("Updated")
            st.session_state.pop("edit_id", None)
            load_findings.clear()
            st.rerun()

else:
    st.info("Type a model number above to view history.")


