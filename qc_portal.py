# Quality Management Portal — Streamlit app (edit/delete + robust search)
# ----------------------------------------------------------------------
# - Login/roles (Admin, QA, QC)
# - Home tiles + bottom nav
# - Create First Piece (TOP/BOTTOM), Create NC
# - Search & View with: date range, "All time", "Include undated", Customer/Supplier,
#   large/sharp images, per-card Edit + Delete (with confirmation), diagnostics,
#   CSV exports
# - Import CSV/Excel (Preview -> Import; keeps sheet event date in extra.created_date)
#
# Storage:
#   DB    : /mount/data/qm_portal.sqlite3 (fallback /tmp/qc_portal/…)
#   Photos: ROOT/images/...

import os, io, json, hashlib, sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

import streamlit as st
import pandas as pd
from PIL import Image

# =========================== Cloud-safe storage ============================ #
def _data_root() -> Path:
    for p in (Path("/mount/data"), Path("/tmp/qc_portal")):
        try:
            p.mkdir(parents=True, exist_ok=True)
            (p / ".ok").write_text("ok", encoding="utf-8")
            return p
        except Exception:
            pass
    raise RuntimeError("No writable directory available")

ROOT   = _data_root()
IMGDIR = ROOT / "images"
IMGDIR.mkdir(parents=True, exist_ok=True)
DB     = ROOT / "qm_portal.sqlite3"

# =============================== Database ================================= #
def db():
    return sqlite3.connect(DB)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         username TEXT UNIQUE, pass_hash TEXT, display_name TEXT,
         role TEXT CHECK(role in ('Admin','QA','QC')) NOT NULL
       )""",
    """CREATE TABLE IF NOT EXISTS first_piece(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         created_at TEXT,
         model_no TEXT, model_version TEXT, sn TEXT, mo TEXT,
         department TEXT, customer_supplier TEXT,
         notes TEXT, img_top TEXT, img_bottom TEXT,
         reporter TEXT
       )""",
    """CREATE TABLE IF NOT EXISTS nc(
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         created_at TEXT,
         model_no TEXT, model_version TEXT, sn TEXT, mo TEXT,
         description TEXT, severity TEXT, reporter TEXT,
         img TEXT, extra TEXT
       )""",
]

def init_db():
    with db() as c:
        for s in SCHEMA: c.execute(s)
        # seed admin
        cur = c.execute("SELECT COUNT(*) FROM users WHERE username='Admin'")
        if cur.fetchone()[0] == 0:
            c.execute("INSERT INTO users(username,pass_hash,display_name,role) VALUES(?,?,?,?)",
                      ("Admin", hash_pwd("admin1234"), "Admin", "Admin"))
        c.commit()

# =============================== Auth ===================================== #
def hash_pwd(p: str) -> str:
    return hashlib.sha256(("qmportal::" + p).encode("utf-8")).hexdigest()

def get_user(username: str):
    with db() as c:
        r = c.execute("""SELECT id,username,pass_hash,display_name,role
                         FROM users WHERE username=?""", (username,)).fetchone()
    if not r: return None
    return {"id": r[0], "username": r[1], "pass_hash": r[2],
            "display_name": r[3], "role": r[4]}

def me(): return st.session_state.get("user")
def require_login():
    if "user" not in st.session_state:
        st.session_state["page"] = "LOGIN"; st.rerun()
def can_edit_delete():  # Admin or QA
    u = me(); return u and u["role"] in ("Admin", "QA")

# =============================== Helpers ================================== #
def save_img(subfolder: str, up) -> str:
    """
    Save the uploaded file at full quality.
    - If it's JPG/PNG/WEBP: store the original bytes (no recompress).
    - Otherwise (e.g., HEIC): convert to a high-quality JPEG.
    Returns a path relative to ROOT.
    """
    if not up:
        return ""

    folder = IMGDIR / subfolder
    folder.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    orig_name = (up.name or "image").replace(" ", "_")
    ext = Path(orig_name).suffix.lower()

    as_is = {".jpg", ".jpeg", ".png", ".webp"}
    out_path: Path

    if ext in as_is:
        # write original bytes exactly as uploaded (crisp)
        out_path = folder / f"{ts}_{orig_name}"
        data = up.getvalue()  # original bytes
        with open(out_path, "wb") as f:
            f.write(data)
        return str(out_path.relative_to(ROOT))

    # Attempt to open and convert (e.g., HEIC -> JPEG)
    out_path = folder / f"{ts}_{Path(orig_name).stem}.jpg"
    try:
        img = Image.open(up)
        img = img.convert("RGB")
        img.save(out_path, "JPEG", quality=95, subsampling=0, optimize=True, progressive=True)
        return str(out_path.relative_to(ROOT))
    except UnidentifiedImageError:
        # last resort: dump bytes (viewer might not support the format)
        with open(out_path, "wb") as f:
            f.write(up.getvalue())
        return str(out_path.relative_to(ROOT))

def evt_date_from_row(row: dict) -> str:
    """For NC: prefer sheet event date from extra.created_date (or Date) else created_at."""
    try:
        extra = json.loads(row.get("extra") or "{}")
    except Exception:
        extra = {}
    raw = extra.get("created_date") or row.get("date")
    if raw:
        try:
            return str(pd.to_datetime(raw, errors="coerce").date())
        except Exception:
            pass
    ca = row.get("created_at") or ""
    return ca[:10]
    
def show_image(relpath: str, caption: str | None = None, key: str | None = None, thumb_width: int = 520):
    """
    Render a crisp thumbnail + a full-size modal + download original.
    relpath should be ROOT-relative, e.g. 'images/firstpiece/...'
    """
    if not relpath:
        return
    p = ROOT / str(relpath)
    if not p.is_file():
        return

    # Larger, crisp thumbnail to avoid upscaling blur
    st.image(str(p), width=thumb_width, caption=caption)

    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("🔎 View full-size", key=f"view_full_{key or relpath}"):
            with st.modal("Full-size image"):
                st.image(str(p), use_container_width=True)
    with c2:
        try:
            st.download_button("⬇️ Download original", data=p.read_bytes(), file_name=p.name, use_container_width=True)
        except Exception:
            pass
            
def banner():
    st.markdown("""
    <style>
      .banner{background:linear-gradient(120deg,#eaf2ff 0%,#f8fbff 100%);
              padding:14px 18px;border-radius:16px;border:1px solid #e9eef5;margin-bottom:14px}
      .brandrow{display:flex;align-items:center;gap:14px}
      .brandrow .logo{font-size:30px}
      .brandrow .title{font-size:18px;font-weight:800;color:#1b2b59}
      .sub{font-size:13px;color:#4a5b88}
      .chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
      .chip{background:#f6f8ff;border:1px solid #e8ecfd;color:#2c3b69;padding:4px 8px;border-radius:12px;font-size:12px}
    </style>
    """, unsafe_allow_html=True)
    user = me()
    disp = user["display_name"] if user else "-"
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

def bottom_nav():
    st.divider()
    c1,c2,c3 = st.columns(3)
    if c1.button("🏠 Home", key="bn_home", use_container_width=True):
        st.session_state["page"] = "HOME"; st.rerun()
    if c2.button("👤 Personal", key="bn_me", use_container_width=True):
        st.session_state["page"] = "PROFILE"; st.rerun()
    if c3.button("🚪 Logout", key="bn_out", use_container_width=True):
        st.session_state.clear(); st.rerun()

# ======== Update & Delete (records) + tiny cache bust via rerun =========== #
def update_fp(fp_id: int, payload: dict):
    if not payload: return
    fields = ", ".join([f"{k}=?" for k in payload.keys()])
    vals = list(payload.values()) + [fp_id]
    with db() as c:
        c.execute(f"UPDATE first_piece SET {fields} WHERE id=?", vals); c.commit()
    st.toast("First-Piece updated"); st.rerun()

def delete_fp(fp_id: int):
    with db() as c:
        c.execute("DELETE FROM first_piece WHERE id=?", (fp_id,)); c.commit()
    st.toast("First-Piece deleted"); st.rerun()

def update_nc(nc_id: int, payload: dict, extra_upd: dict | None = None):
    to_set = payload.copy()
    if extra_upd is not None:
        with db() as c:
            row = c.execute("SELECT extra FROM nc WHERE id=?", (nc_id,)).fetchone()
        cur_extra = {}
        if row and row[0]:
            try: cur_extra = json.loads(row[0])
            except Exception: cur_extra = {}
        cur_extra.update({k:v for k,v in (extra_upd or {}).items()})
        to_set["extra"] = json.dumps(cur_extra, ensure_ascii=False)
    if to_set:
        fields = ", ".join([f"{k}=?" for k in to_set.keys()])
        vals = list(to_set.values()) + [nc_id]
        with db() as c:
            c.execute(f"UPDATE nc SET {fields} WHERE id=?", vals); c.commit()
    st.toast("NC updated"); st.rerun()

def delete_nc(nc_id: int):
    with db() as c:
        c.execute("DELETE FROM nc WHERE id=?", (nc_id,)); c.commit()
    st.toast("NC deleted"); st.rerun()

# ============================== Pages ===================================== #
def page_login():
    st.set_page_config(page_title="Quality Management Portal", layout="wide")
    st.markdown("<h2 style='text-align:center;margin-top:8vh'>🔐 Quality Management Portal</h2>", unsafe_allow_html=True)
    with st.form("login"):
        u = st.text_input("User")
        p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login", type="primary")
        if ok:
            user = get_user(u.strip())
            if user and user["pass_hash"] == hash_pwd(p):
                st.session_state["user"] = user
                st.session_state["page"] = "HOME"
                st.success("Welcome!"); st.rerun()
            st.error("Invalid credentials.")
            
        st.markdown("""
        <style>
        /* Keep images crisp; avoid browser soft scaling artifacts */
        img { image-rendering: auto; }
        </style>
        """, unsafe_allow_html=True)

def page_home():
    require_login(); banner()
    st.markdown("### Main Menu")
    r1 = st.columns(3, gap="large")
    if r1[0].button("📷 First Piece", use_container_width=True): st.session_state.page="FP"; st.rerun()
    if r1[1].button("🧩 Create NC", use_container_width=True): st.session_state.page="NC"; st.rerun()
    if r1[2].button("🔎 Search & View", use_container_width=True): st.session_state.page="SEARCH"; st.rerun()
    r2 = st.columns(3, gap="large")
    if r2[0].button("⬆️ Import CSV/Excel", use_container_width=True): st.session_state.page="IMPORT"; st.rerun()
    if r2[1].button("👤 Personal", use_container_width=True): st.session_state.page="PROFILE"; st.rerun()
    admin = me()["role"] == "Admin"
    if r2[2].button("⚙️ User Setup", use_container_width=True, disabled=not admin):
        st.session_state.page="USERS"; st.rerun()

def page_fp_create():
    require_login(); banner()
    st.subheader("📷 First Piece — Create")
    with st.form("fp_form", clear_on_submit=True):
        c1,c2,c3 = st.columns(3)
        model_no = c1.text_input("Model (short)")
        sn       = c2.text_input("SN / Barcode")
        dept     = c3.text_input("Department")
        c4,c5,c6 = st.columns(3)
        version  = c4.text_input("Model Version")
        mo       = c5.text_input("MO / Work Order")
        cs       = c6.text_input("Customer / Supplier")
        notes    = st.text_area("Notes / Description")

        st.markdown("**TOP image**")
        up_top    = st.file_uploader("Upload TOP photo", type=["jpg","jpeg","png"], key="fp_top")
        st.markdown("**BOTTOM image**")
        up_bottom = st.file_uploader("Upload BOTTOM photo", type=["jpg","jpeg","png"], key="fp_bottom")

        if st.form_submit_button("Save first piece", type="primary"):
            if not model_no.strip():
                st.error("Model is required.")
            else:
                p_top = save_img(f"firstpiece/{model_no}", up_top) if up_top else None
                p_bot = save_img(f"firstpiece/{model_no}", up_bottom) if up_bottom else None
                with db() as c:
                    c.execute("""INSERT INTO first_piece(created_at,model_no,model_version,sn,mo,
                                department,customer_supplier,notes,img_top,img_bottom,reporter)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                              (datetime.utcnow().isoformat(), model_no.strip(), version.strip(), sn.strip(),
                               mo.strip(), dept.strip(), cs.strip(), notes.strip(), p_top, p_bot,
                               me()["display_name"]))
                    c.commit()
                st.success("First piece saved.")

def page_nc_create():
    require_login(); banner()
    st.subheader("🧩 Create Non-Conformity")
    with st.form("nc_form", clear_on_submit=True):
        c1,c2,c3,c4 = st.columns(4)
        model       = c1.text_input("Model")
        version     = c2.text_input("Model Version")
        sn          = c3.text_input("SN / Barcode")
        mo          = c4.text_input("MO / Work Order")

        severity    = st.text_input("Nonconformity (category)")
        descr       = st.text_area("Description of Nonconformity")

        c5,c6,c7,c8 = st.columns(4)
        cs          = c5.text_input("Customer/Supplier")
        line        = c6.text_input("Line")
        ws          = c7.text_input("Work Station")
        head        = c8.text_input("Unit Head")

        c9,c10,c11  = st.columns(3)
        resp        = c9.text_input("Responsibility")
        root        = c10.text_input("Root Cause")
        ca          = c11.text_input("Corrective Action")

        c12,c13,c14 = st.columns(3)
        exc         = c12.text_input("Exception reporters")
        disc        = c13.text_input("Discovery")
        origin      = c14.text_input("Origil Sources")

        c15,c16,c17 = st.columns(3)
        item1       = c15.text_input("Defective Item")
        item2       = c16.text_input("Defective Item (2)")
        outflow     = c17.text_input("Defective Outflow")

        c18,c19,c20 = st.columns(3)
        dqty        = c18.text_input("Defective Qty")
        iqty        = c19.text_input("Inspection Qty")
        lqty        = c20.text_input("Lot Qty")

        photo = st.file_uploader("Upload photo (optional)", type=["jpg","jpeg","png"], key="nc_photo")

        if st.form_submit_button("Save NC", type="primary"):
            img = save_img(f"nc/{model or 'misc'}", photo) if photo else None
            extra = {
                "customer_supplier": cs.strip(), "line": line.strip(),
                "work_station": ws.strip(), "unit_head": head.strip(),
                "responsibility": resp.strip(), "root_cause": root.strip(),
                "corrective_action": ca.strip(), "exception_reporters": exc.strip(),
                "discovery": disc.strip(), "origin_sources": origin.strip(),
                "defective_item": item1.strip(), "defective_item_2": item2.strip(),
                "defective_outflow": outflow.strip(), "defective_qty": dqty.strip(),
                "inspection_qty": iqty.strip(), "lot_qty": lqty.strip()
            }
            with db() as c:
                c.execute("""INSERT INTO nc(created_at,model_no,model_version,sn,mo,description,severity,
                              reporter,img,extra) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                          (datetime.utcnow().isoformat(), model.strip(), version.strip(), sn.strip(), mo.strip(),
                           descr.strip(), severity.strip(), me()["display_name"], img,
                           json.dumps(extra, ensure_ascii=False)))
                c.commit()
            st.success("Non-Conformity saved.")

def _cs_options() -> list[str]:
    s = set()
    with db() as c:
        s.update(v for (v,) in c.execute(
            "SELECT DISTINCT customer_supplier FROM first_piece "
            "WHERE customer_supplier IS NOT NULL AND TRIM(customer_supplier)<>''"
        ).fetchall())
        for (ex,) in c.execute("SELECT extra FROM nc WHERE extra IS NOT NULL").fetchall():
            try:
                v = (json.loads(ex).get("customer_supplier") or "").strip()
                if v: s.add(v)
            except Exception:
                pass
    return ["(any)"] + sorted(x for x in s if x)

def page_search():
    require_login(); banner()
    st.subheader("🔎 Search & View")

    # Filters
    f1 = st.columns([1,1,1,1,1.6])
    m  = f1[0].text_input("Model contains")
    v  = f1[1].text_input("Version contains")
    s  = f1[2].text_input("SN contains")
    mo = f1[3].text_input("MO contains")
    t  = f1[4].text_input("Text in description/reporter/type/extra")

    f2 = st.columns([1.2,1.2,1,1,2])
    cs = f2[0].selectbox("Customer / Supplier", _cs_options())
    scope = f2[1].selectbox("Scope", ["Both","First Piece only","Non-Conformity only"])
    limit = f2[2].slider("Max per section", 20, 500, 200, 20)
    alltime = f2[3].checkbox("All time", value=False)

    dcol = st.columns([1,1,3])
    d_from = dcol[0].date_input("From", date.today()-timedelta(days=90))
    d_to   = dcol[1].date_input("To", date.today())
    include_undated = dcol[2].checkbox("Include undated rows", value=True)

    if alltime:
        d_from = date(1900,1,1); d_to = date(2100,12,31)
    if d_from > d_to: d_from, d_to = d_to, d_from
    ds, de = d_from.strftime("%Y-%m-%d"), d_to.strftime("%Y-%m-%d")

    go = st.button("Search", type="primary")

    df_fp = df_nc = None
    diag = {"fp_raw": 0, "fp_after": 0, "nc_raw": 0, "nc_after": 0}

    if go:
        # First piece by import timestamp (created_at)
        if scope in ("Both","First Piece only"):
            q, pa = "SELECT * FROM first_piece WHERE date(substr(created_at,1,10)) BETWEEN ? AND ?", [ds,de]
            for col,val in (("model_no",m),("model_version",v),("sn",s),("mo",mo)):
                if val: q += f" AND {col} LIKE ?"; pa.append(f"%{val}%")
            if cs != "(any)": q += " AND customer_supplier=?"; pa.append(cs)
            q += " ORDER BY id DESC"
            with db() as c: raw = pd.read_sql_query(q, c, params=pa)
            diag["fp_raw"] = len(raw)
            df_fp = raw.head(limit)
            diag["fp_after"] = len(df_fp)

        # NC: fetch broadly, then filter by event date & CS in JSON
        if scope in ("Both","Non-Conformity only"):
            q, pa, flt = "SELECT * FROM nc", [], []
            for col,val in (("model_no",m),("model_version",v),("sn",s),("mo",mo)):
                if val: flt.append(f"{col} LIKE ?"); pa.append(f"%{val}%")
            if t:
                flt.append("(description LIKE ? OR reporter LIKE ? OR severity LIKE ? OR extra LIKE ?)")
                pa += [f"%{t}%"]*4
            if flt: q += " WHERE " + " AND ".join(flt)
            q += " ORDER BY id DESC"
            with db() as c: raw = pd.read_sql_query(q, c, params=pa)
            diag["nc_raw"] = len(raw)
            if not raw.empty:
                raw = raw.copy()
                raw["__evt__"] = raw.apply(evt_date_from_row, axis=1)
                mask = (raw["__evt__"] >= ds) & (raw["__evt__"] <= de)
                if include_undated:
                    mask = mask | (raw["__evt__"] == "")
                if cs != "(any)":
                    def _jcs(r):
                        try: return (json.loads(r.get("extra") or "{}").get("customer_supplier") or "").strip()
                        except Exception: return ""
                    raw["__cs__"] = raw.apply(_jcs, axis=1)
                    mask = mask & ((raw["__cs__"] == cs) | (raw["__cs__"].isna()))
                df_nc = raw[mask].drop(columns=[c for c in ["__evt__","__cs__"] if c in raw.columns]).head(limit)
            else:
                df_nc = raw
            diag["nc_after"] = len(df_nc)
        st.toast("Search complete.")

    # Diagnostics so you see WHY only 3 appear
    if go:
        with st.expander("Search diagnostics"):
            st.write(diag)
            st.caption("Tip: set **All time** ON and/or **Include undated rows** to widen results.")

    # ---------- First Piece cards
    if df_fp is not None:
        st.markdown(f"#### First Piece results ({len(df_fp)})")
        if df_fp.empty:
            st.info("No First-Piece records.")
        else:
            for _, r in df_fp.iterrows():
                with st.container(border=True):
                    imgL, infoR = st.columns([2,5])
                    with imgL:
                        p_top = ROOT/str(r.get("img_top") or "")
                        p_bot = ROOT/str(r.get("img_bottom") or "")
                        if p_top.is_file():
                            show_image(str(p_top.relative_to(ROOT)), "TOP", key=f"fp_top_{r['id']}", thumb_width=560)
                        if p_bot.is_file():
                            show_image(str(p_bot.relative_to(ROOT)), "BOTTOM", key=f"fp_bot_{r['id']}", thumb_width=560)
                    with infoR:
                        st.markdown(
                            f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} | "
                            f"**SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
                        )
                        st.caption(
                            f"🕒 {r['created_at'][:16]} · 🧑‍💼 {r['reporter']} · "
                            f"🏷 Dept: {r.get('department') or '-'} · 👥 {r.get('customer_supplier') or '-'}"
                        )
                        if r.get("notes"): st.write(r["notes"])

                        # --- Edit / Delete ---
                        with st.expander("✏️ Edit", expanded=False):
                            c1,c2,c3 = st.columns(3)
                            e_model = c1.text_input("Model", value=r["model_no"], key=f"fp_emodel_{r['id']}")
                            e_ver   = c2.text_input("Version", value=r["model_version"], key=f"fp_ever_{r['id']}")
                            e_sn    = c3.text_input("SN", value=r["sn"], key=f"fp_esn_{r['id']}")
                            c4,c5,c6 = st.columns(3)
                            e_mo    = c4.text_input("MO", value=r["mo"], key=f"fp_emo_{r['id']}")
                            e_dept  = c5.text_input("Dept", value=r.get("department",""), key=f"fp_edept_{r['id']}")
                            e_cs    = c6.text_input("Customer/Supplier", value=r.get("customer_supplier",""), key=f"fp_ecs_{r['id']}")
                            e_notes = st.text_area("Notes", value=r.get("notes",""), key=f"fp_enotes_{r['id']}")
                            if st.button("Save changes", key=f"fp_save_{r['id']}", type="primary"):
                                update_fp(int(r["id"]), {
                                    "model_no": e_model.strip(), "model_version": e_ver.strip(),
                                    "sn": e_sn.strip(), "mo": e_mo.strip(), "department": e_dept.strip(),
                                    "customer_supplier": e_cs.strip(), "notes": e_notes.strip()
                                })
                        if can_edit_delete():
                            if st.button("🗑 Delete", key=f"fp_del_{r['id']}", type="secondary"):
                                delete_fp(int(r["id"]))
        if len(df_fp):
            st.download_button("Download First-Piece CSV",
                               df_fp.to_csv(index=False).encode("utf-8"),
                               "firstpiece_export.csv", "text/csv", use_container_width=True)

    # ---------- NC cards
    if df_nc is not None:
        st.markdown(f"#### Non-Conformity results ({len(df_nc)})")
        if df_nc.empty:
            st.info("No NC records.")
        else:
            for _, r in df_nc.iterrows():
                with st.container(border=True):
                    left, right = st.columns([2,5])
                    with left:
                        p = ROOT/str(r.get("img") or "")
                        if p.is_file():
                            show_image(str(p.relative_to(ROOT)), None, key=f"nc_{r['id']}", thumb_width=560)
                        add = st.file_uploader(f"Add photo (ID {r['id']})",
                                               type=["jpg","jpeg","png"], key=f"add_{r['id']}")
                        if add:
                            newp = save_img(f"nc/{r.get('model_no') or 'misc'}", add)
                            with db() as c:
                                c.execute("UPDATE nc SET img=? WHERE id=?", (newp, int(r["id"])))
                                c.commit()
                            st.success("Photo added."); st.rerun()
                    with right:
                        st.markdown(
                            f"**Model:** {r['model_no'] or '-'} | **Version:** {r['model_version'] or '-'} | "
                            f"**SN:** {r['sn'] or '-'} | **MO:** {r['mo'] or '-'}"
                        )
                        st.caption(f"🕒 {evt_date_from_row(r)} · 🧑‍💼 {r['reporter']} · 🏷 Category: {r.get('severity') or '-'}")
                        if r.get("description"): st.write(r["description"])

                        # chips from JSON
                        try:
                            extra = json.loads(r.get("extra") or "{}")
                            chips = [f"<span class='chip'><b>{k.replace('_',' ')}</b>: {str(v).strip()}</span>"
                                     for k,v in extra.items() if str(v).strip()]
                            if chips: st.markdown("<div class='chips'>" + "".join(chips) + "</div>", unsafe_allow_html=True)
                        except Exception:
                            pass

                        # --- Edit / Delete ---
                        with st.expander("✏️ Edit", expanded=False):
                            c1,c2,c3,c4 = st.columns(4)
                            e_model  = c1.text_input("Model", value=r["model_no"], key=f"nc_emodel_{r['id']}")
                            e_ver    = c2.text_input("Version", value=r["model_version"], key=f"nc_ever_{r['id']}")
                            e_sn     = c3.text_input("SN", value=r["sn"], key=f"nc_esn_{r['id']}")
                            e_mo     = c4.text_input("MO", value=r["mo"], key=f"nc_emo_{r['id']}")
                            e_sev    = st.text_input("Nonconformity (category)", value=r.get("severity",""), key=f"nc_esev_{r['id']}")
                            e_desc   = st.text_area("Description", value=r.get("description",""), key=f"nc_edesc_{r['id']}")
                        
                            try: ex = json.loads(r.get("extra") or "{}")
                            except Exception: ex = {}
                            c5,c6,c7 = st.columns(3)
                            e_cs   = c5.text_input("Customer/Supplier", value=ex.get("customer_supplier",""), key=f"nc_ecs_{r['id']}")
                            e_line = c6.text_input("Line", value=ex.get("line",""), key=f"nc_eline_{r['id']}")
                            e_ws   = c7.text_input("Work Station", value=ex.get("work_station",""), key=f"nc_ews_{r['id']}")
                            c8,c9,c10 = st.columns(3)
                            e_resp = c8.text_input("Responsibility", value=ex.get("responsibility",""), key=f"nc_ersp_{r['id']}")
                            e_root = c9.text_input("Root Cause", value=ex.get("root_cause",""), key=f"nc_eroot_{r['id']}")
                            e_ca   = c10.text_input("Corrective Action", value=ex.get("corrective_action",""), key=f"nc_eca_{r['id']}")
                        
                            if st.button("Save changes", key=f"nc_save_{r['id']}", type="primary"):
                                update_nc(int(r["id"]),
                                          {"model_no": e_model.strip(), "model_version": e_ver.strip(),
                                           "sn": e_sn.strip(), "mo": e_mo.strip(),
                                           "severity": e_sev.strip(), "description": e_desc.strip()},
                                          {"customer_supplier": e_cs.strip(), "line": e_line.strip(),
                                           "work_station": e_ws.strip(), "responsibility": e_resp.strip(),
                                           "root_cause": e_root.strip(), "corrective_action": e_ca.strip()})
                        if can_edit_delete():
                            if st.button("🗑 Delete", key=f"nc_del_{r['id']}", type="secondary"):
                                delete_nc(int(r["id"]))
        if len(df_nc):
            st.download_button("Download NC CSV",
                               df_nc.to_csv(index=False).encode("utf-8"),
                               "nc_export.csv", "text/csv", use_container_width=True)

def _read_table(file) -> pd.DataFrame:
    """Read CSV/XLSX with encoding fallbacks and return str df."""
    name = (file.name or "").lower()
    data = file.read()
    bio  = io.BytesIO(data)
    if name.endswith((".xlsx",".xls")):
        return pd.read_excel(bio, dtype=str).fillna("")
    for enc in ("utf-8-sig","utf-8","big5","cp950","cp1252","latin1"):
        try:
            return pd.read_csv(io.BytesIO(data), dtype=str, encoding=enc).fillna("")
        except Exception:
            continue
    return pd.read_csv(io.BytesIO(data), dtype=str, engine="python", sep=None).fillna("")

def page_import():
    require_login(); banner()
    st.subheader("⬆️ Import CSV / Excel")
    st.info("Browse a CSV/Excel file, click **Preview**, verify, then **Import**. No auto-import.")

    up = st.file_uploader("Choose CSV/Excel", type=["csv","xlsx","xls"])
    if not up: return

    if st.button("Preview", type="secondary"):
        try:
            st.session_state["import_df"] = _read_table(up)
            st.success("Preview ready below.")
        except Exception as e:
            st.error(f"Failed to read file: {e}")

    df = st.session_state.get("import_df")
    if df is None: return

    st.dataframe(df.head(100), use_container_width=True, hide_index=True)

    MAP = {  # Excel header -> json/columns
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
    missing = [k for k in MAP if k not in df.columns]
    if missing:
        st.warning("Missing columns in file: " + ", ".join(missing))

    if st.button(f"Import {len(df)} rows", type="primary"):
        count = 0
        with db() as c:
            for _, row in df.iterrows():
                try:
                    extra = { MAP[k]: row.get(k,"") for k in MAP if MAP[k] not in ("severity","description","created_date") }
                    extra["created_date"] = row.get("Date","")
                    c.execute("""INSERT INTO nc(created_at,model_no,model_version,sn,mo,description,severity,
                                   reporter,img,extra)
                                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
                              (datetime.utcnow().isoformat(),
                               str(row.get("Model/Part No.","")), "", "", str(row.get("MO/PO","")),
                               str(row.get("Description of Nonconformity","")),
                               str(row.get("Nonconformity","")),
                               me()["display_name"], None,
                               json.dumps(extra, ensure_ascii=False)))
                    count += 1
                except Exception:
                    pass
            c.commit()
        st.success(f"Imported {count} rows.")

def page_profile():
    require_login(); banner()
    u = me()
    st.subheader("👤 Personal")
    st.write(f"**User:** {u['username']}")
    st.write(f"**Display name:** {u['display_name']}")
    st.write(f"**Role:** {u['role']}")
    st.divider()
    st.markdown("#### Change password")
    with st.form("chg_pw"):
        p1 = st.text_input("New password", type="password")
        p2 = st.text_input("Confirm", type="password")
        if st.form_submit_button("Change", type="primary"):
            if not p1 or p1 != p2:
                st.error("Password mismatch.")
            else:
                with db() as c:
                    c.execute("UPDATE users SET pass_hash=? WHERE id=?", (hash_pwd(p1), u["id"]))
                    c.commit()
                st.success("Password updated. Please re-login.")
                st.session_state.clear(); st.rerun()

def page_users():
    require_login()
    if me()["role"] != "Admin":
        st.error("Admin only."); return
    banner()
    st.subheader("⚙️ User Setup (Admin)")
    with st.form("add_user", clear_on_submit=True):
        c1,c2,c3 = st.columns(3)
        uname = c1.text_input("Username")
        disp  = c2.text_input("Display name")
        role  = c3.selectbox("Role", ["QA","QC","Admin"], index=1)
        pwd   = st.text_input("Temp password", type="password")
        if st.form_submit_button("Create", type="primary"):
            if not uname or not pwd:
                st.error("User & password required.")
            else:
                try:
                    with db() as c:
                        c.execute("INSERT INTO users(username,pass_hash,display_name,role) VALUES(?,?,?,?)",
                                  (uname, hash_pwd(pwd), disp, role))
                        c.commit()
                    st.success("User created.")
                except sqlite3.IntegrityError:
                    st.error("Username already exists.")
    st.divider()
    with db() as c:
        rows = c.execute("SELECT id,username,display_name,role FROM users ORDER BY username").fetchall()
    for uid,un,disp,role in rows:
        with st.container(border=True):
            c1,c2,c3,c4 = st.columns([2,2,1,2])
            c1.write(f"**{un}**")
            new_disp = c2.text_input("Display name", value=disp, key=f"ud_{uid}")
            new_role = c3.selectbox("Role", ["Admin","QA","QC"],
                                    index=["Admin","QA","QC"].index(role), key=f"ur_{uid}")
            if c4.button("Update", key=f"u_{uid}"):
                with db() as c:
                    c.execute("UPDATE users SET display_name=?, role=? WHERE id=?",
                              (new_disp, new_role, uid)); c.commit()
                st.success("Updated.")
            if c4.button("Reset password to '123456'", key=f"rp_{uid}"):
                with db() as c:
                    c.execute("UPDATE users SET pass_hash=? WHERE id=?",
                              (hash_pwd("123456"), uid)); c.commit()
                st.success("Password reset.")

# ============================ Router / Boot ================================ #
def router():
    page = st.session_state.get("page", "LOGIN")
    if page == "LOGIN":   page_login();  return
    if page == "HOME":    page_home()
    elif page == "FP":    page_fp_create()
    elif page == "NC":    page_nc_create()
    elif page == "SEARCH":page_search()
    elif page == "IMPORT":page_import()
    elif page == "PROFILE":page_profile()
    elif page == "USERS": page_users()
    else: st.session_state["page"] = "HOME"; st.rerun()
    bottom_nav()

if __name__ == "__main__":
    init_db()
    if "page" not in st.session_state: st.session_state["page"] = "LOGIN"
    router()


