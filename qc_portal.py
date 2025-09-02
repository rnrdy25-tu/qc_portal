# qc_portal.py (v3) — Streamlit QC Portal
# - Sidebar (Admin): Add/Update Model • Models list (click to search) • Report New Finding
# - Main: Search model -> Past Findings (filters, edit/delete)
# - Operator fields match your Teams List; QA/CAPA fields on edit panel
# - CSV-driven dropdowns in ./config
# - Optional: TEAMS_WEBHOOK_URL, FLOW_WEBHOOK_URL, QC_PORTAL_PASSCODE

import os
import io
import uuid
import json
import base64
import socket
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
from PIL import Image
import requests

# ---------- Paths & constants ----------
APP_DIR = Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
IMG_DIR = DATA_DIR / "images"
CFG_DIR = APP_DIR / "config"
DB_PATH = DATA_DIR / "qc_portal.sqlite3"

DATA_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(parents=True, exist_ok=True)
CFG_DIR.mkdir(exist_ok=True)

# ---------- Env ----------
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "").strip()
FLOW_WEBHOOK_URL = os.getenv("FLOW_WEBHOOK_URL", "").strip()  # Power Automate "When an HTTP request is received"
PORTAL_PASSCODE = os.getenv("QC_PORTAL_PASSCODE", "").strip()

# ---------- Defaults for dropdowns (used if CSV missing) ----------
DEFAULT_STAGES = ["IQC", "IPQC", "FQC", "OQC", "ATE", "Packing"]
DEFAULT_SHIFT = ["Day", "Night", "Other"]
DEFAULT_STATIONS = ["DIP-A", "DIP-B", "DIP-C", "SMT", "AOI", "SPI", "ATE", "Repair", "Packing"]
DEFAULT_SOURCES = ["IPQC", "OQC", "AOI", "Customer", "Supplier", "Internal Audit"]
DEFAULT_DISCOVERY_DEPTS = ["IPQC", "OQC", "QA", "QS", "Production", "Warehouse"]
DEFAULT_RESP_UNITS = ["Production", "Engineering", "Process", "QA", "Supplier"]
DEFAULT_STOCK_WIP = ["Stock", "WIP"]
DEFAULT_OUTFLOW = ["None", "OQC", "Customer"]

DEFAULT_CATEGORIES = [
    {"code": "POL", "name": "Polarity error", "group": "Electrical"},
    {"code": "SHORT", "name": "Short circuit", "group": "Electrical"},
    {"code": "OPEN", "name": "Open solder / Non-wetting", "group": "Soldering"},
    {"code": "MISS", "name": "Missing part", "group": "Material"},
    {"code": "WRONG", "name": "Wrong part/value", "group": "Material"},
    {"code": "TOMB", "name": "Tombstoning", "group": "Soldering"},
    {"code": "BRIDGE", "name": "Solder bridge", "group": "Soldering"},
    {"code": "DISC", "name": "Discoloration", "group": "Cosmetic"},
    {"code": "RESIDUE", "name": "Flux residue", "group": "Cosmetic"},
    {"code": "SCRATCH", "name": "Scratch/Dent", "group": "Cosmetic"},
    {"code": "LABEL", "name": "Label mismatch", "group": "Label"},
    {"code": "OTHER", "name": "Other (describe)", "group": "Other"},
]

# ---------- Utility ----------
def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

LAN_IP = _lan_ip()

# ---------- DB ----------
SCHEMA_FINDINGS = """
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    model_no TEXT,

    -- Operator fields
    station TEXT,
    line TEXT,
    shift TEXT,
    nonconformity_category TEXT,
    description TEXT,
    defective_qty INTEGER,
    inspection_qty INTEGER,
    lot_qty INTEGER,
    stock_or_wip TEXT,
    discovery_dept TEXT,
    source TEXT,
    outflow_stage TEXT,
    defect_group TEXT,
    defect_item TEXT,
    mo_po TEXT,

    reporter TEXT,
    image_path TEXT,
    extra JSON,

    -- QA/CAPA fields
    need_capa INTEGER,
    capa_date TEXT,
    capa_no TEXT,
    customer_or_supplier TEXT,
    judgment TEXT,
    responsibility_unit TEXT,
    unit_head TEXT,
    owner TEXT,
    root_cause TEXT,
    corrective_action TEXT,
    reply_date TEXT,
    days_to_reply INTEGER,
    delay_days INTEGER,
    reply_closed INTEGER,
    results_closed INTEGER,
    results_tracking_unit TEXT,
    occurrences INTEGER,
    remark TEXT,

    detection INTEGER,
    severity INTEGER,
    occurrence INTEGER
);
"""

SCHEMA_MODELS = """
CREATE TABLE IF NOT EXISTS models (
    model_no TEXT PRIMARY KEY,
    name TEXT,
    customer TEXT,
    bucket TEXT
);
"""

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

@st.cache_data(show_spinner=False)
def list_models():
    with get_conn() as c:
        return pd.read_sql_query(
            "SELECT model_no, COALESCE(name,'') AS name, COALESCE(customer,'') AS customer, COALESCE(bucket,'') AS bucket "
            "FROM models ORDER BY model_no",
            c
        )

@st.cache_data(show_spinner=False)
def load_findings(model_no: str, days: int | None = None):
    q = """SELECT * FROM findings WHERE model_no=?"""
    params = [model_no]
    if days:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        q += " AND created_at >= ?"
        params.append(since)
    q += " ORDER BY id DESC"
    with get_conn() as c:
        return pd.read_sql_query(q, c, params=params)

def init_db():
    with get_conn() as c:
        c.execute(SCHEMA_MODELS)
        # add missing columns for models (safe migration)
cols = {row[1] for row in c.execute("PRAGMA table_info(models)").fetchall()}
if "customer" not in cols:
    c.execute("ALTER TABLE models ADD COLUMN customer TEXT")
if "bucket" not in cols:
    c.execute("ALTER TABLE models ADD COLUMN bucket TEXT")
        c.execute(SCHEMA_FINDINGS)
        # Add missing columns on the fly (safe migrations)
        existing = {row[1] for row in c.execute("PRAGMA table_info(findings)").fetchall()}
        expected = [ln.split()[0] for ln in SCHEMA_FINDINGS.split("(")[1].split(")")[0].split(",") if ln.strip() and not ln.strip().startswith("--")]
        for coldef in [ln.strip() for ln in SCHEMA_FINDINGS.splitlines() if ln.strip() and not ln.strip().startswith("--")]:
            if "CREATE TABLE" in coldef or coldef.startswith(")"):
                continue
            parts = coldef.replace(",", "").split()
            col = parts[0]
            typ = parts[1] if len(parts) > 1 else "TEXT"
            if col not in existing:
                c.execute(f"ALTER TABLE findings ADD COLUMN {col} {typ}")
        c.commit()

init_db()

# ---------- Config readers ----------
def read_csv_column(path: Path, col: str) -> list[str]:
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path)
        return [str(x).strip() for x in df.get(col, []) if str(x).strip()]
    except Exception:
        return []

def load_taxonomy():
    categories = DEFAULT_CATEGORIES
    stations = read_csv_column(CFG_DIR / "stations.csv", "station") or DEFAULT_STATIONS
    discovery = read_csv_column(CFG_DIR / "discovery_depts.csv", "dept") or DEFAULT_DISCOVERY_DEPTS
    sources = read_csv_column(CFG_DIR / "sources.csv", "source") or DEFAULT_SOURCES
    resp_units = read_csv_column(CFG_DIR / "responsibility_units.csv", "unit") or DEFAULT_RESP_UNITS

    cat_csv = CFG_DIR / "categories.csv"
    if cat_csv.exists():
        try:
            df = pd.read_csv(cat_csv)
            cats = []
            for _, r in df.iterrows():
                code = str(r.get("code", "")).strip() or "OTHER"
                name = str(r.get("name", "")).strip() or "Unnamed"
                group = str(r.get("group", "")).strip() or "Other"
                cats.append({"code": code, "name": name, "group": group})
            if cats:
                categories = cats
        except Exception:
            pass

    return categories, stations, discovery, sources, resp_units

CATEGORIES, STATION_LIST, DISCOVERY_DEPTS, SOURCES, RESPONSIBILITY_UNITS = load_taxonomy()

# ---------- Helpers ----------
def upsert_model(model_no: str, name: str = ""):
    with get_conn() as c:
        c.execute(
            "INSERT INTO models(model_no, name) VALUES(?, ?) "
            "ON CONFLICT(model_no) DO UPDATE SET name=excluded.name",
            (model_no.strip(), name.strip()),
        )
        c.commit()
def update_model_meta(model_no: str, name: str, customer: str, bucket: str):
    with get_conn() as c:
        c.execute(
            "UPDATE models SET name=?, customer=?, bucket=? WHERE model_no=?",
            (name.strip(), customer.strip(), bucket.strip(), model_no.strip())
        )
        c.commit()

def rename_model(old_no: str, new_no: str, move_images: bool = True):
    old_no, new_no = old_no.strip(), new_no.strip()
    if not old_no or not new_no or old_no == new_no:
        return "Invalid model numbers."

    with get_conn() as c:
        # read old row
        row = c.execute("SELECT model_no, name, customer, bucket FROM models WHERE model_no=?", (old_no,)).fetchone()
        if not row:
            return f"Model {old_no} not found."

        name, customer, bucket = row[1], row[2], row[3]

        # insert/replace new row
        c.execute(
            "INSERT INTO models(model_no, name, customer, bucket) VALUES(?,?,?,?) "
            "ON CONFLICT(model_no) DO UPDATE SET name=excluded.name, customer=excluded.customer, bucket=excluded.bucket",
            (new_no, name or "", customer or "", bucket or "")
        )
        # update foreign keys
        c.execute("UPDATE findings SET model_no=? WHERE model_no=?", (new_no, old_no))
        c.execute("UPDATE criteria SET model_no=? WHERE model_no=?", (new_no, old_no))
        # remove old row
        c.execute("DELETE FROM models WHERE model_no=?", (old_no,))
        c.commit()

    # move image folder
    if move_images:
        src = (IMG_DIR / old_no)
        dst = (IMG_DIR / new_no)
        try:
            if src.exists() and not dst.exists():
                src.rename(dst)
        except Exception:
            # ignore file-system errors, DB is already consistent
            pass
    # clear cache
    list_models.clear()
    return None  # success

def delete_model_all(model_no: str, delete_images: bool = False, delete_findings: bool = True, delete_criteria: bool = True):
    with get_conn() as c:
        if delete_findings:
            c.execute("DELETE FROM findings WHERE model_no=?", (model_no,))
        if delete_criteria:
            c.execute("DELETE FROM criteria WHERE model_no=?", (model_no,))
        c.execute("DELETE FROM models WHERE model_no=?", (model_no,))
        c.commit()
    if delete_images:
        folder = IMG_DIR / model_no
        try:
            if folder.exists():
                # delete all files then the folder
                for p in folder.glob("**/*"):
                    try:
                        p.unlink()
                    except Exception:
                        pass
                try:
                    folder.rmdir()
                except Exception:
                    pass
        except Exception:
            pass
    list_models.clear()

def save_image(model_no: str, uploaded_file) -> str:
    model_folder = IMG_DIR / model_no
    model_folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
    fname = f"{ts}_{uuid.uuid4().hex[:8]}.jpg"
    out_path = model_folder / fname
    img = Image.open(uploaded_file).convert("RGB")
    img.save(out_path, format="JPEG", quality=90)
    return str(out_path.relative_to(DATA_DIR))

def notify_teams(card: dict):
    if not TEAMS_WEBHOOK_URL:
        return
    try:
        requests.post(TEAMS_WEBHOOK_URL, json=card, timeout=8)
    except Exception:
        pass

def post_to_flow(payload: dict, first_image_abs: Path | None):
    if not FLOW_WEBHOOK_URL:
        return
    try:
        b64 = None
        if first_image_abs and first_image_abs.exists():
            with open(first_image_abs, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
        data = payload.copy()
        data["image_base64"] = b64
        requests.post(FLOW_WEBHOOK_URL, json=data, timeout=10)
    except Exception:
        pass

def compute_week_month(ts: str) -> tuple[int, str]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.utcnow()
    week = int(dt.strftime("%V"))
    month = dt.strftime("%Y-%m")
    return week, month

def compute_defect_rate(def_qty, insp_qty) -> float | None:
    try:
        dq = int(def_qty)
        iq = int(insp_qty)
        if iq <= 0:
            return None
        return round((dq / iq) * 100, 3)
    except Exception:
        return None

# ---------- CRUD for findings ----------
def insert_finding(payload: dict):
    with get_conn() as c:
        cols = ", ".join(payload.keys())
        vals = ", ".join(["?"] * len(payload))
        c.execute(f"INSERT INTO findings({cols}) VALUES({vals})", list(payload.values()))
        c.commit()

def update_finding(fid: int, payload: dict):
    if not payload:
        return
    sets = ", ".join([f"{k}=?" for k in payload.keys()])
    with get_conn() as c:
        c.execute(f"UPDATE findings SET {sets} WHERE id=?", list(payload.values()) + [fid])
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
        paths: list[Path] = []
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

# ---------- Auth gate (optional) ----------
def gate():
    if not PORTAL_PASSCODE:
        return
    if st.session_state.get("_authed"):
        return
    st.title("QC Portal – Sign in")
    code = st.text_input("Enter passcode", type="password")
    if st.button("Unlock"):
        if code == PORTAL_PASSCODE:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Wrong passcode")
    st.stop()

# ---------- UI ----------
st.set_page_config(page_title="QC Portal", layout="wide")
gate()

st.title("🔎 QC Portal – History & Reporting")

# helper for sidebar radio
def _set_selected_model():
    picked = st.session_state.get("model_pick")
    if picked:
        st.session_state["search_model"] = picked
        st.session_state["rep_model"] = picked

# Sidebar
with st.sidebar:
    st.header("Admin")

    with st.expander("Add/Update Model"):
        m_no = st.text_input("Model number", key="mno_admin")
        m_name = st.text_input("Name / Nickname (optional)")
        if st.button("Save model"):
            if m_no.strip():
                upsert_model(m_no, m_name)
                list_models.clear()
                st.success("Model saved")
            else:
                st.error("Please enter a model number")

    with st.expander("Models"):
    mdf = list_models()
    if mdf.empty:
        st.caption("No models yet.")
    else:
        # filter by Customer (file server / account)
        customers = ["All"] + sorted([x for x in mdf["customer"].dropna().unique() if str(x).strip()])
        pick_cust = st.selectbox("Customer / Group", customers, index=0, key="cust_filter")
        if pick_cust != "All":
            mdf = mdf[mdf["customer"] == pick_cust]

        # quick filter box
        filt = st.text_input("Filter", "", key="models_filter_sidebar")
        if filt.strip():
            s = filt.lower().strip()
            mdf = mdf[mdf.apply(lambda r: s in (r["model_no"] + " " + r["name"] + " " + r["customer"] + " " + r["bucket"]).lower(), axis=1)]

        # radio list (click fills search box)
        options = mdf["model_no"].tolist()
        label_map = {
            r["model_no"]: f'{r["name"] or r["model_no"]}  •  {r["model_no"]}' + (f'  ({r["customer"]})' if r["customer"] else "")
            for _, r in mdf.iterrows()
        }
        def _on_pick():
            picked = st.session_state.get("model_pick")
            st.session_state["search_model"] = picked
            st.session_state["rep_model"] = picked
        st.radio("Select a model", options=options, key="model_pick", format_func=lambda m: label_map.get(m, m), on_change=_on_pick)

        # management panel for the selected model
        sel = st.session_state.get("model_pick")
        if sel:
            st.markdown("---")
            st.subheader("Manage selected model")

            row = mdf[mdf["model_no"] == sel].iloc[0] if not mdf[mdf["model_no"] == sel].empty else None
            name_val = st.text_input("Display name", value=(row["name"] if row is not None else ""))
            customer_val = st.text_input("Customer / File server", value=(row["customer"] if row is not None else ""))
            bucket_val = st.text_input("Bucket / Group tag", value=(row["bucket"] if row is not None else ""))

            c1, c2 = st.columns(2)
            with c1:
                if st.button("💾 Save name/customer/bucket", key="save_model_meta"):
                    update_model_meta(sel, name_val, customer_val, bucket_val)
                    list_models.clear()
                    st.success("Saved.")
            with c2:
                new_no = st.text_input("Rename model number", value=sel, key="rename_model_no")
                if st.button("✏️ Rename model number", key="btn_rename_model"):
                    err = rename_model(sel, new_no, move_images=True)
                    if err:
                        st.error(err)
                    else:
                        st.success(f"Renamed {sel} → {new_no}")
                        st.session_state["model_pick"] = new_no
                        st.session_state["search_model"] = new_no
                        st.session_state["rep_model"] = new_no
                        st.experimental_rerun()

            st.markdown("#### Danger zone")
            del_find = st.checkbox("Also delete all findings (DB)", value=True, key="del_findings_chk")
            del_imgs = st.checkbox("Also delete image files", value=False, key="del_images_chk")
            del_crit = st.checkbox("Also delete criteria (DB)", value=False, key="del_criteria_chk")
            if st.button("🗑️ Delete this model", key="btn_delete_model"):
                delete_model_all(sel, delete_images=del_imgs, delete_findings=del_find, delete_criteria=del_crit)
                st.success(f"Deleted model {sel}")
                st.session_state.pop("model_pick", None)
                st.session_state.pop("search_model", None)
                st.session_state.pop("rep_model", None)
                st.experimental_rerun()

    with st.expander("Report New Finding", expanded=True):
        rep_model = st.text_input("Model/Part No.", value=st.session_state.get("rep_model", ""))
        col_a, col_b = st.columns(2)
        with col_a:
            station = st.selectbox("Work Station", STATION_LIST)
            line = st.text_input("Line", placeholder="e.g., A / 1")
            shift = st.selectbox("Shift", DEFAULT_SHIFT)
        with col_b:
            cat_names = [f"{c['name']} ({c['code']})" for c in CATEGORIES]
            cat_sel = st.selectbox("Nonconformity", cat_names)
            cat_obj = CATEGORIES[cat_names.index(cat_sel)]
            defect_group = cat_obj["group"]
            defect_item = cat_obj["name"]
            nonconformity_category = defect_item  # alias

        description = st.text_area("Description of Nonconformity")
        col_n, col_i, col_l = st.columns(3)
        with col_n:
            defective_qty = st.number_input("Defective Qty", min_value=0, value=0, step=1)
        with col_i:
            inspection_qty = st.number_input("Inspection Qty", min_value=0, value=0, step=1)
        with col_l:
            lot_qty = st.number_input("Lot Qty", min_value=0, value=0, step=1)

        stock_or_wip = st.selectbox("Stock/WIP", DEFAULT_STOCK_WIP)
        discovery_dept = st.selectbox("Discovery Dept", DISCOVERY_DEPTS)
        source = st.selectbox("Original Source", SOURCES)
        outflow_stage = st.selectbox("Defective Outflow", DEFAULT_OUTFLOW)
        mo_po = st.text_input("MO/PO")
        reporter = st.text_input("Reporter")
        up_img = st.file_uploader("Upload photo(s)", type=["jpg","jpeg","png","bmp","heic"], accept_multiple_files=True)

        if st.button("Save finding", type="primary"):
            if not rep_model.strip():
                st.error("Please provide a Model/Part No.")
            elif not up_img:
                st.error("Please attach at least one photo")
            else:
                upsert_model(rep_model.strip())
                list_models.clear()

                saved_rel = []
                for f in up_img:
                    rel = save_image(rep_model.strip(), f)
                    saved_rel.append(rel)

                payload_db = {
                    "created_at": datetime.utcnow().isoformat(),
                    "model_no": rep_model.strip(),
                    "station": station,
                    "line": line,
                    "shift": shift,
                    "nonconformity_category": nonconformity_category,
                    "description": description,
                    "defective_qty": int(defective_qty),
                    "inspection_qty": int(inspection_qty),
                    "lot_qty": int(lot_qty),
                    "stock_or_wip": stock_or_wip,
                    "discovery_dept": discovery_dept,
                    "source": source,
                    "outflow_stage": outflow_stage,
                    "defect_group": defect_group,
                    "defect_item": defect_item,
                    "mo_po": mo_po,
                    "reporter": reporter,
                    "image_path": saved_rel[0],
                    "extra": json.dumps({"images": saved_rel}),
                    # QA/CAPA defaults
                    "need_capa": 0,
                    "reply_closed": 0,
                    "results_closed": 0,
                }
                insert_finding(payload_db)

                # Notify (optional)
                week, month = compute_week_month(payload_db["created_at"])
                rate = compute_defect_rate(defective_qty, inspection_qty)
                if TEAMS_WEBHOOK_URL:
                    card = {
                        "@type": "MessageCard",
                        "@context": "http://schema.org/extensions",
                        "summary": f"{rep_model} – {nonconformity_category}",
                        "themeColor": "E81123",
                        "title": f"{rep_model} – {nonconformity_category}",
                        "sections": [{
                            "activitySubtitle": f"{station} · Line {line} · Shift {shift}",
                            "text": description or "",
                            "facts": [
                                {"name": "Week", "value": str(week)},
                                {"name": "Month", "value": month},
                                {"name": "Defective/Inspection", "value": f"{defective_qty}/{inspection_qty}"},
                                {"name": "Defect Rate", "value": f"{rate}%" if rate is not None else "-"},
                                {"name": "Reporter", "value": reporter or "-"},
                            ],
                        }],
                    }
                    notify_teams(card)

                if FLOW_WEBHOOK_URL:
                    flow_payload = payload_db.copy()
                    flow_payload["defect_rate"] = rate
                    flow_payload["week"], flow_payload["month"] = week, month
                    first_abs = (DATA_DIR / saved_rel[0]).resolve()
                    post_to_flow(flow_payload, first_abs)

                st.success("Finding saved")
                load_findings.clear()
                st.session_state["search_model"] = rep_model.strip()
                st.rerun()

# Main area: search & history
models_df = list_models()
col1, col2 = st.columns([3, 1])
with col1:
    query = st.text_input("Search model number", value=st.session_state.get("search_model", ""), placeholder="Type model number…", key="search_model")
with col2:
    days_filter = st.selectbox("Show findings from", ["All", "7 days", "30 days", "90 days"])
selected_days = None if days_filter == "All" else int(days_filter.split()[0])

if query:
    model_no = query.strip()
    if model_no and model_no not in models_df["model_no"].tolist():
        upsert_model(model_no)
        list_models.clear()
        
    # --- Show model meta (Customer / Group/Bucket) ---
    mmeta = list_models()   # refresh cached list after potential upsert
    meta_row = mmeta[mmeta["model_no"] == model_no]
    if not meta_row.empty:
        name = (meta_row.iloc[0]["name"] or "").strip()
        cust = (meta_row.iloc[0].get("customer", "") or "").strip()
        buck = (meta_row.iloc[0].get("bucket", "") or "").strip()
        if name:
            st.caption(f"Name: {name}")
        if cust or buck:
            st.caption(f"Customer: {cust or '-'}  •  Group: {buck or '-'}")

    st.subheader("🗂️ Past Findings")
    fdf = load_findings(model_no, selected_days)

    if fdf.empty:
        st.info("No findings yet for this model.")
    else:
        # Filters
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            f_station = st.selectbox("Filter: Station", ["All"] + sorted([x for x in fdf["station"].dropna().unique() if str(x).strip()]))
        with f2:
            f_shift = st.selectbox("Filter: Shift", ["All"] + sorted([x for x in fdf["shift"].dropna().unique() if str(x).strip()]))
        with f3:
            f_category = st.selectbox("Filter: Nonconformity", ["All"] + sorted([x for x in fdf["nonconformity_category"].dropna().unique() if str(x).strip()]))
        with f4:
            f_reporter = st.selectbox("Filter: Reporter", ["All"] + sorted([x for x in fdf["reporter"].dropna().unique() if str(x).strip()]))

        view = fdf.copy()
        if f_station != "All":
            view = view[view["station"] == f_station]
        if f_shift != "All":
            view = view[view["shift"] == f_shift]
        if f_category != "All":
            view = view[view["nonconformity_category"] == f_category]
        if f_reporter != "All":
            view = view[view["reporter"] == f_reporter]

        # Render cards
        for _, r in view.iterrows():
            with st.container(border=True):
                cols = st.columns([1, 3])
                with cols[0]:
                    img_path = DATA_DIR / str(r["image_path"]) if pd.notna(r.get("image_path")) and str(r.get("image_path")).strip() else None
                    if img_path and img_path.exists():
                        st.image(str(img_path), use_container_width=True)

                with cols[1]:
                    # Header
                    week, month = compute_week_month(r.get("created_at", ""))
                    rate = compute_defect_rate(r.get("defective_qty", 0), r.get("inspection_qty", 0))
                    header = f"**{r.get('nonconformity_category','')}** · {r.get('station','')} · Line {r.get('line','')} · Shift {r.get('shift','')}"
                    st.markdown(header)
                    st.caption(
                        f"{r.get('created_at','')} · Week {week} · {month} · Reporter: {r.get('reporter','-')}"
                    )
                    # Quantities
                    qline = []
                    if pd.notna(r.get("defective_qty")): qline.append(f"Defective: {int(r.get('defective_qty') or 0)}")
                    if pd.notna(r.get("inspection_qty")): qline.append(f"Inspection: {int(r.get('inspection_qty') or 0)}")
                    if rate is not None: qline.append(f"Rate: {rate}%")
                    if pd.notna(r.get("lot_qty")) and int(r.get("lot_qty") or 0) > 0: qline.append(f"Lot: {int(r.get('lot_qty') or 0)}")
                    if qline: st.caption(" · ".join(qline))
                    # Description
                    st.write(r.get("description",""))

                    # --- Manage buttons ---
                    rid = int(r["id"])
                    b1, b2 = st.columns([1, 1])
                    with b1:
                        if st.button("✏️ Edit / CAPA", key=f"edit_{rid}"):
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

                    # Edit / CAPA form
                    if st.session_state.get("edit_id") == rid:
                        with st.form(key=f"edit_form_{rid}", clear_on_submit=False):
                            st.markdown("**Operator fields**")
                            col_e1, col_e2, col_e3 = st.columns(3)
                            with col_e1:
                                e_station = st.selectbox("Work Station", STATION_LIST, index=(STATION_LIST.index(r.get("station","")) if r.get("station","") in STATION_LIST else 0), key=f"e_station_{rid}")
                                e_line = st.text_input("Line", value=str(r.get("line","")), key=f"e_line_{rid}")
                            with col_e2:
                                e_shift = st.selectbox("Shift", DEFAULT_SHIFT, index=(DEFAULT_SHIFT.index(r.get("shift","")) if r.get("shift","") in DEFAULT_SHIFT else 0), key=f"e_shift_{rid}")
                                e_stock = st.selectbox("Stock/WIP", DEFAULT_STOCK_WIP, index=(DEFAULT_STOCK_WIP.index(r.get("stock_or_wip","Stock")) if r.get("stock_or_wip","Stock") in DEFAULT_STOCK_WIP else 0), key=f"e_stock_{rid}")
                            with col_e3:
                                e_outflow = st.selectbox("Defective Outflow", DEFAULT_OUTFLOW, index=(DEFAULT_OUTFLOW.index(r.get("outflow_stage","None")) if r.get("outflow_stage","None") in DEFAULT_OUTFLOW else 0), key=f"e_out_{rid}")

                            cat_names = [f"{c['name']} ({c['code']})" for c in CATEGORIES]
                            try:
                                default_idx = next(i for i, c in enumerate(CATEGORIES) if c['name'] == str(r.get("nonconformity_category","")))
                            except StopIteration:
                                default_idx = 0
                            e_cat_sel = st.selectbox("Nonconformity", cat_names, index=default_idx, key=f"e_cat_{rid}")
                            cat_obj = CATEGORIES[cat_names.index(e_cat_sel)]
                            e_defect_group = cat_obj["group"]
                            e_defect_item = cat_obj["name"]
                            e_description = st.text_area("Description of Nonconformity", value=str(r.get("description","")), key=f"e_desc_{rid}")

                            col_q1, col_q2, col_q3 = st.columns(3)
                            with col_q1:
                                e_def_q = st.number_input("Defective Qty", min_value=0, value=int(r.get("defective_qty") or 0), key=f"e_defq_{rid}")
                            with col_q2:
                                e_insp_q = st.number_input("Inspection Qty", min_value=0, value=int(r.get("inspection_qty") or 0), key=f"e_inspq_{rid}")
                            with col_q3:
                                e_lot_q = st.number_input("Lot Qty", min_value=0, value=int(r.get("lot_qty") or 0), key=f"e_lotq_{rid}")

                            col_mo1, col_mo2, col_mo3 = st.columns(3)
                            with col_mo1:
                                e_mopo = st.text_input("MO/PO", value=str(r.get("mo_po","")), key=f"e_mopo_{rid}")
                            with col_mo2:
                                e_disc = st.selectbox("Discovery Dept", DISCOVERY_DEPTS, index=(DISCOVERY_DEPTS.index(r.get("discovery_dept","")) if r.get("discovery_dept","") in DISCOVERY_DEPTS else 0), key=f"e_disc_{rid}")
                            with col_mo3:
                                e_src = st.selectbox("Original Source", SOURCES, index=(SOURCES.index(r.get("source","")) if r.get("source","") in SOURCES else 0), key=f"e_src_{rid}")

                            st.markdown("---")
                            st.markdown("**QA / CAPA**")
                            col_c1, col_c2, col_c3 = st.columns(3)
                            with col_c1:
                                e_need_capa = st.checkbox("Need CAPA?", value=bool(int(r.get("need_capa") or 0)), key=f"e_need_{rid}")
                                e_capa_no = st.text_input("CAPA No.", value=str(r.get("capa_no","")), key=f"e_cno_{rid}")
                                e_resp_unit = st.selectbox("Responsibility Unit", RESPONSIBILITY_UNITS, index=(RESPONSIBILITY_UNITS.index(r.get("responsibility_unit","")) if r.get("responsibility_unit","") in RESPONSIBILITY_UNITS else 0), key=f"e_resp_{rid}")
                            with col_c2:
                                e_capa_date = st.text_input("CAPA Application Date (YYYY-MM-DD)", value=str(r.get("capa_date","")), key=f"e_cdate_{rid}")
                                e_customer = st.text_input("Customer/Supplier", value=str(r.get("customer_or_supplier","")), key=f"e_cust_{rid}")
                                e_unit_head = st.text_input("Unit Head", value=str(r.get("unit_head","")), key=f"e_uhead_{rid}")
                            with col_c3:
                                e_owner = st.text_input("Responsibility (Owner)", value=str(r.get("owner","")), key=f"e_owner_{rid}")
                                e_judgment = st.text_input("Judgment Nonconformity", value=str(r.get("judgment","")), key=f"e_judge_{rid}")
                                e_results_track = st.text_input("Results Tracking Unit", value=str(r.get("results_tracking_unit","")), key=f"e_rtu_{rid}")

                            e_root = st.text_area("Root Cause", value=str(r.get("root_cause","")), key=f"e_root_{rid}")
                            e_action = st.text_area("Corrective Action", value=str(r.get("corrective_action","")), key=f"e_act_{rid}")

                            col_r1, col_r2, col_r3 = st.columns(3)
                            with col_r1:
                                e_reply_date = st.text_input("Reply date (YYYY-MM-DD)", value=str(r.get("reply_date","")), key=f"e_rdate_{rid}")
                                e_reply_closed = st.checkbox("Reply Closed", value=bool(int(r.get("reply_closed") or 0)), key=f"e_rclosed_{rid}")
                            with col_r2:
                                e_results_closed = st.checkbox("Results Closed", value=bool(int(r.get("results_closed") or 0)), key=f"e_resclosed_{rid}")
                                e_occ = st.number_input("Occurrences", min_value=0, value=int(r.get("occurrences") or 0), key=f"e_occ_{rid}")
                            with col_r3:
                                e_detection = st.number_input("Detection (1-10)", min_value=0, max_value=10, value=int(r.get("detection") or 0), key=f"e_det_{rid}")
                                e_severity = st.number_input("Severity (1-10)", min_value=0, max_value=10, value=int(r.get("severity") or 0), key=f"e_sev_{rid}")
                                e_occurrence = st.number_input("Occurrence (1-10)", min_value=0, max_value=10, value=int(r.get("occurrence") or 0), key=f"e_occu_{rid}")

                            e_remark = st.text_input("Remark", value=str(r.get("remark","")), key=f"e_rem_{rid}")

                            submitted = st.form_submit_button("Save changes")
                            if submitted:
                                payload = {
                                    # Operator
                                    "station": e_station, "line": e_line, "shift": e_shift,
                                    "nonconformity_category": e_defect_item, "description": e_description,
                                    "defective_qty": int(e_def_q), "inspection_qty": int(e_insp_q),
                                    "lot_qty": int(e_lot_q), "stock_or_wip": e_stock, "outflow_stage": e_outflow,
                                    "discovery_dept": e_disc, "source": e_src, "mo_po": e_mopo,
                                    "defect_group": e_defect_group, "defect_item": e_defect_item,
                                    # QA/CAPA
                                    "need_capa": int(bool(e_need_capa)), "capa_no": e_capa_no, "capa_date": e_capa_date,
                                    "customer_or_supplier": e_customer, "judgment": e_judgment,
                                    "responsibility_unit": e_resp_unit, "unit_head": e_unit_head, "owner": e_owner,
                                    "root_cause": e_root, "corrective_action": e_action, "reply_date": e_reply_date,
                                    "reply_closed": int(bool(e_reply_closed)), "results_closed": int(bool(e_results_closed)),
                                    "results_tracking_unit": e_results_track, "occurrences": int(e_occ),
                                    "detection": int(e_detection), "severity": int(e_severity), "occurrence": int(e_occurrence),
                                    "remark": e_remark,
                                }
                                # days_to_reply / delay_days recompute
                                try:
                                    if e_capa_date and e_reply_date:
                                        d1 = datetime.fromisoformat(e_capa_date)
                                        d2 = datetime.fromisoformat(e_reply_date)
                                        payload["days_to_reply"] = (d2 - d1).days
                                    elif e_capa_date and not e_reply_date:
                                        d1 = datetime.fromisoformat(e_capa_date)
                                        payload["delay_days"] = (datetime.utcnow() - d1).days
                                except Exception:
                                    pass

                                update_finding(rid, payload)
                                st.success("Updated")
                                st.session_state.pop("edit_id", None)
                                load_findings.clear()
                                st.rerun()
else:
    st.info(f"Type a model number above to view history.  |  LAN: http://{LAN_IP}:8501")



