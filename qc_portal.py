F# qc\_portal.py — Full rewrite (folders tree, model management, history, edit/delete,

# Excel/CSV import, Teams/Flow hooks, no duplicate Streamlit keys)

import os
import io
import uuid
import json
import base64
import socket
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import streamlit as st
import pandas as pd
from PIL import Image
import requests

# ---------------------------- Paths & setup ----------------------------

from pathlib import Path

# Robust way (works even if __file__ isn't defined in some runtimes)
try:
    APP_DIR = Path(__file__).parent.resolve()
except NameError:
    APP_DIR = Path.cwd()

DATA_DIR = APP_DIR / "data"
IMG_DIR  = DATA_DIR / "images"
CFG_DIR  = APP_DIR / "config"
DB_PATH  = DATA_DIR / "qc_portal.sqlite3"

DATA\_DIR.mkdir(exist\_ok=True)
IMG\_DIR.mkdir(parents=True, exist\_ok=True)
CFG\_DIR.mkdir(exist\_ok=True)

TEAMS\_WEBHOOK\_URL = os.getenv("TEAMS\_WEBHOOK\_URL", "").strip()
FLOW\_WEBHOOK\_URL = os.getenv("FLOW\_WEBHOOK\_URL", "").strip()
PORTAL\_PASSCODE   = os.getenv("QC\_PORTAL\_PASSCODE", "").strip()

def \_lan\_ip() -> str:
try:
s = socket.socket(socket.AF\_INET, socket.SOCK\_DGRAM)
s.connect(("8.8.8.8", 80))
ip = s.getsockname()\[0]
s.close()
return ip
except Exception:
return "127.0.0.1"

LAN\_IP = \_lan\_ip()

# ---------------------------- Taxonomy defaults ----------------------------

DEFAULT\_SHIFT = \["Day", "Night", "Other"]
DEFAULT\_STATIONS = \["DIP-A", "DIP-B", "DIP-C", "SMT", "AOI", "SPI", "ATE", "Repair", "Packing"]
DEFAULT\_SOURCES = \["IPQC", "OQC", "AOI", "Customer", "Supplier", "Internal Audit"]
DEFAULT\_DISCOVERY\_DEPTS = \["IPQC", "OQC", "QA", "QS", "Production", "Warehouse"]
DEFAULT\_RESP\_UNITS = \["Production", "Engineering", "Process", "QA", "Supplier"]
DEFAULT\_STOCK\_WIP = \["Stock", "WIP"]
DEFAULT\_OUTFLOW = \["None", "OQC", "Customer"]

DEFAULT\_CATEGORIES = \[
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

def \_read\_csv\_col(path: Path, col: str) -> List\[str]:
if not path.exists():
return \[]
try:
df = pd.read\_csv(path)
return \[str(x).strip() for x in df.get(col, \[]) if str(x).strip()]
except Exception:
return \[]

def load\_taxonomy():
categories = DEFAULT\_CATEGORIES
stations = \_read\_csv\_col(CFG\_DIR / "stations.csv", "station") or DEFAULT\_STATIONS
discovery = \_read\_csv\_col(CFG\_DIR / "discovery\_depts.csv", "dept") or DEFAULT\_DISCOVERY\_DEPTS
sources = \_read\_csv\_col(CFG\_DIR / "sources.csv", "source") or DEFAULT\_SOURCES
resp\_units = \_read\_csv\_col(CFG\_DIR / "responsibility\_units.csv", "unit") or DEFAULT\_RESP\_UNITS

```
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
```

CATEGORIES, STATION\_LIST, DISCOVERY\_DEPTS, SOURCES, RESPONSIBILITY\_UNITS = load\_taxonomy()

# ---------------------------- Database ----------------------------

SCHEMA\_MODELS = """
CREATE TABLE IF NOT EXISTS models (
model\_no TEXT PRIMARY KEY,
name TEXT,
customer TEXT,
bucket TEXT
);
"""

SCHEMA\_FINDINGS = """
CREATE TABLE IF NOT EXISTS findings (
id INTEGER PRIMARY KEY AUTOINCREMENT,
created\_at TEXT,
model\_no TEXT,

```
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
```

);
"""

SCHEMA\_FOLDERS = """
CREATE TABLE IF NOT EXISTS folders (
name TEXT PRIMARY KEY
);
"""

def get\_conn():
conn = sqlite3.connect(DB\_PATH)
conn.execute("PRAGMA journal\_mode=WAL;")
return conn

def init\_db():
with get\_conn() as c:
c.execute(SCHEMA\_MODELS)
mcols = {row\[1] for row in c.execute("PRAGMA table\_info(models)").fetchall()}
if "customer" not in mcols:
c.execute("ALTER TABLE models ADD COLUMN customer TEXT")
if "bucket" not in mcols:
c.execute("ALTER TABLE models ADD COLUMN bucket TEXT")

```
    c.execute(SCHEMA_FINDINGS)
    fcols = {row[1] for row in c.execute("PRAGMA table_info(findings)").fetchall()}
    add_cols = {
        "line": "TEXT", "shift": "TEXT", "nonconformity_category": "TEXT",
        "defective_qty": "INTEGER", "inspection_qty": "INTEGER", "lot_qty": "INTEGER",
        "stock_or_wip": "TEXT", "discovery_dept": "TEXT", "source": "TEXT",
        "outflow_stage": "TEXT", "defect_group": "TEXT", "defect_item": "TEXT",
        "mo_po": "TEXT", "need_capa": "INTEGER", "capa_date": "TEXT", "capa_no": "TEXT",
        "customer_or_supplier": "TEXT", "judgment": "TEXT", "responsibility_unit": "TEXT",
        "unit_head": "TEXT", "owner": "TEXT", "root_cause": "TEXT", "corrective_action": "TEXT",
        "reply_date": "TEXT", "days_to_reply": "INTEGER", "delay_days": "INTEGER",
        "reply_closed": "INTEGER", "results_closed": "INTEGER", "results_tracking_unit": "TEXT",
        "occurrences": "INTEGER", "remark": "TEXT", "detection": "INTEGER",
        "severity": "INTEGER", "occurrence": "INTEGER",
    }
    for col, typ in add_cols.items():
        if col not in fcols:
            c.execute(f"ALTER TABLE findings ADD COLUMN {col} {typ}")

    c.execute(SCHEMA_FOLDERS)
    c.commit()
```

init\_db()

# ---------------------------- Model helpers ----------------------------

@st.cache\_data(show\_spinner=False)
def list\_models() -> pd.DataFrame:
with get\_conn() as c:
return pd.read\_sql\_query(
"SELECT model\_no, COALESCE(name,'') AS name, COALESCE(customer,'') AS customer, COALESCE(bucket,'') AS bucket FROM models ORDER BY model\_no",
c,
)

@st.cache\_data(show\_spinner=False)
def list\_folders() -> pd.DataFrame:
with get\_conn() as c:
return pd.read\_sql\_query("SELECT name FROM folders ORDER BY name", c)

def upsert\_model(model\_no: str, name: str = "", customer: str = "", bucket: str = ""):
with get\_conn() as c:
c.execute(
"INSERT INTO models(model\_no, name, customer, bucket) VALUES(?,?,?,?) ON CONFLICT(model\_no) DO UPDATE SET name=excluded.name, customer=excluded.customer, bucket=excluded.bucket",
(model\_no.strip(), name.strip(), customer.strip(), bucket.strip()),
)
c.commit()
list\_models.clear()

def update\_model\_meta(model\_no: str, name: str, customer: str, bucket: str):
with get\_conn() as c:
c.execute("UPDATE models SET name=?, customer=?, bucket=? WHERE model\_no=?", (name.strip(), customer.strip(), bucket.strip(), model\_no.strip()))
c.commit()
list\_models.clear()

def rename\_model(old\_no: str, new\_no: str, move\_images: bool = True) -> Optional\[str]:
old\_no, new\_no = old\_no.strip(), new\_no.strip()
if not old\_no or not new\_no or old\_no == new\_no:
return "Invalid model numbers."
with get\_conn() as c:
row = c.execute("SELECT name, customer, bucket FROM models WHERE model\_no=?", (old\_no,)).fetchone()
if not row:
return f"Model {old\_no} not found."
name, customer, bucket = row
c.execute(
"INSERT INTO models(model\_no, name, customer, bucket) VALUES(?,?,?,?) ON CONFLICT(model\_no) DO UPDATE SET name=excluded.name, customer=excluded.customer, bucket=excluded.bucket",
(new\_no, name or "", customer or "", bucket or ""),
)
c.execute("UPDATE findings SET model\_no=? WHERE model\_no=?", (new\_no, old\_no))
try:
c.execute("UPDATE criteria SET model\_no=? WHERE model\_no=?", (new\_no, old\_no))
except Exception:
pass
c.execute("DELETE FROM models WHERE model\_no=?", (old\_no,))
c.commit()
if move\_images:
src, dst = IMG\_DIR / old\_no, IMG\_DIR / new\_no
try:
if src.exists() and not dst.exists():
src.rename(dst)
except Exception:
pass
list\_models.clear()
return None

def delete\_model\_all(model\_no: str, delete\_images: bool = False, delete\_findings: bool = True, delete\_criteria: bool = True):
with get\_conn() as c:
if delete\_findings:
c.execute("DELETE FROM findings WHERE model\_no=?", (model\_no,))
if delete\_criteria:
try:
c.execute("DELETE FROM criteria WHERE model\_no=?", (model\_no,))
except Exception:
pass
c.execute("DELETE FROM models WHERE model\_no=?", (model\_no,))
c.commit()
if delete\_images:
folder = IMG\_DIR / model\_no
if folder.exists():
for p in folder.glob("\*\*/\*"):
try:
p.unlink()
except Exception:
pass
try:
folder.rmdir()
except Exception:
pass
list\_models.clear()

def add\_folder(name: str):
name = (name or "").strip()
if not name:
return
with get\_conn() as c:
c.execute("INSERT OR IGNORE INTO folders(name) VALUES(?)", (name,))
c.commit()
list\_folders.clear()

def rename\_folder(old\_name: str, new\_name: str) -> Optional\[str]:
old\_name, new\_name = (old\_name or "").strip(), (new\_name or "").strip()
if not old\_name or not new\_name or old\_name == new\_name:
return "Invalid folder names."
with get\_conn() as c:
c.execute("UPDATE folders SET name=? WHERE name=?", (new\_name, old\_name))
c.execute("UPDATE models SET bucket=? WHERE bucket=?", (new\_name, old\_name))
c.commit()
list\_folders.clear(); list\_models.clear()
return None

def delete\_folder(name: str) -> Optional\[str]:
name = (name or "").strip()
if not name:
return "Invalid folder."
with get\_conn() as c:
cnt = c.execute("SELECT COUNT(\*) FROM models WHERE bucket=?", (name,)).fetchone()\[0]
if cnt > 0:
return f"Folder has {cnt} model(s). Reassign first."
c.execute("DELETE FROM folders WHERE name=?", (name,))
c.commit()
list\_folders.clear()
return None

# ---------------------------- Findings helpers ----------------------------

@st.cache\_data(show\_spinner=False)
def load\_findings(model\_no: str, days: Optional\[int] = None) -> pd.DataFrame:
q = "SELECT \* FROM findings WHERE model\_no=?"
params = \[model\_no]
if days:
since = (datetime.utcnow() - timedelta(days=days)).isoformat()
q += " AND created\_at >= ?"; params.append(since)
q += " ORDER BY id DESC"
with get\_conn() as c:
return pd.read\_sql\_query(q, c, params=params)

def save\_image(model\_no: str, uploaded\_file) -> str:
model\_folder = IMG\_DIR / model\_no
model\_folder.mkdir(parents=True, exist\_ok=True)
ts = datetime.utcnow().strftime('%Y%m%dT%H%M%S')
fname = f"{ts}\_{uuid.uuid4().hex\[:8]}.jpg"
out\_path = model\_folder / fname
Image.open(uploaded\_file).convert("RGB").save(out\_path, format="JPEG", quality=90)
return str(out\_path.relative\_to(DATA\_DIR))

def insert\_finding(payload: Dict):
with get\_conn() as c:
cols = ", ".join(payload.keys())
vals = ", ".join(\["?"] \* len(payload))
c.execute(f"INSERT INTO findings({cols}) VALUES({vals})", list(payload.values()))
c.commit()

def update\_finding(fid: int, payload: Dict):
if not payload:
return
sets = ", ".join(\[f"{k}=?" for k in payload.keys()])
with get\_conn() as c:
c.execute(f"UPDATE findings SET {sets} WHERE id=?", list(payload.values()) + \[fid])
c.commit()

def delete\_finding(fid: int, delete\_images: bool = False):
image\_path, extra\_json = None, None
with get\_conn() as c:
row = c.execute("SELECT image\_path, extra FROM findings WHERE id=?", (fid,)).fetchone()
if not row:
return
image\_path, extra\_json = row
c.execute("DELETE FROM findings WHERE id=?", (fid,)); c.commit()
if delete\_images:
paths: List\[Path] = \[]
if image\_path:
paths.append(DATA\_DIR / str(image\_path))
try:
j = json.loads(extra\_json or "{}")
for rel in j.get("images", \[]):
paths.append(DATA\_DIR / str(rel))
except Exception:
pass
for p in paths:
try:
if p.exists():
p.unlink()
except Exception:
pass

def notify\_teams(card: dict):
if not TEAMS\_WEBHOOK\_URL:
return
try:
requests.post(TEAMS\_WEBHOOK\_URL, json=card, timeout=8)
except Exception:
pass

def post\_to\_flow(payload: dict, first\_image\_abs: Optional\[Path]):
if not FLOW\_WEBHOOK\_URL:
return
try:
b64 = None
if first\_image\_abs and first\_image\_abs.exists():
with open(first\_image\_abs, "rb") as f:
b64 = base64.b64encode(f.read()).decode("utf-8")
data = payload.copy(); data\["image\_base64"] = b64
requests.post(FLOW\_WEBHOOK\_URL, json=data, timeout=10)
except Exception:
pass

def compute\_week\_month(ts: str):
try:
dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
except Exception:
dt = datetime.utcnow()
return int(dt.strftime("%V")), dt.strftime("%Y-%m")

def compute\_defect\_rate(def\_qty, insp\_qty) -> Optional\[float]:
try:
dq, iq = int(def\_qty), int(insp\_qty)
if iq <= 0:
return None
return round((dq / iq) \* 100, 3)
except Exception:
return None

# ---------------------------- Optional gate ----------------------------

def gate():
if not PORTAL\_PASSCODE:
return
if st.session\_state.get("\_authed"):
return
st.set\_page\_config(page\_title="QC Portal", layout="wide")
st.title("QC Portal – Sign in")
code = st.text\_input("Enter passcode", type="password")
if st.button("Unlock"):
if code == PORTAL\_PASSCODE:
st.session\_state\["\_authed"] = True
st.rerun()
else:
st.error("Wrong passcode")
st.stop()

# ---------------------------- App ----------------------------

st.set\_page\_config(page\_title="QC Portal", layout="wide")
gate()

st.title("🔎 QC Portal – History & Reporting")

# ---------- Sidebar ----------

with st.sidebar:
\# A little CSS polish for tighter sidebar cards/buttons
st.markdown(
""" <style>
div\[data-testid="stSidebar"] .block-container{padding-top:.5rem}
.stButton>button{width:100%;border-radius:10px}
.card{padding:.75rem;border:1px solid #e5e7eb;border-radius:12px;background:#fff}
.badge{background:#eef2ff;color:#3730a3;padding:2px 8px;border-radius:999px;font-size:12px} </style>
""",
unsafe\_allow\_html=True,
)

```
st.header("Admin")

# ---- Add/Update Model ----
with st.expander("Add/Update Model"):
    m_no = st.text_input("Model number", key="adm_model_no")
    m_name = st.text_input("Display name", key="adm_name")
    m_customer = st.text_input("Customer", key="adm_customer")
    m_bucket = st.text_input("Folder (bucket)", key="adm_bucket")
    if st.button("Save model", key="btn_adm_save"):
        if m_no.strip():
            upsert_model(m_no, m_name, m_customer, m_bucket)
            st.success("Model saved")
        else:
            st.error("Please enter a model number")

# ---- Models (Folders tree) ----
with st.expander("Models"):
    # 1) Load data and prepare mdf_v
    mdf = list_models()
    fdf = list_folders()

    st.markdown("**Folders**")
    with st.container():
        col_a, col_b, col_c = st.columns([2, 2, 1])
        with col_a:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            nf = st.text_input("New folder", key="ui_new_folder", placeholder="e.g., SHURE")
            if st.button("➕ Create", key="btn_create_folder"):
                add_folder(nf); st.success("Folder created"); list_folders.clear()
            st.markdown("</div>", unsafe_allow_html=True)
        with col_b:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            existing = [""] + (fdf["name"].tolist() if not fdf.empty else [])
            old = st.selectbox("Rename folder", existing, key="ui_old_folder")
            new = st.text_input("→ New name", key="ui_new_folder_name")
            if st.button("✏️ Rename", key="btn_rename_folder"):
                if old:
                    err = rename_folder(old, new)
                    st.success("Renamed") if not err else st.error(err)
            st.markdown("</div>", unsafe_allow_html=True)
        with col_c:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            to_del = st.selectbox("Delete", (fdf["name"].tolist() if not fdf.empty else []), key="ui_del_folder")
            if st.button("🗑️ Delete", key="btn_delete_folder"):
                err = delete_folder(to_del)
                st.success("Deleted") if not err else st.error(err)
            st.markdown("</div>", unsafe_allow_html=True)

    st.divider()

    # Filter models for the tree
    filt = st.text_input("Filter models", "", key="models_filter_sidebar")
    mdf_v = mdf.copy()
    if "bucket" not in mdf_v.columns:
        mdf_v["bucket"] = ""
    if "customer" not in mdf_v.columns:
        mdf_v["customer"] = ""
    if filt.strip():
        s = filt.lower().strip()
        mdf_v = mdf_v[
            mdf_v.apply(lambda r: s in (f"{r['model_no']} {r['name']} {r['customer']} {r['bucket']}").lower(), axis=1)
        ]

    # Build folder list and counts
    tmp = mdf.copy(); tmp["bucket"] = tmp["bucket"].fillna("").apply(lambda x: x.strip())
    tmp["__folder"] = tmp["bucket"].apply(lambda x: x if x else "Unassigned")
    folder_counts = tmp["__folder"].value_counts().to_dict()

    seen, folder_names = set(), []
    for b in mdf_v["bucket"].fillna("").tolist():
        n = b.strip() if b.strip() else "Unassigned"
        if n not in seen:
            folder_names.append(n); seen.add(n)
    for n in (fdf["name"].tolist() if not fdf.empty else []):
        if n not in seen:
            folder_names.append(n); seen.add(n)

    # Tree UI (no callbacks; write selection to session)
    for i, folder in enumerate(folder_names):
        tag = folder if folder != "Unassigned" else "Unassigned (no folder)"
        count = folder_counts.get(folder, 0)
        with st.expander(f"📁 {tag}  ·  {count} model(s)"):
            if folder == "Unassigned":
                sub = mdf_v[mdf_v["bucket"].fillna("").eq("")]
            else:
                sub = mdf_v[mdf_v["bucket"].fillna("").eq(folder)]
            if sub.empty:
                st.caption("No models here yet.")
            else:
                options = sub["model_no"].tolist()
                label_map = {
                    r["model_no"]: f"{r['name'] or r['model_no']}  •  {r['model_no']}" + (f"  ({r['customer']})" if (r.get('customer') or '').strip() else "")
                    for _, r in sub.iterrows()
                }
                radio_key = f"folder_radio_{i}"
                selected = st.radio("Select a model", options=options, key=radio_key, format_func=lambda m: label_map.get(m, m))
                if selected:
                    st.session_state["search_model"] = selected
                    st.session_state["rep_model"] = selected

    # Manage selected model
    sel = st.session_state.get("search_model") or st.session_state.get("model_pick")
    if sel:
        st.markdown("---")
        st.subheader("Manage selected model")
        row = mdf[mdf["model_no"] == sel]
        row = row.iloc[0] if not row.empty else None

        name_val = st.text_input("Display name", value=(row["name"] if row is not None else ""), key=f"mgr_name_{sel}")
        customer_val = st.text_input("Customer", value=(row["customer"] if row is not None else ""), key=f"mgr_customer_{sel}")
        folder_choices = [""] + (fdf["name"].tolist() if not fdf.empty else [])
        current_folder = row["bucket"] if row is not None else ""
        folder_sel = st.selectbox("Folder", folder_choices, index=(folder_choices.index(current_folder) if current_folder in folder_choices else 0), key=f"mgr_folder_{sel}")

        c1, c2 = st.columns(2)
        with c1:
            if st.button("💾 Save name/customer/folder", key=f"mgr_save_{sel}"):
                update_model_meta(sel, name_val, customer_val, folder_sel)
                st.success("Saved."); list_models.clear()
        with c2:
            new_no = st.text_input("Rename model number", value=sel, key=f"mgr_rename_{sel}")
            if st.button("✏️ Rename model number", key=f"mgr_btn_rename_{sel}"):
                err = rename_model(sel, new_no, move_images=True)
                if err:
                    st.error(err)
                else:
                    st.success(f"Renamed {sel} → {new_no}")
                    st.session_state["search_model"] = new_no
                    st.session_state["rep_model"] = new_no
                    st.rerun()

        st.markdown("#### Danger zone")
        del_find = st.checkbox("Also delete all findings (DB)", value=True, key=f"mgr_del_find_{sel}")
        del_imgs = st.checkbox("Also delete image files", value=False, key=f"mgr_del_imgs_{sel}")
        del_crit = st.checkbox("Also delete criteria (DB)", value=False, key=f"mgr_del_crit_{sel}")
        if st.button("🗑️ Delete this model", key=f"mgr_btn_delete_{sel}"):
            delete_model_all(sel, delete_images=del_imgs, delete_findings=del_find, delete_criteria=del_crit)
            st.success(f"Deleted model {sel}")
            st.session_state.pop("search_model", None)
            st.session_state.pop("model_pick", None)
            st.session_state.pop("rep_model", None)
            st.rerun()

# ---- Report New Finding ----
with st.expander("Report New Finding", expanded=True):
    rep_model = st.text_input("Model/Part No.", value=st.session_state.get("rep_model", ""), key="rf_model")
    col_a, col_b = st.columns(2)
    with col_a:
        station = st.selectbox("Work Station", STATION_LIST, key="rf_station")
        line    = st.text_input("Line", placeholder="e.g., A / 1", key="rf_line")
        shift   = st.selectbox("Shift", DEFAULT_SHIFT, key="rf_shift")
    with col_b:
        cat_names = [f"{c['name']} ({c['code']})" for c in CATEGORIES]
        cat_sel   = st.selectbox("Nonconformity", cat_names, key="rf_cat")
        cat_obj   = CATEGORIES[cat_names.index(cat_sel)]
        defect_group, defect_item = cat_obj["group"], cat_obj["name"]
        nonconformity_category = defect_item

    description = st.text_area("Description of Nonconformity", key="rf_desc")
    col_n, col_i, col_l = st.columns(3)
    with col_n: defective_qty  = st.number_input("Defective Qty",  min_value=0, value=0, step=1, key="rf_defq")
    with col_i: inspection_qty = st.number_input("Inspection Qty", min_value=0, value=0, step=1, key="rf_inspq")
    with col_l: lot_qty        = st.number_input("Lot Qty",        min_value=0, value=0, step=1, key="rf_lotq")

    stock_or_wip   = st.selectbox("Stock/WIP", DEFAULT_STOCK_WIP, key="rf_stock")
    discovery_dept = st.selectbox("Discovery Dept", DISCOVERY_DEPTS, key="rf_disc")
    source         = st.selectbox("Original Source", SOURCES, key="rf_src")
    outflow_stage  = st.selectbox("Defective Outflow", DEFAULT_OUTFLOW, key="rf_outflow")
    mo_po          = st.text_input("MO/PO", key="rf_mopo")
    reporter       = st.text_input("Reporter", key="rf_reporter")
    up_img         = st.file_uploader("Upload photo(s)", type=["jpg","jpeg","png","bmp","heic"], accept_multiple_files=True, key="rf_files")

    if st.button("Save finding", type="primary", key="rf_save_btn"):
        if not rep_model.strip():
            st.error("Please provide a Model/Part No.")
        elif not up_img:
            st.error("Please attach at least one photo")
        else:
            upsert_model(rep_model.strip())
            saved_rel = [save_image(rep_model.strip(), f) for f in up_img]
            payload_db = {
                "created_at": datetime.utcnow().isoformat(), "model_no": rep_model.strip(),
                "station": station, "line": line, "shift": shift,
                "nonconformity_category": nonconformity_category, "description": description,
                "defective_qty": int(defective_qty), "inspection_qty": int(inspection_qty), "lot_qty": int(lot_qty),
                "stock_or_wip": stock_or_wip, "discovery_dept": discovery_dept, "source": source,
                "outflow_stage": outflow_stage, "defect_group": defect_group, "defect_item": defect_item,
                "mo_po": mo_po, "reporter": reporter,
                "image_path": saved_rel[0], "extra": json.dumps({"images": saved_rel}),
                "need_capa": 0, "reply_closed": 0, "results_closed": 0,
            }
            insert_finding(payload_db)

            week, month = compute_week_month(payload_db["created_at"])
            rate = compute_defect_rate(defective_qty, inspection_qty)
            if TEAMS_WEBHOOK_URL:
                card = {
                    "@type": "MessageCard", "@context": "http://schema.org/extensions",
                    "summary": f"{rep_model} – {nonconformity_category}", "themeColor": "E81123",
                    "title": f"{rep_model} – {nonconformity_category}",
                    "sections": [{
                        "activitySubtitle": f"{station} · Line {line} · Shift {shift}",
                        "text": description or "",
                        "facts": [
                            {"name": "Week", "value": str(week)},
                            {"name": "Month", "value": month},
                            {"name": "Def/Inspect", "value": f"{defective_qty}/{inspection_qty}"},
                            {"name": "Rate", "value": f"{rate}%" if rate is not None else "-"},
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

# ---- Import data ----
with st.expander("Import data (Excel/CSV)"):
    st.caption("Upload an Excel (.xlsx) or CSV with findings. Map columns → Import.")
    file = st.file_uploader("Choose file", type=["xlsx", "xls", "csv"], key="imp_file")
    if file is not None:
        if file.name.lower().endswith(".csv"):
            df_in = pd.read_csv(file)
        else:
            try:
                df_in = pd.read_excel(file, engine="openpyxl")
            except ImportError:
                st.error("Excel import needs the 'openpyxl' package. Install it or upload CSV.")
                st.stop()
            except ValueError:
                if file.name.lower().endswith(".xls"):
                    st.error("Legacy .xls detected. Install xlrd==1.2.0 or convert to .xlsx.")
                    st.stop()
                else:
                    raise

        st.write("Detected columns:", list(df_in.columns))

        required_fields = {
            "model_no": "Model number / Part No.",
            "nonconformity_category": "Nonconformity (category name)",
            "description": "Description",
        }
        optional_fields = {
            "created_at": "Created time (ISO or yyyy-mm-dd hh:mm)",
            "station": "Station", "line": "Line", "shift": "Shift",
            "defective_qty": "Defective Qty", "inspection_qty": "Inspection Qty", "lot_qty": "Lot Qty",
            "stock_or_wip": "Stock/WIP", "discovery_dept": "Discovery Dept", "source": "Source",
            "outflow_stage": "Defective Outflow", "defect_group": "Defect Group", "defect_item": "Defect Item",
            "mo_po": "MO/PO", "reporter": "Reporter",
        }

        st.markdown("**Map required fields**")
        map_required = {}
        for k, label in required_fields.items():
            map_required[k] = st.selectbox(label, ["-- select --"] + list(df_in.columns), key=f"map_req_{k}")

        st.markdown("**Map optional fields**")
        map_optional = {}
        for k, label in optional_fields.items():
            map_optional[k] = st.selectbox(label, ["(skip)"] + list(df_in.columns), key=f"map_opt_{k}")

        if st.button("🚚 Import", key="btn_do_import"):
            missing = [k for k, v in map_required.items() if v == "-- select --"]
            if missing:
                st.error(f"Please map required fields: {missing}")
            else:
                inserted = 0
                for _, row in df_in.iterrows():
                    try:
                        model_no = str(row[map_required["model_no"]]).strip()
                        if not model_no:
                            continue
                        upsert_model(model_no)

                        created_at = row[map_optional["created_at"]] if map_optional["created_at"] != "(skip)" else ""
                        if str(created_at).strip() and str(created_at).lower() != "nan":
                            try:
                                dt = pd.to_datetime(created_at)
                                created_at = dt.isoformat()
                            except Exception:
                                created_at = datetime.utcnow().isoformat()
                        else:
                            created_at = datetime.utcnow().isoformat()

                        payload = {
                            "created_at": created_at,
                            "model_no": model_no,
                            "nonconformity_category": str(row[map_required["nonconformity_category"]]),
                            "description": str(row[map_required["description"]]),
                            "image_path": "",
                            "extra": json.dumps({"images": []}),
                            "need_capa": 0, "reply_closed": 0, "results_closed": 0,
                        }
                        for k, colname in map_optional.items():
                            if colname != "(skip)":
                                v = row[colname]
                                if pd.isna(v):
                                    v = ""
                                if k in {"defective_qty", "inspection_qty", "lot_qty"}:
                                    try:
                                        v = int(v or 0)
                                    except Exception:
                                        v = 0
                                payload[k] = v

                        insert_finding(payload)
                        inserted += 1
                    except Exception:
                        pass
                load_findings.clear()
                st.success(f"Imported {inserted} row(s).")
```

# ---------- Main area: search & history ----------

models\_df = list\_models()
col1, col2 = st.columns(\[3, 1])
with col1:
query = st.text\_input("Search model number", value=st.session\_state.get("search\_model", ""), placeholder="Type model number…", key="search\_model")
with col2:
days\_filter = st.selectbox("Show findings from", \["All", "7 days", "30 days", "90 days"], key="hist\_days")
selected\_days = None if days\_filter == "All" else int(days\_filter.split()\[0])

if query:
model\_no = query.strip()
if model\_no and model\_no not in models\_df\["model\_no"].tolist():
upsert\_model(model\_no)

```
meta = list_models()
row = meta[meta["model_no"] == model_no]
if not row.empty:
    name = (row.iloc[0]["name"] or "").strip()
    cust = (row.iloc[0]["customer"] or "").strip()
    buck = (row.iloc[0]["bucket"] or "").strip()
    if name:
        st.caption(f"Name: {name}")
    if cust or buck:
        st.caption(f"Customer: {cust or '-'}  •  Folder: {buck or '-'}")

st.subheader("🗂️ Past Findings")
fdf = load_findings(model_no, selected_days)

if fdf.empty:
    st.info("No findings yet for this model.")
else:
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        f_station  = st.selectbox("Filter: Station",  ["All"] + sorted([x for x in fdf["station"].dropna().unique() if str(x).strip()]), key="flt_station")
    with f2:
        f_shift    = st.selectbox("Filter: Shift",    ["All"] + sorted([x for x in fdf["shift"].dropna().unique() if str(x).strip()]), key="flt_shift")
    with f3:
        f_cat      = st.selectbox("Filter: Category", ["All"] + sorted([x for x in fdf["nonconformity_category"].dropna().unique() if str(x).strip()]), key="flt_cat")
    with f4:
        f_reporter = st.selectbox("Filter: Reporter", ["All"] + sorted([x for x in fdf["reporter"].dropna().unique() if str(x).strip()]), key="flt_reporter")

    view = fdf.copy()
    if f_station  != "All": view = view[view["station"] == f_station]
    if f_shift    != "All": view = view[view["shift"] == f_shift]
    if f_cat      != "All": view = view[view["nonconformity_category"] == f_cat]
    if f_reporter != "All": view = view[view["reporter"] == f_reporter]

    for _, r in view.iterrows():
        with st.container(border=True):
            cols = st.columns([1, 3])
            with cols[0]:
                img_path = DATA_DIR / str(r.get("image_path", "")) if str(r.get("image_path", "")).strip() else None
                if img_path and img_path.exists():
                    st.image(str(img_path), use_container_width=True)
            with cols[1]:
                week, month = compute_week_month(r.get("created_at", ""))
                rate = compute_defect_rate(r.get("defective_qty", 0), r.get("inspection_qty", 0))
                st.markdown(f"**{r.get('nonconformity_category','')}** · {r.get('station','')} · Line {r.get('line','')} · Shift {r.get('shift','')}")
                st.caption(f"{r.get('created_at','')} · Week {week} · {month} · Reporter: {r.get('reporter','-')}")
                qline = []
                if pd.notna(r.get("defective_qty")):  qline.append(f"Defective: {int(r.get('defective_qty') or 0)}")
                if pd.notna(r.get("inspection_qty")): qline.append(f"Inspection: {int(r.get('inspection_qty') or 0)}")
                if rate is not None: qline.append(f"Rate: {rate}%")
                if pd.notna(r.get("lot_qty")) and int(r.get("lot_qty") or 0) > 0: qline.append(f"Lot: {int(r.get('lot_qty') or 0)}")
                if qline: st.caption(" · ".join(qline))
                st.write(r.get("description", ""))

                rid = int(r["id"])
                b1, b2 = st.columns([1, 1])
                with b1:
                    if st.button("✏️ Edit / CAPA", key=f"edit_{rid}"):
                        st.session_state["edit_id"] = rid
                with b2:
                    if st.button("🗑️ Delete", key=f"del_{rid}"):
                        st.session_state[f"confirm_del_{rid}"] = True

                if st.session_state.get(f"confirm_del_{rid}"):
                    with st.expander("Confirm delete?", expanded=True):
                        del_imgs = st.checkbox("Also delete image files", value=False, key=f"delimgs_{rid}")
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button("Yes, delete", key=f"yesdel_{rid}"):
                                delete_finding(rid, delete_images=del_imgs)
                                st.session_state.pop(f"confirm_del_{rid}", None)
                                load_findings.clear(); st.rerun()
                        with c2:
                            if st.button("Cancel", key=f"canceldel_{rid}"):
                                st.session_state.pop(f"confirm_del_{rid}", None)

                if st.session_state.get("edit_id") == rid:
                    with st.form(key=f"edit_form_{rid}", clear_on_submit=False):
                        st.markdown("**Operator fields**")
                        col_e1, col_e2, col_e3 = st.columns(3)
                        with col_e1:
                            e_station = st.selectbox("Work Station", STATION_LIST, index=(STATION_LIST.index(r.get("station", "")) if r.get("station", "") in STATION_LIST else 0), key=f"e_station_{rid}")
                            e_line = st.text_input("Line", value=str(r.get("line", "")), key=f"e_line_{rid}")
                        with col_e2:
                            e_shift = st.selectbox("Shift", DEFAULT_SHIFT, index=(DEFAULT_SHIFT.index(r.get("shift", "")) if r.get("shift", "") in DEFAULT_SHIFT else 0), key=f"e_shift_{rid}")
                            e_stock = st.selectbox("Stock/WIP", DEFAULT_STOCK_WIP, index=(DEFAULT_STOCK_WIP.index(r.get("stock_or_wip", "Stock")) if r.get("stock_or_wip", "Stock") in DEFAULT_STOCK_WIP else 0), key=f"e_stock_{rid}")
                        with col_e3:
                            e_outflow = st.selectbox("Defective Outflow", DEFAULT_OUTFLOW, index=(DEFAULT_OUTFLOW.index(r.get("outflow_stage", "None")) if r.get("outflow_stage", "None") in DEFAULT_OUTFLOW else 0), key=f"e_out_{rid}")

                        cat_names = [f"{c['name']} ({c['code']})" for c in CATEGORIES]
                        try:
                            default_idx = next(i for i, c in enumerate(CATEGORIES) if c["name"] == str(r.get("nonconformity_category", "")))
                        except StopIteration:
                            default_idx = 0
                        e_cat_sel = st.selectbox("Nonconformity", cat_names, index=default_idx, key=f"e_cat_{rid}")
                        cat_obj = CATEGORIES[cat_names.index(e_cat_sel)]
                        e_defect_group, e_defect_item = cat_obj["group"], cat_obj["name"]

                        e_description = st.text_area("Description of Nonconformity", value=str(r.get("description", "")), key=f"e_desc_{rid}")

                        col_q1, col_q2, col_q3 = st.columns(3)
                        with col_q1: e_def_q  = st.number_input("Defective Qty",  min_value=0, value=int(r.get("defective_qty") or 0), key=f"e_defq_{rid}")
                        with col_q2: e_insp_q = st.number_input("Inspection Qty", min_value=0, value=int(r.get("inspection_qty") or 0), key=f"e_inspq_{rid}")
                        with col_q3: e_lot_q  = st.number_input("Lot Qty",        min_value=0, value=int(r.get("lot_qty") or 0), key=f"e_lotq_{rid}")

                        col_mo1, col_mo2, col_mo3 = st.columns(3)
                        with col_mo1: e_mopo = st.text_input("MO/PO", value=str(r.get("mo_po", "")), key=f"e_mopo_{rid}")
                        with col_mo2: e_disc = st.selectbox("Discovery Dept", DISCOVERY_DEPTS, index=(DISCOVERY_DEPTS.index(r.get("discovery_dept", "")) if r.get("discovery_dept", "") in DISCOVERY_DEPTS else 0), key=f"e_disc_{rid}")
                        with col_mo3: e_src  = st.selectbox("Original Source", SOURCES, index=(SOURCES.index(r.get("source", "")) if r.get("source", "") in SOURCES else 0), key=f"e_src_{rid}")

                        st.markdown("---"); st.markdown("**QA / CAPA**")
                        col_c1, col_c2, col_c3 = st.columns(3)
                        with col_c1:
                            e_need_capa = st.checkbox("Need CAPA?", value=bool(int(r.get("need_capa") or 0)), key=f"e_need_{rid}")
                            e_capa_no   = st.text_input("CAPA No.", value=str(r.get("capa_no", "")), key=f"e_cno_{rid}")
                            e_resp_unit = st.selectbox("Responsibility Unit", RESPONSIBILITY_UNITS, index=(RESPONSIBILITY_UNITS.index(r.get("responsibility_unit", "")) if r.get("responsibility_unit", "") in RESPONSIBILITY_UNITS else 0), key=f"e_resp_{rid}")
                        with col_c2:
                            e_capa_date = st.text_input("CAPA Application Date (YYYY-MM-DD)", value=str(r.get("capa_date", "")), key=f"e_cdate_{rid}")
                            e_customer  = st.text_input("Customer/Supplier", value=str(r.get("customer_or_supplier", "")), key=f"e_cust_{rid}")
                            e_unit_head = st.text_input("Unit Head", value=str(r.get("unit_head", "")), key=f"e_uhead_{rid}")
                        with col_c3:
                            e_owner   = st.text_input("Responsibility (Owner)", value=str(r.get("owner", "")), key=f"e_owner_{rid}")
                            e_judgment = st.text_input("Judgment Nonconformity", value=str(r.get("judgment", "")), key=f"e_judge_{rid}")
                            e_results_track = st.text_input("Results Tracking Unit", value=str(r.get("results_tracking_unit", "")), key=f"e_rtu_{rid}")

                        e_root  = st.text_area("Root Cause", value=str(r.get("root_cause", "")), key=f"e_root_{rid}")
                        e_action= st.text_area("Corrective Action", value=str(r.get("corrective_action", "")), key=f"e_act_{rid}")

                        col_r1, col_r2, col_r3 = st.columns(3)
                        with col_r1:
                            e_reply_date  = st.text_input("Reply date (YYYY-MM-DD)", value=str(r.get("reply_date", "")), key=f"e_rdate_{rid}")
                            e_reply_closed= st.checkbox("Reply Closed", value=bool(int(r.get("reply_closed") or 0)), key=f"e_rclosed_{rid}")
                        with col_r2:
                            e_results_closed = st.checkbox("Results Closed", value=bool(int(r.get("results_closed") or 0)), key=f"e_resclosed_{rid}")
                            e_occ            = st.number_input("Occurrences", min_value=0, value=int(r.get("occurrences") or 0), key=f"e_occ_{rid}")
                        with col_r3:
                            e_detection  = st.number_input("Detection (1-10)",  min_value=0, max_value=10, value=int(r.get("detection") or 0), key=f"e_det_{rid}")
                            e_severity   = st.number_input("Severity (1-10)",   min_value=0, max_value=10, value=int(r.get("severity") or 0), key=f"e_sev_{rid}")
                            e_occurrence = st.number_input("Occurrence (1-10)", min_value=0, max_value=10, value=int(r.get("occurrence") or 0), key=f"e_occu_{rid}")

                        e_remark = st.text_input("Remark", value=str(r.get("remark", "")), key=f"e_rem_{rid}")

                        submitted = st.form_submit_button("Save changes")
                        if submitted:
                            payload = {
                                "station": e_station, "line": e_line, "shift": e_shift,
                                "nonconformity_category": e_defect_item, "description": e_description,
                                "defective_qty": int(e_def_q), "inspection_qty": int(e_insp_q), "lot_qty": int(e_lot_q),
                                "stock_or_wip": e_stock, "outflow_stage": e_outflow,
                                "discovery_dept": e_disc, "source": e_src, "mo_po": e_mopo,
                                "defect_group": e_defect_group, "defect_item": e_defect_item,
                                "need_capa": int(bool(e_need_capa)), "capa_no": e_capa_no, "capa_date": e_capa_date,
                                "customer_or_supplier": e_customer, "judgment": e_judgment,
                                "responsibility_unit": e_resp_unit, "unit_head": e_unit_head, "owner": e_owner,
                                "root_cause": e_root, "corrective_action": e_action, "reply_date": e_reply_date,
                                "reply_closed": int(bool(e_reply_closed)), "results_closed": int(bool(e_results_closed)),
                                "results_tracking_unit": e_results_track, "occurrences": int(e_occ),
                                "detection": int(e_detection), "severity": int(e_severity), "occurrence": int(e_occurrence),
                                "remark": e_remark,
                            }
                            try:
                                if e_capa_date and e_reply_date:
                                    d1 = datetime.fromisoformat(e_capa_date); d2 = datetime.fromisoformat(e_reply_date)
                                    payload["days_to_reply"] = (d2 - d1).days
                                elif e_capa_date and not e_reply_date:
                                    d1 = datetime.fromisoformat(e_capa_date)
                                    payload["delay_days"] = (datetime.utcnow() - d1).days
                            except Exception:
                                pass

                            update_finding(rid, payload)
                            st.success("Updated"); st.session_state.pop("edit_id", None)
                            load_findings.clear(); st.rerun()
```

else:
st.info(f"Type a model number above to view history.  |  LAN: http\://{LAN\_IP}:8501")



