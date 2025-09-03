# Quality Management Portal — mobile-first hub
# Pages: Login → Home tiles → First Piece / Non-Conformity / Search / Import / User Setup / Personal
# Roles: Admin (all + user setup) / QA (can delete & modify) / QC (cannot delete/modify)
# Data lives under /mount/data if available, else /tmp/qmp

import os, io, json, sqlite3, hashlib, textwrap, csv
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd
import streamlit as st
from PIL import Image

# --------------------------------------------------------------------------------------
# Storage (cloud-safe)
# --------------------------------------------------------------------------------------
def data_root() -> Path:
    for p in (Path("/mount/data/qmp"), Path("/tmp/qmp")):
        try:
            p.mkdir(parents=True, exist_ok=True)
            (p / ".w").write_text("ok", encoding="utf-8")
            return p
        except Exception:
            continue
    raise RuntimeError("No writable storage found")

ROOT   = data_root()
DB     = ROOT / "qmp.sqlite3"
IMGDIR = ROOT / "images"
FPDIR  = IMGDIR / "first_piece"
NCDIR  = IMGDIR / "nonconform"
for d in (IMGDIR, FPDIR, NCDIR): d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------------------
# Styling (mobile-ish) + page config
# --------------------------------------------------------------------------------------
st.set_page_config(page_title="Quality Management Portal", page_icon="🔎", layout="wide")

st.markdown("""
<style>
/* Remove the default wide paddings for more app-like feel */
.block-container{padding-top:0.8rem;padding-bottom:0.5rem;max-width:1200px;}
/* Tile buttons */
.qmp-card button{height:120px;width:100%;border-radius:18px;border:0;
background:linear-gradient(180deg,#fff,#f6f7fb);box-shadow:0 8px 20px rgba(0,0,0,0.06);}
.qmp-card .st-emotion-cache-1xarl3l{display:none;} /* hide help tooltip container */
.qmp-banner{
  background: linear-gradient(135deg,#f2f6ff,#e2f0ff 50%,#eafcf2);
  border-radius: 18px;
  padding: 14px 16px 18px 16px;
  margin: 12px 0 8px 0;
}
.qmp-title{font-size:1.25rem;margin:0;color:#083a63;font-weight:700;}
.qmp-sub{opacity:.85;margin-top:4px}
.qmp-brand{font-weight:800;letter-spacing:.3px}
.qmp-foot{
  position:sticky;bottom:0;left:0;right:0;background:#fff;border-top:1px solid #eee;
  padding:.35rem .6rem;z-index:99;
}
.qmp-chip{display:inline-block;padding:.12rem .5rem;border-radius:999px;background:#eef2ff;border:1px solid #dce3ff}
.qmp-cap{font-size:.87rem;opacity:.9}
.qmp-note{font-size:.92rem;opacity:.9}
.qmp-badge{padding:.25rem .6rem;border-radius:10px;background:#f2f6ff;border:1px solid #dee6ff;margin-left:.35rem}
.qmp-pic{border-radius:8px;border:1px solid #eef0f5}
.smalltxt input, .smalltxt textarea, .smalltxt select{font-size:.92rem}
.card-out{border:1px solid #eef0f5;border-radius:14px;padding:.65rem .8rem;margin:.35rem 0}
</style>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------------------
# DB schema
# --------------------------------------------------------------------------------------
SCHEMA = [
    # Users
    """
    CREATE TABLE IF NOT EXISTS users(
      username TEXT PRIMARY KEY,
      pw_hash  TEXT NOT NULL,
      display  TEXT NOT NULL,
      role     TEXT NOT NULL CHECK(role in ('Admin','QA','QC'))
    );""",
    # First piece
    """
    CREATE TABLE IF NOT EXISTS first_piece(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT,
      model_no TEXT,
      model_version TEXT,
      sn TEXT,
      mo TEXT,
      department TEXT,
      customer_supplier TEXT,
      img_top TEXT,
      img_bottom TEXT,
      reporter TEXT
    );""",
    # Non-conformities
    """
    CREATE TABLE IF NOT EXISTS nc(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT,
      model_no TEXT,
      model_version TEXT,
      sn TEXT,
      mo TEXT,
      reporter TEXT,
      severity TEXT,
      description TEXT,
      img TEXT,
      extra JSON
    );""",
]

SALT = "qmp_salt_v1"  # for hashing

def hash_pw(pw: str) -> str:
    return hashlib.sha256((SALT + pw).encode("utf-8")).hexdigest()

def conn():
    return sqlite3.connect(DB, detect_types=sqlite3.PARSE_DECLTYPES)

def init_db():
    with conn() as c:
        for s in SCHEMA: c.execute(s)
        # bootstrap admin if not exists
        cur = c.execute("SELECT 1 FROM users WHERE username='admin'")
        if cur.fetchone() is None:
            c.execute("INSERT INTO users(username,pw_hash,display,role) VALUES(?,?,?,?)",
                      ("admin", hash_pw("admin1234"), "Admin", "Admin"))
        c.commit()

init_db()

# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")

def current_user() -> Optional[Dict[str,Any]]:
    return st.session_state.get("user")

def require_auth() -> bool:
    return "user" in st.session_state

def save_img_to(subdir: Path, uploaded) -> str:
    """Return relative path under ROOT"""
    if not uploaded: return ""
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe = uploaded.name.replace(" ", "_")
    out = subdir / f"{ts}_{safe}"
    im = Image.open(uploaded).convert("RGB")
    im.save(out, format="JPEG", quality=90)
    return str(out.relative_to(ROOT))

def can_delete_modify() -> bool:
    u = current_user()
    return bool(u and (u["role"] in ("Admin","QA")))

def is_admin() -> bool:
    u = current_user()
    return bool(u and u["role"] == "Admin")

def set_page(p: str):
    st.session_state["page"] = p

def get_page() -> str:
    return st.session_state.get("page","HOME")

# --------------------------------------------------------------------------------------
# Auth views
# --------------------------------------------------------------------------------------
def view_login():
    st.markdown(
        '<div class="qmp-banner">'
        '<div class="qmp-title qmp-brand">Quality Management Portal</div>'
        '<div class="qmp-sub">Please sign in to continue</div>'
        '</div>', unsafe_allow_html=True
    )
    with st.form("login", clear_on_submit=False):
        u = st.text_input("User", placeholder="username")
        p = st.text_input("Password", type="password", placeholder="••••••••")
        ok = st.form_submit_button("Sign in", use_container_width=True)
        if ok:
            with conn() as c:
                cur = c.execute("SELECT username,pw_hash,display,role FROM users WHERE username=?", (u,))
                row = cur.fetchone()
            if row and hash_pw(p) == row[1]:
                st.session_state["user"] = {"username": row[0], "display": row[2], "role": row[3]}
                set_page("HOME")
                st.rerun()
            else:
                st.error("Invalid user or password.")

# --------------------------------------------------------------------------------------
# Banner + tiles (Home)
# --------------------------------------------------------------------------------------
def banner():
    user = current_user()
    name = user["display"] if user else ""
    st.markdown(
        f"""
        <div class="qmp-banner">
          <div style="display:flex;align-items:center;gap:.8rem">
            <div style="font-size:40px">🔧</div>
            <div>
              <div class="qmp-title">Quality Management Portal</div>
              <div class="qmp-sub">Hi, <b>{name}</b>. Choose a function below.</div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

def tile(label:str, emoji:str, page:str):
    st.markdown('<div class="qmp-card">', unsafe_allow_html=True)
    if st.button(f"{emoji}\n\n{label}", key=f"tile_{page}"):
        set_page(page); st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

def view_home():
    banner()
    c1,c2,c3 = st.columns(3)
    with c1: tile("First Piece", "📸", "FP")
    with c2: tile("Non-Conformity", "🛑", "NC")
    with c3: tile("Search & Export", "🔍", "SEARCH")
    c4,c5,c6 = st.columns(3)
    with c4: tile("Import CSV", "⬆️", "IMPORT")
    with c5:
        if is_admin():
            tile("User Setup", "👥", "USERS")
        else:
            st.empty()
    with c6: tile("Personal", "👤", "PERSONAL")

# --------------------------------------------------------------------------------------
# First Piece page
# --------------------------------------------------------------------------------------
def page_first_piece():
    banner()
    st.subheader("First Piece")
    st.caption("Save TOP & BOTTOM pictures and basic info. Department and Customer/Supplier are searchable.")

    with st.form("fp_form", clear_on_submit=True):
        colA,colB,colC = st.columns(3)
        with colA:
            model_no = st.text_input("Model (short)")
            sn       = st.text_input("SN / Barcode")
            dept     = st.text_input("Department")
        with colB:
            version  = st.text_input("Model Version")
            mo       = st.text_input("MO / Work Order")
            cs       = st.text_input("Customer / Supplier")
        with colC:
            top_img  = st.file_uploader("TOP image", type=["jpg","jpeg","png"])
            bot_img  = st.file_uploader("BOTTOM image", type=["jpg","jpeg","png"])
        submitted = st.form_submit_button("Save first piece", use_container_width=True)

    if submitted:
        if not model_no:
            st.warning("Model is required.")
        else:
            rel_top = save_img_to(FPDIR, top_img)
            rel_bot = save_img_to(FPDIR, bot_img)
            with conn() as c:
                c.execute("""INSERT INTO first_piece
                    (created_at,model_no,model_version,sn,mo,department,customer_supplier,img_top,img_bottom,reporter)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (now_iso(), model_no.strip(), version.strip(), sn.strip(), mo.strip(),
                     dept.strip(), cs.strip(), rel_top, rel_bot, current_user()["display"]))
                c.commit()
            st.success("Saved.")

# --------------------------------------------------------------------------------------
# Non-Conformity page
# --------------------------------------------------------------------------------------
NC_EXTRA_FIELDS = [
    "customer_supplier","line","work_station","unit_head","responsibility",
    "root_cause","corrective_action","exception_reporters","discovery",
    "origin_sources","defective_item","defective_qty","inspection_qty","lot_qty"
]

def page_nc():
    banner()
    st.subheader("Create Non-Conformity")

    with st.form("nc_form", clear_on_submit=True):
        c1,c2,c3,c4 = st.columns(4)
        with c1:
            model_no = st.text_input("Model")
        with c2:
            version  = st.text_input("Model Version")
        with c3:
            sn       = st.text_input("SN / Barcode")
        with c4:
            mo       = st.text_input("MO / Work Order")

        severity = st.selectbox("Category", ["Minor","Major","Critical"])
        desc     = st.text_area("Description of Nonconformity", height=120)
        # granular categories
        row1 = st.columns(4)
        row2 = st.columns(4)
        row3 = st.columns(4)
        vals = {}
        vals["customer_supplier"] = row1[0].text_input("Customer/Supplier")
        vals["line"]              = row1[1].text_input("Line")
        vals["work_station"]      = row1[2].text_input("Work Station")
        vals["unit_head"]         = row1[3].text_input("Unit Head")
        vals["responsibility"]    = row2[0].text_input("Responsibility")
        vals["root_cause"]        = row2[1].text_input("Root Cause")
        vals["corrective_action"] = row2[2].text_input("Corrective Action")
        vals["exception_reporters"]= row2[3].text_input("Exception reporters")
        vals["discovery"]         = row3[0].text_input("Discovery")
        vals["origin_sources"]    = row3[1].text_input("Origin Sources")
        vals["defective_item"]    = row3[2].text_input("Defective Item")
        qrow = st.columns(3)
        vals["defective_qty"]     = qrow[0].text_input("Defective Qty")
        vals["inspection_qty"]    = qrow[1].text_input("Inspection Qty")
        vals["lot_qty"]           = qrow[2].text_input("Lot Qty")

        img = st.file_uploader("Photo (optional)", type=["jpg","jpeg","png"])
        ok  = st.form_submit_button("Save non-conformity", use_container_width=True)

    if ok:
        rel = save_img_to(NCDIR, img) if img else ""
        with conn() as c:
            c.execute("""INSERT INTO nc
                (created_at,model_no,model_version,sn,mo,reporter,severity,description,img,extra)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (now_iso(), model_no.strip(), version.strip(), sn.strip(), mo.strip(),
                 current_user()["display"], severity, desc.strip(), rel, json.dumps(vals, ensure_ascii=False)))
            c.commit()
        st.success("Saved.")

# --------------------------------------------------------------------------------------
# Search & Export page (does nothing until Search is clicked)
# --------------------------------------------------------------------------------------
def page_search():
    banner()
    st.subheader("Search & Export")

    # ---------- Build Customer/Supplier option list (from both tables) ----------
    cs_opts = set()
    with conn() as c:
        # From first_piece table
        rows = c.execute(
            "SELECT DISTINCT customer_supplier FROM first_piece "
            "WHERE customer_supplier IS NOT NULL AND TRIM(customer_supplier) <> ''"
        ).fetchall()
        cs_opts.update([r[0] for r in rows if r and r[0]])

        # From nc.extra JSON (best-effort)
        rows = c.execute(
            "SELECT extra FROM nc WHERE extra IS NOT NULL AND TRIM(extra) <> '' LIMIT 2000"
        ).fetchall()
    for (ex,) in rows:
        try:
            d = json.loads(ex)
            v = str(d.get("customer_supplier", "")).strip()
            if v:
                cs_opts.add(v)
        except Exception:
            pass

    cs_list = ["(any)"] + sorted(cs_opts)

    # ---------- Filters UI ----------
    st.markdown("##### Filters")
    frow1 = st.columns([1, 1, 1, 1, 1.2])
    model  = frow1[0].text_input("Model contains")
    vers   = frow1[1].text_input("Version contains")
    sn     = frow1[2].text_input("SN contains")
    mo     = frow1[3].text_input("MO contains")
    textin = frow1[4].text_input("Text in description/reporter/type/extra")

    frow2 = st.columns([1, 1, 2])
    cs_pick = frow2[0].selectbox("Customer / Supplier", cs_list)
    scope   = frow2[1].selectbox("Search scope", ["Both", "First Piece only", "Non-Conformity only"])
    limit   = frow2[2].slider("Max records per section", 20, 300, 100, step=20)

    run = st.button("Search", type="primary")

    df_fp, df_nc = None, None
    if run:
        # ---------- FIRST PIECE ----------
        if scope in ("Both", "First Piece only"):
            q  = "SELECT * FROM first_piece WHERE 1=1"
            pa = []
            for col,val in (("model_no",model),("model_version",vers),("sn",sn),("mo",mo)):
                if val:
                    q += f" AND {col} LIKE ?"; pa.append(f"%{val}%")
            if cs_pick and cs_pick != "(any)":
                q += " AND customer_supplier = ?"; pa.append(cs_pick)
            q += f" ORDER BY id DESC LIMIT {int(limit)}"
            with conn() as c:
                df_fp = pd.read_sql_query(q, c, params=pa)

        # ---------- NON-CONFORMITY ----------
        if scope in ("Both", "Non-Conformity only"):
            qn, pn = "SELECT * FROM nc WHERE 1=1", []
            for col,val in (("model_no",model),("model_version",vers),("sn",sn),("mo",mo)):
                if val:
                    qn += f" AND {col} LIKE ?"; pn.append(f"%{val}%")
            if textin:
                qn += " AND (description LIKE ? OR reporter LIKE ? OR severity LIKE ? OR extra LIKE ?)"
                pn += [f"%{textin}%"]*4
            qn += f" ORDER BY id DESC LIMIT {int(limit*2)}"   # grab a bit more for client-side CS filter
            with conn() as c:
                df_nc = pd.read_sql_query(qn, c, params=pn)

            # Apply Customer/Supplier filter from extra JSON client-side
            if df_nc is not None and not df_nc.empty and cs_pick != "(any)":
                def ex_cs(row):
                    try:
                        return (json.loads(row["extra"] or "{}").get("customer_supplier") or "").strip()
                    except Exception:
                        return ""
                df_nc = df_nc.copy()
                df_nc["__cs__"] = df_nc.apply(ex_cs, axis=1)
                df_nc = df_nc[df_nc["__cs__"] == cs_pick].drop(columns=["__cs__"])
                # Keep limit after filtering
                df_nc = df_nc.head(limit)

        st.toast("Search complete.")

    # ---------- Render FIRST PIECE ----------
    if df_fp is not None:
        exp_main = st.expander(f"First Piece results ({len(df_fp)})", expanded=True)
        with exp_main:
            for _,r in df_fp.iterrows():
                with st.container(border=True):
                    cols = st.columns([1,1,4])
                    p_top = ROOT / str(r["img_top"]) if r["img_top"] else None
                    p_bot = ROOT / str(r["img_bottom"]) if r["img_bottom"] else None
                    with cols[0]:
                        if p_top and p_top.exists(): st.image(str(p_top), width=160, caption="TOP", output_format="JPEG")
                    with cols[1]:
                        if p_bot and p_bot.exists(): st.image(str(p_bot), width=160, caption="BOTTOM", output_format="JPEG")
                    with cols[2]:
                        st.markdown(
                            f"**Model:** {r['model_no'] or '-'} "
                            f"| **Version:** {r['model_version'] or '-'} "
                            f"| **SN:** {r['sn'] or '-'} "
                            f"| **MO:** {r['mo'] or '-'}"
                        )
                        st.caption(
                            f"🕒 {r['created_at']}  ·  🧑‍💼 Reporter: {r['reporter']}  "
                            f"·  🏷 Dept: {r['department'] or '-'}  ·  👥 Customer/Supplier: {r['customer_supplier'] or '-'}"
                        )

        # IMPORTANT: the "Table view" expander is OUTSIDE the results expander (no nesting)
        exp_tbl = st.expander("First Piece — Table view & export", expanded=False)
        with exp_tbl:
            st.dataframe(df_fp, use_container_width=True, hide_index=True)
            st.download_button("Download First-Piece CSV",
                               df_fp.to_csv(index=False).encode("utf-8"),
                               "firstpiece_export.csv", "text/csv")

    # ---------- Render NON-CONFORMITY ----------
    if df_nc is not None:
        exp_main = st.expander(f"Non-Conformity results ({len(df_nc)})", expanded=True)
        with exp_main:
            for _,r in df_nc.iterrows():
                with st.container(border=True):
                    c1,c2 = st.columns([1,4])
                    p = ROOT / str(r["img"]) if r["img"] else None
                    with c1:
                        if p and p.exists(): st.image(str(p), width=160, output_format="JPEG")
                    with c2:
                        st.markdown(
                            f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} | "
                            f"**SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
                        )
                        st.caption(f"🕒 {r['created_at']}  ·  🧑‍💼 Reporter: {r['reporter']}  ·  🏷 Category: {r['severity']}")
                        if r["description"]:
                            st.write(r["description"])
                        # compact extra
                        try:
                            extra = json.loads(r["extra"] or "{}")
                            if extra:
                                line = "  ".join([f"**{k.replace('_',' ')}:** {v}" for k,v in extra.items() if str(v).strip()])
                                st.markdown(line)
                        except Exception:
                            pass
                        if can_delete_modify():
                            if st.button("Delete", key=f"del_nc_{r['id']}"):
                                with conn() as c:
                                    c.execute("DELETE FROM nc WHERE id=?", (int(r["id"]),))
                                    c.commit()
                                st.success("Deleted."); st.rerun()

        # Again, table expander OUTSIDE the results expander
        exp_tbl = st.expander("Non-Conformity — Table view & export", expanded=False)
        with exp_tbl:
            st.dataframe(df_nc, use_container_width=True, hide_index=True)
            st.download_button("Download NC CSV",
                               df_nc.to_csv(index=False).encode("utf-8"),
                               "nc_export.csv", "text/csv")

# --------------------------------------------------------------------------------------
# Import CSV page (safe – requires click)
# --------------------------------------------------------------------------------------
NC_IMPORT_MAP = {
  # CSV/Excel column -> DB field (extra for unmapped is allowed)
  "Nonconformity": "severity",
  "Description of Nonconformity": "description",
  "Date": "created_at",
  "Customer/Supplier": ("extra","customer_supplier"),
  "Model/Part No.": "model_no",
  "MO/PO": "mo",
  "Line": ("extra","line"),
  "Work Station": ("extra","work_station"),
  "Unit Head": ("extra","unit_head"),
  "Responsibility": ("extra","responsibility"),
  "Root Cause": ("extra","root_cause"),
  "Corrective Action": ("extra","corrective_action"),
  "Exception reporters": ("extra","exception_reporters"),
  "Discovery": ("extra","discovery"),
  "Origil Sources": ("extra","origin_sources"),
  "Defective Item": ("extra","defective_item"),
  "Defective Outflow": ("extra","defective_outflow"),
  "Defective Qty": ("extra","defective_qty"),
  "Inspection Qty": ("extra","inspection_qty"),
  "Lot Qty": ("extra","lot_qty"),
}

def best_csv_read(file, encoding: str) -> pd.DataFrame:
    # try utf-8, big5, cp950 fallback
    for enc in ([encoding] if encoding else []) + ["utf-8", "utf-8-sig", "big5", "cp950", "latin1"]:
        try:
            return pd.read_csv(file, encoding=enc)
        except Exception:
            file.seek(0)
    # last try with excel
    try:
        return pd.read_excel(file)
    except Exception as e:
        raise e

def page_import():
    banner()
    st.subheader("Import CSV (Non-Conformities)")
    up = st.file_uploader("Upload CSV/Excel", type=["csv","xlsx","xls"])
    enc = st.selectbox("If file has Chinese, try an encoding", ["(auto)","utf-8","utf-8-sig","big5","cp950","latin1"])
    if up:
        if st.button("Preview & Import", type="primary"):
            df = best_csv_read(up, None if enc=="(auto)" else enc)
            st.write("Preview (first 50 rows)")
            st.dataframe(df.head(50), use_container_width=True)
            if st.button("Import now"):
                n=0
                with conn() as c:
                    for _,row in df.iterrows():
                        model = str(row.get("Model/Part No.", "")).strip()
                        mo    = str(row.get("MO/PO","")).strip()
                        created = str(row.get("Date","")).strip() or now_iso()
                        # map
                        severity = str(row.get("Nonconformity","")).strip()
                        desc     = str(row.get("Description of Nonconformity","")).strip()
                        extra={}
                        for k, target in NC_IMPORT_MAP.items():
                            if isinstance(target, tuple):  # extra
                                _, sub = target
                                extra[sub] = row.get(k, "")
                        c.execute("""INSERT INTO nc
                          (created_at,model_no,model_version,sn,mo,reporter,severity,description,img,extra)
                          VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          (created, model, "", "", mo, current_user()["display"], severity, desc, "", json.dumps(extra, ensure_ascii=False)))
                        n+=1
                    c.commit()
                st.success(f"Imported {n} rows.")
    else:
        st.info("Choose a CSV/Excel file, then click **Preview & Import**. Nothing is imported until you click the second button.")

# --------------------------------------------------------------------------------------
# User setup (Admin only)
# --------------------------------------------------------------------------------------
def page_users():
    banner()
    if not is_admin():
        st.warning("Admins only.")
        return
    st.subheader("User Setup")
    with st.form("new_user"):
        u = st.text_input("Username")
        d = st.text_input("Display name")
        r = st.selectbox("Role", ["QC","QA","Admin"])
        p1= st.text_input("Temp password", type="password")
        ok= st.form_submit_button("Create")
    if ok:
        with conn() as c:
            cur = c.execute("SELECT 1 FROM users WHERE username=?", (u,))
            if cur.fetchone():
                st.warning("User already exists.")
            else:
                c.execute("INSERT INTO users(username,pw_hash,display,role) VALUES(?,?,?,?)",
                          (u, hash_pw(p1), d, r))
                c.commit()
                st.success("User created.")
    # list
    with conn() as c:
        df = pd.read_sql_query("SELECT username,display,role FROM users ORDER BY username", c)
    st.dataframe(df, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------------------
# Personal page
# --------------------------------------------------------------------------------------
def page_personal():
    banner()
    u = current_user()
    if not u: return
    st.subheader("Account")
    st.write(f"**User**: {u['username']} • **Display**: {u['display']} • **Role**: {u['role']}")
    with st.form("pwchg", clear_on_submit=True):
        cur = st.text_input("Current password", type="password")
        new1= st.text_input("New password", type="password")
        new2= st.text_input("Repeat new password", type="password")
        save= st.form_submit_button("Change password")
    if save:
        with conn() as c:
            row = c.execute("SELECT pw_hash FROM users WHERE username=?", (u["username"],)).fetchone()
        if not row or row[0] != hash_pw(cur):
            st.error("Current password incorrect.")
        elif len(new1) < 8 or new1 != new2:
            st.error("New password must match and be ≥ 8 characters.")
        else:
            with conn() as c:
                c.execute("UPDATE users SET pw_hash=? WHERE username=?", (hash_pw(new1), u["username"]))
                c.commit()
            st.success("Password changed.")
    st.divider()
    if st.button("Log out", type="secondary"):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()

# --------------------------------------------------------------------------------------
# Footer nav
# --------------------------------------------------------------------------------------
def footer_nav():
    c1,c2,c3 = st.columns([1,1,1])
    with c1:
        if st.button("🏠 Home", use_container_width=True):
            set_page("HOME"); st.rerun()
    with c2:
        if st.button("👤 Personal", use_container_width=True):
            set_page("PERSONAL"); st.rerun()
    with c3:
        if is_admin():
            st.caption("Admin")

# --------------------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------------------
def router():
    if not require_auth():
        view_login()
        return

    page = get_page()
    if page == "HOME":          view_home()
    elif page == "FP":          page_first_piece()
    elif page == "NC":          page_nc()
    elif page == "SEARCH":      page_search()
    elif page == "IMPORT":      page_import()
    elif page == "USERS":       page_users()
    elif page == "PERSONAL":    page_personal()
    else:
        set_page("HOME"); view_home()

    st.markdown('<div class="qmp-foot"></div>', unsafe_allow_html=True)
    footer_nav()

router()

