# Quality Management Portal — complete Streamlit app
# --------------------------------------------------
# Features:
# - Login with roles (Admin/QA/QC)
# - Home menu (banner + tiles)
# - First Piece create (dept, customer/supplier, top/bottom photos)
# - Create Non-Conformity (fields matching provided layout, photo optional)
# - Search & View (date range + filters + cards + optional export)
# - Import CSV/Excel (browse -> preview -> import; mapping to DB)
# - Personal page (change password)
# - User setup (Admin only)

import os, io, json, sqlite3, hashlib, base64
from pathlib import Path
from datetime import datetime, date, timedelta

import streamlit as st
import pandas as pd
from PIL import Image

# --------------------------- Storage (cloud-safe) --------------------------- #
def pick_data_dir() -> Path:
    for root in (Path("/mount/data"), Path("/tmp/qc_portal")):
        try:
            root.mkdir(parents=True, exist_ok=True)
            (root / ".ok").write_text("ok", encoding="utf-8")
            return root
        except Exception:
            pass
    raise RuntimeError("No writable directory found")

ROOT = pick_data_dir()
IMG_DIR = ROOT / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = ROOT / "qm_portal.sqlite3"

# --------------------------- DB & schema ----------------------------------- #
def conn():
    return sqlite3.connect(DB_PATH)

SCHEMA = [
    # users
    """
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE,
      pass_hash TEXT,
      display_name TEXT,
      role TEXT CHECK(role in ('Admin','QA','QC')) NOT NULL
    );
    """,
    # first piece
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
      notes TEXT,
      img_top TEXT,
      img_bottom TEXT,
      reporter TEXT
    );
    """,
    # non-conformity
    """
    CREATE TABLE IF NOT EXISTS nc(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT,
      model_no TEXT,
      model_version TEXT,
      sn TEXT,
      mo TEXT,
      description TEXT,
      severity TEXT,
      reporter TEXT,
      img TEXT,
      extra TEXT
    );
    """,
]

def init_db():
    with conn() as c:
        for s in SCHEMA: c.execute(s)
        # create default admin if missing
        cur = c.execute("SELECT COUNT(*) FROM users WHERE username='Admin'")
        if cur.fetchone()[0] == 0:
            h = hash_pwd("admin1234")
            c.execute(
                "INSERT INTO users(username,pass_hash,display_name,role) VALUES(?,?,?,?)",
                ("Admin", h, "Admin", "Admin")
            )
        c.commit()

# --------------------------- Auth utils ------------------------------------ #
def hash_pwd(pwd: str) -> str:
    return hashlib.sha256(("qmportal::" + pwd).encode("utf-8")).hexdigest()

def get_user(username: str):
    with conn() as c:
        r = c.execute("SELECT id,username,pass_hash,display_name,role FROM users WHERE username=?",
                      (username,)).fetchone()
        if r:
            return {"id": r[0], "username": r[1], "pass_hash": r[2], "display_name": r[3], "role": r[4]}
    return None

def current_user():
    return st.session_state.get("user")

def require_login():
    if "user" not in st.session_state:
        st.session_state["page"] = "LOGIN"
        st.rerun()

def can_delete_modify() -> bool:
    u = current_user()
    return u and (u["role"] in ("Admin", "QA"))

from datetime import datetime
import textwrap

def _parse_date_safe(s: str) -> str | None:
    if not s:
        return None
    s = str(s).strip()
    # Try a few common formats (add more if you need)
    fmts = ["%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except Exception:
            pass
    # last resort: try letting pandas parse if you already use it
    try:
        return pd.to_datetime(s, errors="coerce").strftime("%Y-%m-%d")
    except Exception:
        return None

def display_date_for_row(row: dict) -> str:
    """
    Prefer the CSV's event date (extra.event_date) if present,
    otherwise fall back to created_at.
    """
    extra = {}
    try:
        extra = json.loads(row.get("extra") or "{}")
    except Exception:
        pass

    event_date = extra.get("event_date") or row.get("date")  # tolerate top-level 'date'
    event_date = _parse_date_safe(event_date)
    if event_date:
        return event_date
    # fallback: created_at (already iso)
    created = row.get("created_at")
    if created and isinstance(created, str) and len(created) >= 10:
        return created[:10]
    return ""

# --------------------------- UI helpers ------------------------------------ #
def brand_banner():
    st.markdown("""
    <style>
      .banner{background:linear-gradient(120deg,#e9f3ff 0%,#f7fbff 100%);padding:14px 18px;border-radius:16px;border:1px solid #e9eef5;margin-bottom:14px}
      .brandrow{display:flex;align-items:center;gap:14px}
      .brandrow .logo{font-size:32px}
      .brandrow .title{font-size:18px;font-weight:700;color:#1b2b59;line-height:1.2}
      .sub{font-size:13px;color:#4a5b88}
      .tiles .stButton>button{height:120px;border-radius:18px;border:1px solid #edf0f6;box-shadow:0 1px 6px rgba(0,0,0,.05);font-weight:700}
      .tiles .stButton>button:hover{border-color:#9cc5ff;box-shadow:0 6px 16px rgba(0,0,0,.08)}
    </style>
    """, unsafe_allow_html=True)
    u = current_user()
    disp = u["display_name"] if u else "-"
    st.markdown(f"""
      <div class="banner">
        <div class="brandrow">
          <div class="logo">🏭</div>
          <div>
            <div class="title">Top Union Electronics Corp.</div>
            <div class="sub">Quality Management Portal</div>
          </div>
        </div>
        <div class="sub" style="margin-top:6px;">Hi, <b>{disp}</b> — welcome back!</div>
      </div>
    """, unsafe_allow_html=True)

def save_image(rel_subdir: str, uploaded_file) -> str:
    """Save PIL image under images/<subdir>/... return path relative to ROOT."""
    folder = IMG_DIR / rel_subdir
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_name = uploaded_file.name.replace(" ", "_")
    out_path = folder / f"{ts}_{safe_name}"
    Image.open(uploaded_file).convert("RGB").save(out_path, format="JPEG", quality=90)
    return str(out_path.relative_to(ROOT))

def bottom_nav():
    st.divider()
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("🏠 Home", use_container_width=True):
            st.session_state["page"] = "HOME"
            st.rerun()
    with c2:
        if st.button("👤 Personal", use_container_width=True):
            st.session_state["page"] = "PROFILE"
            st.rerun()
    with c3:
        if st.button("🚪 Logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

# --------------------------- Pages ----------------------------------------- #
def page_login():
    st.set_page_config(page_title="Quality Management Portal", layout="wide")
    st.markdown("""
    <div style="text-align:center;margin-top:10vh">
      <h2>🔐 Quality Management Portal</h2>
      <p style="color:#566;">Please sign in to continue.</p>
    </div>
    """, unsafe_allow_html=True)
    with st.form("login", clear_on_submit=False):
        username = st.text_input("User")
        pwd = st.text_input("Password", type="password")
        col = st.columns([1,1,2])
        ok = col[0].form_submit_button("Login", type="primary", use_container_width=True)
        col[1].form_submit_button("Clear", use_container_width=True)
        if ok:
            u = get_user(username.strip())
            if u and u["pass_hash"] == hash_pwd(pwd):
                st.session_state["user"] = u
                st.session_state["page"] = "HOME"
                st.success("Welcome!")
                st.experimental_rerun()
            else:
                st.error("Invalid credentials.")

def page_home():
    require_login()
    brand_banner()
    st.markdown("### Main Menu")
    cols = st.columns(3, gap="large")
    if cols[0].button("📷 First Piece", use_container_width=True): st.session_state.page="FP"; st.rerun()
    if cols[1].button("🧩 Create NC", use_container_width=True): st.session_state.page="NC"; st.rerun()
    if cols[2].button("🔎 Search & View", use_container_width=True): st.session_state.page="SEARCH"; st.rerun()
    cols2 = st.columns(3, gap="large")
    if cols2[0].button("⬆️ Import CSV/Excel", use_container_width=True): st.session_state.page="IMPORT"; st.rerun()
    if cols2[1].button("👤 Personal", use_container_width=True): st.session_state.page="PROFILE"; st.rerun()
    admin = current_user()["role"] == "Admin"
    if cols2[2].button("⚙️ User Setup", use_container_width=True, disabled=not admin):
        st.session_state.page="USERS"; st.rerun()

def page_fp_create():
    require_login()
    brand_banner()
    st.subheader("📷 First Piece — Create")
    with st.form("fp_form", clear_on_submit=True):
        c1,c2,c3 = st.columns(3)
        model_no = c1.text_input("Model (short)")
        sn = c2.text_input("SN / Barcode")
        department = c3.text_input("Department")
        c4,c5,c6 = st.columns(3)
        model_version = c4.text_input("Model Version")
        mo = c5.text_input("MO / Work Order")
        customer_supplier = c6.text_input("Customer / Supplier")
        notes = st.text_area("Notes / Description")

        st.markdown("**TOP image**")
        up_top = st.file_uploader("Upload TOP photo", type=["jpg","jpeg","png"], key="fp_top")
        st.markdown("**BOTTOM image**")
        up_bottom = st.file_uploader("Upload BOTTOM photo", type=["jpg","jpeg","png"], key="fp_bottom")

        ok = st.form_submit_button("Save first piece", type="primary")
        if ok:
            if not model_no.strip():
                st.error("Model is required.")
            else:
                img_top = save_image(f"firstpiece/{model_no}", up_top) if up_top else None
                img_bottom = save_image(f"firstpiece/{model_no}", up_bottom) if up_bottom else None
                with conn() as c:
                    c.execute("""
                        INSERT INTO first_piece(created_at,model_no,model_version,sn,mo,department,
                                                customer_supplier,notes,img_top,img_bottom,reporter)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        datetime.utcnow().isoformat(),
                        model_no.strip(),
                        model_version.strip(),
                        sn.strip(),
                        mo.strip(),
                        department.strip(),
                        customer_supplier.strip(),
                        notes.strip(),
                        img_top, img_bottom,
                        current_user()["display_name"],
                    ))
                    c.commit()
                st.success("First piece saved.")

def page_nc_create():
    require_login()
    brand_banner()
    st.subheader("🧩 Create Non-Conformity")

    # Form fields in the order matching your sheet
    with st.form("nc_form", clear_on_submit=True):
        c1,c2,c3,c4 = st.columns(4)
        model_no      = c1.text_input("Model")
        model_version = c2.text_input("Model Version")
        sn            = c3.text_input("SN / Barcode")
        mo            = c4.text_input("MO / Work Order")

        severity      = st.text_input("Nonconformity (category, e.g. Minor/Major/Critical)")
        description   = st.text_area("Description of Nonconformity")

        c5,c6,c7,c8 = st.columns(4)
        customer_supplier = c5.text_input("Customer/Supplier")
        line              = c6.text_input("Line")
        work_station      = c7.text_input("Work Station")
        unit_head         = c8.text_input("Unit Head")

        c9,c10,c11 = st.columns(3)
        responsibility   = c9.text_input("Responsibility")
        root_cause       = c10.text_input("Root Cause")
        corrective_action= c11.text_input("Corrective Action")

        c12,c13,c14 = st.columns(3)
        exception_reporters = c12.text_input("Exception reporters")
        discovery           = c13.text_input("Discovery")
        origin_sources      = c14.text_input("Origil Sources")

        c15,c16,c17 = st.columns(3)
        defective_item     = c15.text_input("Defective Item")
        defective_item_2   = c16.text_input("Defective Item (2)")  # in case of duplicate column
        defective_outflow  = c17.text_input("Defective Outflow")

        c18,c19,c20 = st.columns(3)
        defective_qty  = c18.text_input("Defective Qty")
        inspection_qty = c19.text_input("Inspection Qty")
        lot_qty        = c20.text_input("Lot Qty")

        up = st.file_uploader("Upload photo (optional)", type=["jpg","jpeg","png"], key="nc_photo")

        ok = st.form_submit_button("Save NC", type="primary")
        if ok:
            img = save_image(f"nc/{model_no or 'misc'}", up) if up else None
            extra = {
                "customer_supplier": customer_supplier.strip(),
                "line": line.strip(),
                "work_station": work_station.strip(),
                "unit_head": unit_head.strip(),
                "responsibility": responsibility.strip(),
                "root_cause": root_cause.strip(),
                "corrective_action": corrective_action.strip(),
                "exception_reporters": exception_reporters.strip(),
                "discovery": discovery.strip(),
                "origin_sources": origin_sources.strip(),
                "defective_item": defective_item.strip(),
                "defective_item_2": defective_item_2.strip(),
                "defective_outflow": defective_outflow.strip(),
                "defective_qty": defective_qty.strip(),
                "inspection_qty": inspection_qty.strip(),
                "lot_qty": lot_qty.strip(),
            }
            with conn() as c:
                c.execute("""
                    INSERT INTO nc(created_at,model_no,model_version,sn,mo,description,severity,
                                   reporter,img,extra)
                    VALUES(?,?,?,?,?,?,?,?,?,?)
                """, (
                    datetime.utcnow().isoformat(),
                    model_no.strip(), model_version.strip(), sn.strip(), mo.strip(),
                    description.strip(), severity.strip(),
                    current_user()["display_name"],
                    img,
                    json.dumps(extra, ensure_ascii=False),
                ))
                c.commit()
            st.success("Non-Conformity saved.")

def page_search():
    require_login()
    brand_banner()
    st.subheader("🔎 Search & View")

    # Build Customer/Supplier options
    cs_opts = set()
    with conn() as c:
        cs_opts.update([r[0] for r in c.execute(
            "SELECT DISTINCT customer_supplier FROM first_piece "
            "WHERE customer_supplier IS NOT NULL AND TRIM(customer_supplier)<>''").fetchall()
        ])
        for (ex,) in c.execute("SELECT extra FROM nc WHERE extra IS NOT NULL").fetchall():
            try:
                cs = (json.loads(ex).get("customer_supplier") or "").strip()
                if cs: cs_opts.add(cs)
            except Exception:
                pass
    cs_list = ["(any)"] + sorted([x for x in cs_opts if x])

    f1 = st.columns([1,1,1,1,1.5])
    model = f1[0].text_input("Model contains")
    vers  = f1[1].text_input("Version contains")
    sn    = f1[2].text_input("SN contains")
    mo    = f1[3].text_input("MO contains")
    textin= f1[4].text_input("Text in description/reporter/type/extra")

    f2 = st.columns([1.2,1.2,1,2])
    cs_pick = f2[0].selectbox("Customer / Supplier", cs_list)
    scope   = f2[1].selectbox("Scope", ["Both","First Piece only","Non-Conformity only"])
    limit   = f2[2].slider("Max per section", 20, 300, 80, 20)

    dcol = st.columns([1,1,4])
    dt_from = dcol[0].date_input("From", value=date.today()-timedelta(days=30))
    dt_to   = dcol[1].date_input("To",   value=date.today())
    if dt_from > dt_to: dt_from, dt_to = dt_to, dt_from
    ds, de = dt_from.strftime("%Y-%m-%d"), dt_to.strftime("%Y-%m-%d")

    run = st.button("Search", type="primary")

    df_fp, df_nc = None, None
    if run:
        # First piece
        if scope in ("Both","First Piece only"):
            q  = "SELECT * FROM first_piece WHERE date(substr(created_at,1,10)) BETWEEN ? AND ?"
            pa = [ds,de]
            for col,val in (("model_no",model),("model_version",vers),("sn",sn),("mo",mo)):
                if val: q += f" AND {col} LIKE ?"; pa.append(f"%{val}%")
            if cs_pick != "(any)":
                q += " AND customer_supplier=?"; pa.append(cs_pick)
            q += f" ORDER BY id DESC LIMIT {int(limit)}"
            with conn() as c: df_fp = pd.read_sql_query(q, c, params=pa)

        # NC
        if scope in ("Both","Non-Conformity only"):
            qn, pn = "SELECT * FROM nc WHERE date(substr(created_at,1,10)) BETWEEN ? AND ?", [ds,de]
            for col,val in (("model_no",model),("model_version",vers),("sn",sn),("mo",mo)):
                if val: qn += f" AND {col} LIKE ?"; pn.append(f"%{val}%")
            if textin:
                qn += " AND (description LIKE ? OR reporter LIKE ? OR severity LIKE ? OR extra LIKE ?)"
                pn += [f"%{textin}%"]*4
            qn += f" ORDER BY id DESC LIMIT {int(limit*2)}"
            with conn() as c: df_nc = pd.read_sql_query(qn, c, params=pn)
            # filter by CS in JSON
            if df_nc is not None and not df_nc.empty and cs_pick!="(any)":
                def getcs(row):
                    try: return (json.loads(row.get("extra") or "{}").get("customer_supplier") or "").strip()
                    except: return ""
                df_nc = df_nc.copy()
                df_nc["__cs__"] = df_nc.apply(getcs, axis=1)
                df_nc = df_nc[df_nc["__cs__"]==cs_pick].drop(columns="__cs__").head(limit)

        st.toast("Search complete.")

    # Render FP cards
    if df_fp is not None:
        st.markdown(f"#### First Piece results ({len(df_fp)})")
        if df_fp.empty:
            st.info("No First-Piece records.")
        else:
            for _, r in df_fp.iterrows():
                with st.container(border=True):
                    left, mid, right = st.columns([1,1,4])
                    p_top = ROOT/str(r["img_top"]) if r.get("img_top") else None
                    p_bot = ROOT/str(r["img_bottom"]) if r.get("img_bottom") else None
                    with left:
                        if p_top and p_top.exists(): st.image(str(p_top), width=150, caption="TOP")
                    with mid:
                        if p_bot and p_bot.exists(): st.image(str(p_bot), width=150, caption="BOTTOM")
                    with right:
                        st.markdown(
                            f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} | "
                            f"**SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
                        )
                        st.caption(
                            f"🕒 {r['created_at']} · 🧑‍💼 {r['reporter']} · "
                            f"🏷 Dept: {r.get('department') or '-'} · 👥 {r.get('customer_supplier') or '-'}"
                        )
                        if r.get("notes"): st.write(r["notes"])
            # export if results
            st.download_button(
                "Download First-Piece CSV",
                df_fp.to_csv(index=False).encode("utf-8"),
                "firstpiece_export.csv", "text/csv", use_container_width=True
            )

    # Render NC cards
    if df_nc is not None:
        st.markdown(f"#### Non-Conformity results ({len(df_nc)})")
        if df_nc.empty:
            st.info("No NC records.")
        else:
            for _, r in df_nc.iterrows():
                with st.container(border=True):
                    c0,c1 = st.columns([1,4])
                    p = ROOT/str(r["img"]) if r.get("img") else None
                    with c0:
                        if p and p.exists(): st.image(str(p), width=160)
                        # Add photo for NC (optional)
                        add = st.file_uploader(f"Add photo (ID {r['id']})", type=["jpg","jpeg","png"], key=f"addimg_{r['id']}")
                        if add:
                            newp = save_image(f"nc/{r['model_no'] or 'misc'}", add)
                            with conn() as c:
                                c.execute("UPDATE nc SET img=? WHERE id=?", (newp, int(r["id"])))
                                c.commit()
                            st.success("Photo added."); st.rerun()
                    with c1:
                        st.markdown(
                            f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} | "
                            f"**SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
                        )
                        st.caption(f"🕒 {r['created_at']} · 🧑‍💼 {r['reporter']} · 🏷 Category: {r.get('severity') or '-'}")
                        if r.get("description"): st.write(r["description"])
                        # compact extra
                        try:
                            extra = json.loads(r.get("extra") or "{}")
                            if extra:
                                pairs = [f"**{k.replace('_',' ')}:** {v}" for k,v in extra.items() if str(v).strip()]
                                if pairs: st.markdown("  ".join(pairs))
                        except Exception:
                            pass
                        if can_delete_modify():
                            if st.button("Delete", key=f"d_nc_{r['id']}"):
                                with conn() as c:
                                    c.execute("DELETE FROM nc WHERE id=?", (int(r["id"]),))
                                    c.commit()
                                st.success("Deleted."); st.rerun()
            # export if results
            st.download_button(
                "Download NC CSV",
                df_nc.to_csv(index=False).encode("utf-8"),
                "nc_export.csv", "text/csv", use_container_width=True
            )

def read_any_table(file) -> pd.DataFrame:
    """Read CSV or Excel (xlsx/xls), robust encoding for CSV."""
    name = (file.name or "").lower()
    data = file.read()
    bio = io.BytesIO(data)
    if name.endswith((".xlsx",".xls")):
        return pd.read_excel(bio, dtype=str).fillna("")
    # CSV: try utf-8 then big5 then cp1252
    for enc in ("utf-8-sig","utf-8","big5","cp950","cp1252","latin1"):
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding=enc).fillna("")
        except Exception:
            continue
    # last resort: pandas sniff
    return pd.read_csv(io.BytesIO(data), dtype=str, engine="python", sep=None).fillna("")

def page_import():
    require_login()
    brand_banner()
    st.subheader("⬆️ Import CSV / Excel")

    st.info("Browse a CSV or Excel file, **Preview** it, then click **Import**. Nothing is imported automatically.")
    up = st.file_uploader("Choose CSV/Excel", type=["csv","xlsx","xls"])
    if not up: return

    if st.button("Preview", type="secondary"):
        try:
            st.session_state["import_df"] = read_any_table(up)
            st.success("Preview ready below.")
        except Exception as e:
            st.error(f"Failed to read file: {e}")

    df = st.session_state.get("import_df")
    if df is not None:
        st.dataframe(df.head(50), use_container_width=True, hide_index=True)

        # mapping from your Excel headers to NC columns
        # If a header is slightly different, edit here once.
        MAP = {
            "Nonconformity": "severity",
            "Description of Nonconformity": "description",
            "Date": "created_date",
            "Customer/Supplier": "customer_supplier",
            "Model/Part No.": "model_no",
            "MO/PO": "mo",
            "Line": "line",
            "Work Station": "work_station",
            "Unit Head": "unit_head",
            "Responsibility": "responsibility",
            "Root Cause": "root_cause",
            "Corrective Action": "corrective_action",
            "Exception reporters": "exception_reporters",
            "Discovery": "discovery",
            "Origil Sources": "origin_sources",
            "Defective Item": "defective_item",
            "Defective Outflow": "defective_outflow",
            "Defective Qty": "defective_qty",
            "Inspection Qty": "inspection_qty",
            "Lot Qty": "lot_qty",
        }
        missing = [src for src in MAP if src not in df.columns]
        if missing:
            st.warning("Missing columns in file: " + ", ".join(missing))

        if st.button(f"Import {len(df)} rows", type="primary"):
            imported = 0
            with conn() as c:
                for _, row in df.iterrows():
                    try:
                        # build nc record
                        model_no = row.get("Model/Part No.","")
                        model_version = ""  # not typically in sheet
                        sn = ""             # not typically in sheet
                        mo = row.get("MO/PO","")
                        description = row.get("Description of Nonconformity","")
                        severity = row.get("Nonconformity","")
                        # date
                        cd = row.get("Date","")
                        # normalize created_at
                        created_at = None
                        if cd:
                            try:
                                created_at = pd.to_datetime(cd).to_pydatetime().isoformat()
                            except Exception:
                                created_at = datetime.utcnow().isoformat()
                        else:
                            created_at = datetime.utcnow().isoformat()

                        extra = { MAP[k]: row.get(k,"") for k in MAP if MAP[k] not in ("severity","description","created_date") }
                        c.execute("""
                            INSERT INTO nc(created_at,model_no,model_version,sn,mo,description,severity,reporter,img,extra)
                            VALUES(?,?,?,?,?,?,?,?,?,?)
                        """, (
                            created_at,
                            str(model_no), str(model_version), str(sn), str(mo),
                            str(description), str(severity),
                            current_user()["display_name"],  # importer as reporter
                            None,
                            json.dumps(extra, ensure_ascii=False),
                        ))
                        imported += 1
                    except Exception:
                        pass
                c.commit()
            st.success(f"Imported {imported} rows.")

def page_profile():
    require_login()
    brand_banner()
    u = current_user()
    st.subheader("👤 Personal")
    st.write(f"**User:** {u['username']}")
    st.write(f"**Display name:** {u['display_name']}")
    st.write(f"**Role:** {u['role']}")
    st.divider()
    st.markdown("#### Change password")
    with st.form("chg_pwd"):
        p1 = st.text_input("New password", type="password")
        p2 = st.text_input("Confirm", type="password")
        ok = st.form_submit_button("Change", type="primary")
        if ok:
            if not p1 or p1 != p2:
                st.error("Password mismatch.")
            else:
                with conn() as c:
                    c.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_pwd(p1), u["id"]))
                    c.commit()
                st.success("Password updated. Please re-login.")
                st.session_state.clear()
                st.rerun()

def page_users():
    require_login()
    if current_user()["role"] != "Admin":
        st.error("Admin only."); return
    brand_banner()
    st.subheader("⚙️ User Setup (Admin)")

    # add user
    with st.form("add_user", clear_on_submit=True):
        st.markdown("#### Add user")
        c1,c2,c3 = st.columns(3)
        uname = c1.text_input("Username")
        disp  = c2.text_input("Display name")
        role  = c3.selectbox("Role", ["QA","QC","Admin"], index=1)
        pwd   = st.text_input("Temp password", type="password")
        ok = st.form_submit_button("Create", type="primary")
        if ok:
            if not uname or not pwd:
                st.error("User & password required.")
            else:
                try:
                    with conn() as c:
                        c.execute("INSERT INTO users(username,pass_hash,display_name,role) VALUES(?,?,?,?)",
                                  (uname, hash_pwd(pwd), disp, role))
                        c.commit()
                    st.success("User created.")
                except sqlite3.IntegrityError:
                    st.error("Username already exists.")

    st.divider()
    st.markdown("#### All users")
    with conn() as c:
        rows = c.execute("SELECT id,username,display_name,role FROM users ORDER BY username").fetchall()
    for uid,un,disp,role in rows:
        with st.container(border=True):
            c1,c2,c3,c4 = st.columns([2,2,1,2])
            c1.write(f"**{un}**")
            new_disp = c2.text_input("Display name", value=disp, key=f"disp_{uid}")
            new_role = c3.selectbox("Role", options=["Admin","QA","QC"], index=["Admin","QA","QC"].index(role), key=f"role_{uid}")
            ok1 = c4.button("Update", key=f"upd_{uid}")
            ok2 = c4.button("Reset password to '123456'", key=f"rst_{uid}")
            if ok1:
                with conn() as c:
                    c.execute("UPDATE users SET display_name=?, role=? WHERE id=?", (new_disp,new_role,uid))
                    c.commit()
                st.success("Updated.")
            if ok2:
                with conn() as c:
                    c.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_pwd("123456"), uid))
                    c.commit()
                st.success("Password reset.")

# --------------------------- Router ---------------------------------------- #
def router():
    page = st.session_state.get("page", "LOGIN")

    if page == "LOGIN":
        page_login()
        return

    if page == "HOME":
        page_home()
    elif page == "FP":
        page_fp_create()
    elif page == "NC":
        page_nc_create()
    elif page == "SEARCH":
        page_search()
    elif page == "IMPORT":
        page_import()
    elif page == "PROFILE":
        page_profile()
    elif page == "USERS":
        page_users()
    else:
        st.session_state["page"] = "HOME"
        st.rerun()

    # ⬇️ add this so the nav appears on all non-login pages
    bottom_nav()

# --------------------------- Boot ------------------------------------------ #
if __name__ == "__main__":
    init_db()
    if "page" not in st.session_state: st.session_state["page"] = "LOGIN"
    router()

