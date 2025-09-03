# app.py — Quality Portal - Pilot (Sidebar Navigation)
# - Login & roles (Admin/QA/QC)
# - Sidebar nav shows only one section at a time
# - First Piece: department & customer_supplier
# - NC: compact thumbnails; re-ordered fields
# - Import CSV: Browse -> Load -> Map -> Import (no auto-run)
# - Search & View: gated (only loads after Search)
# - Admin pages: Users / Models

import os
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List

import streamlit as st
import pandas as pd
from PIL import Image

# ─────────────────────────────────────────────
# App Style
# ─────────────────────────────────────────────
st.set_page_config(page_title="Quality Portal - Pilot", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:1rem;padding-bottom:2rem;}
h1,h2,h3{font-weight:700}
.smallcap{font-size:0.9rem;color:#6b7280}
.kbd{padding:.05rem .35rem;border:1px solid #ddd;border-bottom-width:2px;border-radius:4px;background:#f8f9fa;}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
# Paths & helpers
# ─────────────────────────────────────────────
def pick_data_dir() -> Path:
    for base in (Path("/mount/data"), Path("/tmp/qc_portal")):
        try:
            base.mkdir(parents=True, exist_ok=True)
            (base/".write_test").write_text("ok", encoding="utf-8")
            return base
        except Exception:
            pass
    raise RuntimeError("No writable dir")

DATA_DIR = pick_data_dir()
IMG_DIR = DATA_DIR / "images"
FP_IMG_DIR = IMG_DIR / "first_piece"
NC_IMG_DIR = IMG_DIR / "nonconformity"
for p in (IMG_DIR, FP_IMG_DIR, NC_IMG_DIR): p.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "qc_portal.sqlite3"

def now_iso() -> str: return datetime.utcnow().isoformat()
def sha256(s: str) -> str: return hashlib.sha256(s.encode("utf-8")).hexdigest()

def save_image(dest: Path, uploaded) -> Optional[str]:
    if not uploaded: return None
    try:
        dest.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        clean = uploaded.name.replace(" ","_")
        outp = dest / f"{ts}_{clean}"
        Image.open(uploaded).convert("RGB").save(outp, format="JPEG", quality=88)
        return str(outp.relative_to(DATA_DIR))
    except Exception:
        return None

# ─────────────────────────────────────────────
# DB schema / init / migrations
# ─────────────────────────────────────────────
SCHEMA_USERS = """
CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  display_name TEXT NOT NULL
);"""
SCHEMA_MODELS = """
CREATE TABLE IF NOT EXISTS models(
  model_no TEXT PRIMARY KEY,
  name TEXT
);"""
SCHEMA_FP = """
CREATE TABLE IF NOT EXISTS first_piece(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  model_no TEXT,
  model_version TEXT,
  sn TEXT,
  mo TEXT,
  department TEXT,
  customer_supplier TEXT,
  reporter TEXT,
  description TEXT,
  top_image_path TEXT,
  bottom_image_path TEXT,
  extra JSON
);"""
SCHEMA_NC = """
CREATE TABLE IF NOT EXISTS nonconf(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  model_no TEXT,
  model_version TEXT,
  sn TEXT,
  mo TEXT,
  reporter TEXT,
  severity TEXT,
  nonconformity TEXT,
  description TEXT,
  customer_supplier TEXT,
  line TEXT,
  work_station TEXT,
  unit_head TEXT,
  responsibility TEXT,
  root_cause TEXT,
  corrective_action TEXT,
  exception_reporters TEXT,
  discovery TEXT,
  origin_sources TEXT,
  defective_item TEXT,
  defective_qty TEXT,
  inspection_qty TEXT,
  lot_qty TEXT,
  image_paths JSON,
  extra JSON
);"""

def get_conn(): return sqlite3.connect(DB_PATH)

def init_db():
    with get_conn() as c:
        c.execute(SCHEMA_USERS)
        c.execute(SCHEMA_MODELS)
        c.execute(SCHEMA_FP)
        c.execute(SCHEMA_NC)
        c.commit()
    ensure_default_admin()
    migrate_columns()

def ensure_default_admin():
    with get_conn() as c:
        n = c.execute("SELECT COUNT(*) FROM users WHERE username='Admin'").fetchone()[0]
        if n == 0:
            c.execute("INSERT INTO users VALUES(?,?,?,?)",
                      ("Admin", sha256("admin1234"), "Admin", "Admin"))
            c.commit()

def has_column(tbl, col) -> bool:
    with get_conn() as c:
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({tbl})")]
    return col in cols

def migrate_columns():
    with get_conn() as c:
        if not has_column("first_piece","department"):
            c.execute("ALTER TABLE first_piece ADD COLUMN department TEXT")
        if not has_column("first_piece","customer_supplier"):
            c.execute("ALTER TABLE first_piece ADD COLUMN customer_supplier TEXT")
        if not has_column("nonconf","customer_supplier"):
            c.execute("ALTER TABLE nonconf ADD COLUMN customer_supplier TEXT")
        if not has_column("nonconf","image_paths"):
            c.execute("ALTER TABLE nonconf ADD COLUMN image_paths JSON")
        c.commit()

# ─────────────────────────────────────────────
# Cached reads
# ─────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def list_users_df() -> pd.DataFrame:
    with get_conn() as c:
        return pd.read_sql("SELECT username, role, display_name FROM users ORDER BY username", c)

@st.cache_data(show_spinner=False)
def list_models_df() -> pd.DataFrame:
    with get_conn() as c:
        return pd.read_sql("SELECT model_no, COALESCE(name,'') AS name FROM models ORDER BY model_no", c)

@st.cache_data(show_spinner=False)
def load_fp_df(f: Dict) -> pd.DataFrame:
    q = "SELECT * FROM first_piece WHERE 1=1"; params=[]
    if f.get("date_from"): q += " AND created_at >= ?"; params.append(f["date_from"])
    if f.get("date_to"):   q += " AND created_at <= ?"; params.append(f["date_to"])
    for col in ["model_no","model_version","sn","mo","customer_supplier","department"]:
        v = (f.get(col) or "").strip()
        if v: q += f" AND " + col + " LIKE ?"; params.append(f"%{v}%")
    with get_conn() as c:
        return pd.read_sql(q + " ORDER BY id DESC LIMIT 500", c, params=params)

@st.cache_data(show_spinner=False)
def load_nc_df(f: Dict) -> pd.DataFrame:
    q = "SELECT * FROM nonconf WHERE 1=1"; params=[]
    if f.get("date_from"): q += " AND created_at >= ?"; params.append(f["date_from"])
    if f.get("date_to"):   q += " AND created_at <= ?"; params.append(f["date_to"])
    for col in ["model_no","model_version","sn","mo","customer_supplier"]:
        v = (f.get(col) or "").strip()
        if v: q += f" AND " + col + " LIKE ?"; params.append(f"%{v}%")
    text = (f.get("text") or "").strip()
    if text:
        q += " AND (reporter LIKE ? OR severity LIKE ? OR description LIKE ? OR nonconformity LIKE ?)"
        params += [f"%{text}%"]*4
    with get_conn() as c:
        return pd.read_sql(q + " ORDER BY id DESC LIMIT 500", c, params=params)

def invalidate_caches():
    list_users_df.clear(); list_models_df.clear()
    load_fp_df.clear(); load_nc_df.clear()

# ─────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────
def do_login():
    st.markdown("## 🔎 Quality Portal - Pilot")
    u = st.text_input("User")
    p = st.text_input("Password", type="password")
    if st.button("Sign in", use_container_width=True):
        with get_conn() as c:
            row = c.execute("SELECT username,password_hash,role,display_name FROM users WHERE username=?", (u,)).fetchone()
        if row and sha256(p) == row[1]:
            st.session_state["auth"] = True
            st.session_state["auth_username"] = row[0]
            st.session_state["auth_role"] = row[2]
            st.session_state["auth_display_name"] = row[3]
            st.success("Welcome"); st.experimental_rerun()
        else:
            st.error("Invalid username or password")

def cur_user():
    return (st.session_state["auth_username"],
            st.session_state["auth_display_name"],
            st.session_state["auth_role"])

# ─────────────────────────────────────────────
# Sections (render)
# ─────────────────────────────────────────────
def section_first_piece():
    st.markdown("## First Piece")
    with st.form("fp_form"):
        a,b,c = st.columns(3)
        with a:
            model = st.text_input("Model (short)")
            version = st.text_input("Model Version")
        with b:
            sn = st.text_input("SN / Barcode")
            mo = st.text_input("MO / Work Order")
        with c:
            dept = st.text_input("Department")
            cs = st.text_input("Customer / Supplier")
        desc = st.text_area("Notes / Description", height=90)
        tcol, bcol = st.columns(2)
        with tcol: top = st.file_uploader("TOP image", type=["jpg","jpeg","png"])
        with bcol: bot = st.file_uploader("BOTTOM image", type=["jpg","jpeg","png"])
        if st.form_submit_button("Save first piece", type="primary"):
            if not model.strip(): st.error("Model required."); return
            top_rel = save_image(FP_IMG_DIR, top)
            bot_rel = save_image(FP_IMG_DIR, bot)
            _, disp, _ = cur_user()
            with get_conn() as c:
                c.execute("""INSERT INTO first_piece
                             (created_at,model_no,model_version,sn,mo,department,customer_supplier,
                              reporter,description,top_image_path,bottom_image_path,extra)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (now_iso(), model.strip(), version.strip(), sn.strip(), mo.strip(),
                           dept.strip(), cs.strip(), disp, desc.strip(), top_rel, bot_rel, json.dumps({})))
                c.commit()
            load_fp_df.clear(); st.success("Saved.")

def section_nonconf():
    st.markdown("## Create Non-Conformity")
    with st.form("nc_form"):
        b1,b2,b3,b4 = st.columns(4)
        with b1: model = st.text_input("Model")
        with b2: version = st.text_input("Model Version")
        with b3: sn = st.text_input("SN / Barcode")
        with b4: mo = st.text_input("MO / Work Order")
        title = st.text_input("Nonconformity")
        desc = st.text_area("Description of Nonconformity", height=90)
        c1,c2,c3,c4 = st.columns(4)
        with c1: cs = st.text_input("Customer/Supplier")
        with c2: line = st.text_input("Line")
        with c3: ws = st.text_input("Work Station")
        with c4: head = st.text_input("Unit Head")
        r1,r2,r3 = st.columns(3)
        with r1: resp = st.text_input("Responsibility")
        with r2: root = st.text_input("Root Cause")
        with r3: corr = st.text_input("Corrective Action")
        e1,e2,e3 = st.columns(3)
        with e1: exr = st.text_input("Exception reporters")
        with e2: disc = st.text_input("Discovery")
        with e3: org = st.text_input("Origil Sources")
        q1,q2,q3 = st.columns(3)
        with q1: d_item = st.text_input("Defective Item")
        with q2: d_qty = st.text_input("Defective Qty")
        with q3: i_qty = st.text_input("Inspection Qty")
        lot = st.text_input("Lot Qty")
        s1,s2 = st.columns([1,3])
        with s1: sev = st.selectbox("Severity", ["Minor","Major","Critical"], index=0)
        with s2: imgs = st.file_uploader("Upload photo(s)", type=["jpg","jpeg","png"], accept_multiple_files=True)
        if st.form_submit_button("Save non-conformity", type="primary"):
            if not title.strip(): st.error("Nonconformity required."); return
            _, disp, _ = cur_user()
            rels=[]
            for f in (imgs or []):
                rel = save_image(NC_IMG_DIR, f)
                if rel: rels.append(rel)
            with get_conn() as c:
                c.execute("""INSERT INTO nonconf
                             (created_at,model_no,model_version,sn,mo,reporter,severity,nonconformity,description,
                              customer_supplier,line,work_station,unit_head,responsibility,root_cause,corrective_action,
                              exception_reporters,discovery,origin_sources,defective_item,defective_qty,inspection_qty,
                              lot_qty,image_paths,extra)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (now_iso(), model.strip(), version.strip(), sn.strip(), mo.strip(), disp, sev, title.strip(),
                           desc.strip(), cs.strip(), line.strip(), ws.strip(), head.strip(), resp.strip(), root.strip(),
                           corr.strip(), exr.strip(), disc.strip(), org.strip(), d_item.strip(), d_qty.strip(),
                           i_qty.strip(), lot.strip(), json.dumps(rels, ensure_ascii=False), json.dumps({})))
                c.commit()
            load_nc_df.clear(); st.success("Saved.")

# CSV field maps for importer
FP_MAP = [
    ("created_at","Date (UTC ISO, optional)"),
    ("model_no","Model/Part No."),
    ("model_version","Model Version"),
    ("sn","SN"),
    ("mo","MO/PO"),
    ("department","Department"),
    ("customer_supplier","Customer/Supplier"),
    ("reporter","Reporter (optional)"),
    ("description","Description"),
]
NC_MAP = [
    ("created_at","Date (UTC ISO, optional)"),
    ("model_no","Model/Part No."),
    ("model_version","Model Version"),
    ("sn","SN"),
    ("mo","MO/PO"),
    ("reporter","Reporter (optional)"),
    ("severity","Severity (Minor/Major/Critical)"),
    ("nonconformity","Nonconformity"),
    ("description","Description of Nonconformity"),
    ("customer_supplier","Customer/Supplier"),
    ("line","Line"),
    ("work_station","Work Station"),
    ("unit_head","Unit Head"),
    ("responsibility","Responsibility"),
    ("root_cause","Root Cause"),
    ("corrective_action","Corrective Action"),
    ("exception_reporters","Exception reporters"),
    ("discovery","Discovery"),
    ("origin_sources","Origil Sources"),
    ("defective_item","Defective Item"),
    ("defective_qty","Defective Qty"),
    ("inspection_qty","Inspection Qty"),
    ("lot_qty","Lot Qty"),
]

def try_read_csv(up) -> Optional[pd.DataFrame]:
    for enc in ["utf-8-sig","utf-8","cp950","big5","cp932","cp936","cp1252"]:
        try:
            up.seek(0); return pd.read_csv(up, encoding=enc)
        except Exception: continue
    return None

def section_import():
    st.markdown("## Import (CSV)")
    target = st.radio("Import into", ["First Piece","Non-Conformities"], horizontal=True)
    up = st.file_uploader("Browse CSV", type=["csv"], key="import_csv")
    if st.button("Load file", type="primary", disabled=(up is None)):
        st.session_state["import_df"] = try_read_csv(up)
        if st.session_state["import_df"] is None:
            st.error("Cannot read file. Try UTF-8/Big5/CP950.")
        else:
            st.success("File loaded. Map columns then click Import.")
    df = st.session_state.get("import_df")
    if isinstance(df, pd.DataFrame):
        st.caption(f"Preview: {df.shape[0]} rows × {df.shape[1]} cols")
        st.dataframe(df.head(20), use_container_width=True, hide_index=True)
        st.markdown("**Map columns**")
        wanted = FP_MAP if target=="First Piece" else NC_MAP
        sel={}
        for k,lbl in wanted:
            default = k if k in df.columns else None
            idx = 1+list(df.columns).index(default) if default else 0
            sel[k]=st.selectbox(lbl, ["(none)"]+list(df.columns), index=idx, key=f"map_{target}_{k}")
        if st.button(f"Import {target}", type="primary"):
            rows=0
            _, disp, _ = cur_user()
            with get_conn() as c:
                for _, r in df.iterrows():
                    def get(colkey):
                        col = sel[colkey]
                        if not col or col=="(none)": return ""
                        v = r[col]
                        return "" if pd.isna(v) else str(v)
                    created = get("created_at") or now_iso()
                    if target=="First Piece":
                        payload=(created,get("model_no"),get("model_version"),get("sn"),get("mo"),
                                 get("department"),get("customer_supplier"),
                                 get("reporter") or disp, get("description"), None, None, json.dumps({}))
                        c.execute("""INSERT INTO first_piece
                                     (created_at,model_no,model_version,sn,mo,department,customer_supplier,
                                      reporter,description,top_image_path,bottom_image_path,extra)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
                    else:
                        payload=(created,get("model_no"),get("model_version"),get("sn"),get("mo"),
                                 get("reporter") or disp, get("severity") or "Minor", get("nonconformity"),
                                 get("description"), get("customer_supplier"), get("line"), get("work_station"),
                                 get("unit_head"), get("responsibility"), get("root_cause"), get("corrective_action"),
                                 get("exception_reporters"), get("discovery"), get("origin_sources"), get("defective_item"),
                                 get("defective_qty"), get("inspection_qty"), get("lot_qty"), json.dumps([]), json.dumps({}))
                        c.execute("""INSERT INTO nonconf
                                     (created_at,model_no,model_version,sn,mo,reporter,severity,nonconformity,description,
                                      customer_supplier,line,work_station,unit_head,responsibility,root_cause,corrective_action,
                                      exception_reporters,discovery,origin_sources,defective_item,defective_qty,inspection_qty,
                                      lot_qty,image_paths,extra)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
                    rows+=1
                c.commit()
            load_fp_df.clear(); load_nc_df.clear()
            st.success(f"Imported {rows} row(s).")

def render_fp_cards(df: pd.DataFrame):
    if df.empty: st.info("No First Piece results."); return
    st.caption(f"{len(df)} record(s)")
    for _, r in df.iterrows():
        with st.container(border=True):
            st.markdown(
                f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} "
                f"| **SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
            )
            st.caption(
                f"🗓 {r['created_at'][:10]} · 👤 {r['reporter']} · "
                f"🏢 Dept: {r.get('department') or '-'} · "
                f"🏷 Customer/Supplier: {r.get('customer_supplier') or '-'}"
            )
            c1,c2 = st.columns(2)
            p_top = (DATA_DIR / str(r.get("top_image_path"))) if r.get("top_image_path") else None
            p_bot = (DATA_DIR / str(r.get("bottom_image_path"))) if r.get("bottom_image_path") else None
            with c1:
                if p_top and p_top.exists(): st.image(str(p_top), caption="TOP", use_container_width=True)
            with c2:
                if p_bot and p_bot.exists(): st.image(str(p_bot), caption="BOTTOM", use_container_width=True)
            if r.get("description"): st.write(r["description"])

def render_nc_cards(df: pd.DataFrame, role: str):
    if df.empty: st.info("No Non-Conformity results."); return
    st.caption(f"{len(df)} record(s)")
    for _, r in df.iterrows():
        with st.container(border=True):
            st.markdown(
                f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} "
                f"| **SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
            )
            st.caption(
                f"🗓 {r['created_at'][:10]} · 👤 {r['reporter']} · "
                f"Severity: **{r['severity'] or '-'}** · "
                f"Customer/Supplier: **{r.get('customer_supplier') or '-'}**"
            )
            if r.get("nonconformity"): st.markdown(f"**{r['nonconformity']}**")
            if r.get("description"): st.write(r["description"])

            # thumbnails (compact)
            rels=[]
            try: rels = json.loads(r.get("image_paths") or "[]")
            except Exception: rels=[]
            if rels:
                row = st.columns(4); i=0
                for rel in rels:
                    p = DATA_DIR / rel
                    if p.exists():
                        with row[i%4]: st.image(str(p), width=210)
                        i+=1

            # brief meta
            fields = [
                ("line","Line"),("work_station","Work Station"),("unit_head","Unit Head"),
                ("responsibility","Responsibility"),("root_cause","Root Cause"),
                ("corrective_action","Corrective Action"),("exception_reporters","Exception reporters"),
                ("discovery","Discovery"),("origin_sources","Origil Sources"),
                ("defective_item","Defective Item"),("defective_qty","Defective Qty"),
                ("inspection_qty","Inspection Qty"),("lot_qty","Lot Qty"),
            ]
            bits=[f"**{lbl}:** {r.get(k)}" for k,lbl in fields if r.get(k)]
            if bits: st.caption(" · ".join(bits))

            if role in ("Admin","QA"):
                if st.button("Delete", key=f"del_nc_{r['id']}"):
                    with get_conn() as c:
                        c.execute("DELETE FROM nonconf WHERE id=?", (int(r["id"]),)); c.commit()
                    load_nc_df.clear(); st.success("Deleted."); st.experimental_rerun()

def section_search_view(role: str):
    st.markdown("## Search & View")
    with st.expander("Filters", expanded=True):
        d1,d2 = st.columns(2)
        with d1: dfrom = st.date_input("Date from", value=None)
        with d2: dto = st.date_input("Date to", value=None)
        r1,r2,r3,r4 = st.columns(4)
        with r1: m = st.text_input("Model")
        with r2: v = st.text_input("Version")
        with r3: sn = st.text_input("SN")
        with r4: mo = st.text_input("MO")
        r5,r6 = st.columns(2)
        with r5: cs = st.text_input("Customer/Supplier")
        with r6: dept = st.text_input("Department (FP)")
        text = st.text_input("Text (NC only; description/reporter/type)")
        run = st.button("Search", type="primary")

    f = {
        "run": run,
        "date_from": dfrom.strftime("%Y-%m-%d") if dfrom else None,
        "date_to": (datetime.combine(dto, datetime.min.time()).strftime("%Y-%m-%dT23:59:59") if dto else None),
        "model_no": m, "model_version": v, "sn": sn, "mo": mo,
        "customer_supplier": cs, "department": dept, "text": text
    }

    if not run:
        st.info("Set filters and click **Search**. Nothing loads by default.")
        return

    st.markdown("### First Piece (results)")
    fp_df = load_fp_df(f); render_fp_cards(fp_df)

    st.markdown("### Non-Conformities (results)")
    nc_df = load_nc_df(f); render_nc_cards(nc_df, role)

    with st.expander("Table view & export"):
        t1, t2 = st.tabs(["First Piece","Non-Conformities"])
        with t1:
            if not fp_df.empty:
                st.dataframe(fp_df, use_container_width=True, hide_index=True)
                st.download_button(
                    "Export First Piece CSV",
                    fp_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"first_piece_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
                    mime="text/csv"
                )
            else: st.info("No rows.")
        with t2:
            if not nc_df.empty:
                st.dataframe(nc_df, use_container_width=True, hide_index=True)
                st.download_button(
                    "Export Non-Conformities CSV",
                    nc_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"nonconf_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv",
                    mime="text/csv"
                )
            else: st.info("No rows.")

def section_models():
    st.markdown("## Models (Admin)")
    st.dataframe(list_models_df(), hide_index=True, use_container_width=True)
    with st.expander("Add / Update Model", expanded=True):
        m = st.text_input("Model")
        nm = st.text_input("Name / Customer (optional)")
        c1,c2=st.columns(2)
        with c1:
            if st.button("Save model"):
                if not m.strip(): st.error("Model required.")
                else:
                    with get_conn() as c:
                        c.execute("""INSERT INTO models(model_no,name) VALUES(?,?)
                                     ON CONFLICT(model_no) DO UPDATE SET name=excluded.name""",
                                  (m.strip(), nm.strip())); c.commit()
                    list_models_df.clear(); st.success("Saved.")
        with c2:
            if st.button("Delete model", type="secondary"):
                if not m.strip(): st.error("Enter model")
                else:
                    with get_conn() as c:
                        c.execute("DELETE FROM models WHERE model_no=?", (m.strip(),)); c.commit()
                    list_models_df.clear(); st.warning("Deleted.")

def section_users():
    st.markdown("## Users (Admin)")
    st.dataframe(list_users_df(), hide_index=True, use_container_width=True)
    with st.expander("Add / Update User", expanded=True):
        nu = st.text_input("Username")
        nd = st.text_input("Display name")
        npw = st.text_input("Password", type="password")
        nr = st.selectbox("Role", ["Admin","QA","QC"], index=2)
        if st.button("Save user"):
            if not nu or not nd or not npw:
                st.error("Complete all fields.")
            else:
                with get_conn() as c:
                    c.execute("""INSERT INTO users(username,password_hash,role,display_name)
                                 VALUES(?,?,?,?)
                                 ON CONFLICT(username) DO UPDATE SET
                                  password_hash=excluded.password_hash,
                                  role=excluded.role,
                                  display_name=excluded.display_name""",
                              (nu, sha256(npw), nr, nd)); c.commit()
                list_users_df.clear(); st.success("User saved.")

# ─────────────────────────────────────────────
# App flow
# ─────────────────────────────────────────────
init_db()
if "auth" not in st.session_state: st.session_state["auth"]=False
if not st.session_state["auth"]:
    do_login(); st.stop()

# Top header
left, right = st.columns([6,1])
with left: st.markdown("## 🔎 Quality Portal — Models, First Piece, Non-Conformities, Search & Import")
with right:
    if st.button("Sign out", use_container_width=True):
        for k in list(st.session_state.keys()):
            if k.startswith("auth"): del st.session_state[k]
        st.session_state["auth"]=False; st.experimental_rerun()

# Sidebar Navigation
_, disp, role = cur_user()
with st.sidebar:
    st.markdown(f"**User:** {disp}  \n**Role:** {role}")
    st.divider()
    nav_items = ["Create First Piece","Create Non-Conformity","Import CSV","Search & View"]
    if role == "Admin":
        nav_items += ["Models (Admin)","Users (Admin)"]
    choice = st.radio("Go to", nav_items, index=3)  # default to Search & View
    st.caption("Use the sidebar to switch sections. The main page shows only the selected section.")

# Show only the selected section
if choice == "Create First Piece":
    section_first_piece()
elif choice == "Create Non-Conformity":
    section_nonconf()
elif choice == "Import CSV":
    section_import()
elif choice == "Search & View":
    section_search_view(role)
elif choice == "Models (Admin)":
    if role != "Admin": st.error("Admin only."); st.stop()
    section_models()
elif choice == "Users (Admin)":
    if role != "Admin": st.error("Admin only."); st.stop()
    section_users()
