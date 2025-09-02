# QC Portal (folders + models + First Piece + Nonconformities)
# Streamlit + SQLite, single-file app
# - Sidebar: Folders -> Models picker
# - Tabs: First Piece, Nonconformity, History, Admin
# - Photos saved under data/images/<model_root>/
# - Optional Teams notify via env TEAMS_WEBHOOK_URL

import os, io, uuid, json, sqlite3, textwrap
from pathlib import Path
from datetime import datetime
import getpass

import streamlit as st
import pandas as pd
from PIL import Image

try:
    import requests
except Exception:
    requests = None  # optional

APP_DIR = Path(__file__).parent.resolve()
DATA_DIR = APP_DIR / "data"
IMG_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "qc_portal.sqlite3"
DATA_DIR.mkdir(exist_ok=True)
IMG_DIR.mkdir(parents=True, exist_ok=True)

TEAMS_WEBHOOK = os.getenv("TEAMS_WEBHOOK_URL", "").strip()  # optional

# ------------------------- DB -------------------------
SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS folders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS models(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_version TEXT NOT NULL,   -- full name e.g. 190A56980
  model_root TEXT NOT NULL,      -- normalized e.g. 190-56980
  customer_supplier TEXT,
  folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
  UNIQUE(model_version)
);

CREATE TABLE IF NOT EXISTS first_piece(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  model_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
  model_root TEXT,
  sn TEXT,
  mo TEXT,
  status TEXT,         -- OK / NG / Pending
  review_notes TEXT,
  reporter TEXT,
  top_image TEXT,      -- rel path
  bottom_image TEXT
);

CREATE TABLE IF NOT EXISTS nonconformities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT,
  model_id INTEGER REFERENCES models(id) ON DELETE CASCADE,
  model_root TEXT,
  customer_supplier TEXT,
  mo TEXT,
  line TEXT,
  work_station TEXT,
  department TEXT,
  unit_head TEXT,
  responsibility TEXT,
  root_cause TEXT,
  corrective_action TEXT,
  discovery_dept TEXT,
  source TEXT,
  defective_category TEXT,
  defective_item TEXT,
  defective_outflow TEXT,
  defective_qty REAL,
  inspection_qty REAL,
  lot_qty REAL,
  severity TEXT,       -- Critical / Major / Minor
  description TEXT,
  reporter TEXT,
  cover_image TEXT     -- first photo rel path
);

CREATE TABLE IF NOT EXISTS nc_images(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  nc_id INTEGER REFERENCES nonconformities(id) ON DELETE CASCADE,
  image_path TEXT
);
"""

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with conn() as c:
        for ddl in SCHEMA.split(";\n"):
            d = ddl.strip()
            if d:
                c.execute(d)
        # default folder
        c.execute("INSERT OR IGNORE INTO folders(id,name) VALUES(1,'Unassigned')")
        c.commit()

init_db()

# --------------------- Helpers ------------------------

def compute_model_root(model_version: str) -> str:
    """Take something like 190A56980 -> 190-56980."""
    v = "".join([ch for ch in (model_version or "").upper() if ch.isalnum()])
    digits = "".join([d for d in v if d.isdigit()])
    if len(digits) >= 4:
        return f"{digits[:3]}-{digits[3:]}"
    return digits or (model_version or "").strip()

def username():
    # fallback if you don’t have auth
    return os.getenv("QC_USER") or getpass.getuser() or "User"

def save_image(model_root: str, file) -> str:
    folder = IMG_DIR / model_root
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    name = f"{ts}_{uuid.uuid4().hex[:8]}.jpg"
    out = folder / name
    img = Image.open(file).convert("RGB")
    img.save(out, "JPEG", quality=90)
    return str(out.relative_to(DATA_DIR))

def post_teams_card(title: str, body_lines: list[str]):
    if not TEAMS_WEBHOOK or not requests:
        return
    text = "**" + title + "**\n" + "\n".join(body_lines)
    try:
        requests.post(
            TEAMS_WEBHOOK,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"text": text}),
            timeout=5,
        )
    except Exception:
        pass

# --------------------- Cached Reads -------------------
@st.cache_data(show_spinner=False)
def list_folders_df():
    with conn() as c:
        return pd.read_sql_query("SELECT id,name FROM folders ORDER BY name", c)

@st.cache_data(show_spinner=False)
def list_models_df():
    with conn() as c:
        q = """SELECT m.id, m.model_version, m.model_root, m.customer_supplier,
                      f.name AS folder
               FROM models m LEFT JOIN folders f ON f.id=m.folder_id
               ORDER BY COALESCE(f.name,'') , m.model_root, m.model_version"""
        return pd.read_sql_query(q, c)

@st.cache_data(show_spinner=False)
def fp_for_model(mid: int):
    with conn() as c:
        return pd.read_sql_query(
            "SELECT * FROM first_piece WHERE model_id=? ORDER BY id DESC", c, params=(mid,)
        )

@st.cache_data(show_spinner=False)
def nc_for_model(mid: int):
    with conn() as c:
        return pd.read_sql_query(
            "SELECT * FROM nonconformities WHERE model_id=? ORDER BY "
            "CASE severity WHEN 'Critical' THEN 1 WHEN 'Major' THEN 2 ELSE 3 END, id DESC",
            c, params=(mid,)
        )

def bust():
    list_folders_df.clear(); list_models_df.clear(); fp_for_model.clear(); nc_for_model.clear()

# ------------------------- UI -------------------------

st.set_page_config(page_title="QC Portal", layout="wide")
st.title("🔎 QC Portal — Folders • Models • First Piece • Nonconformities")

# Sidebar: Folders & Models picker
with st.sidebar:
    st.subheader("📁 Folders & Models")
    folders_df = list_folders_df()
    models_df = list_models_df()

    folder_names = ["All"] + folders_df["name"].tolist()
    pick_folder = st.selectbox("Folder", folder_names, key="sb_folder")

    if pick_folder == "All":
        sub = models_df
    else:
        sub = models_df[models_df["folder"] == pick_folder]

    search = st.text_input("Search (model/version/customer)")
    if search.strip():
        s = search.lower().strip()
        mask = (
            models_df["model_version"].str.lower().str.contains(s, na=False)
            | models_df["model_root"].str.lower().str.contains(s, na=False)
            | models_df["customer_supplier"].fillna("").str.lower().str.contains(s, na=False)
        )
        sub = models_df[mask] if pick_folder == "All" else sub[mask]

    if sub.empty:
        st.info("No models yet.")
        pick_model_id = None
    else:
        labels = [f"{r.model_version}  •  {r.model_root}"
                  + (f" ({r.customer_supplier})" if r.customer_supplier else "")
                  for _, r in sub.iterrows()]
        pick_model_id = st.radio(
            "Select a model", options=sub["id"].tolist(), format_func=lambda i: labels[sub.index[sub['id']==i][0]],
            key="sb_model"
        )

    st.markdown("---")
    st.caption("Logged in as: **%s**" % username())

# Tabs
tab_fpa, tab_nc, tab_hist, tab_admin = st.tabs(["First Piece", "Non-conformity", "History", "Admin"])

# --------------------- First Piece Tab ----------------
with tab_fpa:
    st.subheader("🧪 First Piece (FPA)")
    if not pick_model_id:
        st.info("Pick a model in the left sidebar.")
    else:
        row = models_df[models_df["id"] == pick_model_id].iloc[0]
        col1, col2 = st.columns([2, 1])

        with col1:
            st.markdown(f"**Model version:** {row.model_version}  •  **Root:** `{row.model_root}`")
            mo = st.text_input("MO / Work Order")
            sn = st.text_input("Serial Number (first piece)")
            status = st.selectbox("Status", ["OK", "NG", "Pending"])
            review = st.text_area("Review notes (optional)", height=80)

        with col2:
            up_top = st.file_uploader("Top view photo", type=["jpg","jpeg","png"])
            up_bot = st.file_uploader("Bottom view photo", type=["jpg","jpeg","png"])

        if st.button("Save First Piece"):
            top_rel = save_image(row.model_root, up_top) if up_top else None
            bot_rel = save_image(row.model_root, up_bot) if up_bot else None
            with conn() as c:
                c.execute(
                    "INSERT INTO first_piece(created_at, model_id, model_root, sn, mo, status, review_notes, reporter, top_image, bottom_image) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.utcnow().isoformat(), int(row.id), row.model_root,
                        sn.strip(), mo.strip(), status, review.strip(), username(),
                        top_rel, bot_rel
                    ),
                )
                c.commit()
            bust()
            st.success("First Piece saved.")
            if status == "NG":
                post_teams_card(
                    f"FPA NG — {row.model_root} / {row.model_version}",
                    [
                        f"MO: {mo}",
                        f"SN: {sn}",
                        f"Reporter: {username()}",
                        f"Notes: {review or '-'}",
                    ],
                )

# ------------------ Nonconformity Tab ----------------
with tab_nc:
    st.subheader("🚨 Non-conformity")
    if not pick_model_id:
        st.info("Pick a model in the left sidebar.")
    else:
        row = models_df[models_df["id"] == pick_model_id].iloc[0]
        st.markdown(f"**Model version:** {row.model_version}  •  **Root:** `{row.model_root}`")
        c1,c2,c3 = st.columns(3)
        with c1:
            customer = st.text_input("Customer / Supplier", value=row.customer_supplier or "")
            mo_nc = st.text_input("MO / Work Order")
            line = st.text_input("Line")
            station = st.text_input("Work Station")
        with c2:
            dept = st.text_input("Department")
            unit_head = st.text_input("Unit Head")
            resp = st.text_input("Responsibility")
            discovery = st.text_input("Discovery Dept")
        with c3:
            source = st.text_input("Origin Source")
            cat = st.text_input("Defective Category (e.g., Soldering)")
            item = st.text_input("Defective Item (e.g., Polarity / Short / Open)")
            outflow = st.selectbox("Defective Outflow", ["None","OQC","Customer"])

        c4,c5,c6 = st.columns(3)
        with c4:
            dq = st.number_input("Defective Qty", min_value=0.0, step=1.0)
        with c5:
            iq = st.number_input("Inspection Qty", min_value=0.0, step=1.0)
        with c6:
            lq = st.number_input("Lot Qty", min_value=0.0, step=1.0)

        severity = st.selectbox("Severity", ["Critical","Major","Minor"])
        desc = st.text_area("Description of Nonconformity", height=120)

        photos = st.file_uploader("Upload photos (multiple OK)", type=["jpg","jpeg","png"], accept_multiple_files=True)

        if st.button("Submit Non-conformity"):
            cover = None
            saved = []
            for i, f in enumerate(photos or []):
                rel = save_image(row.model_root, f)
                saved.append(rel)
                if i == 0:
                    cover = rel
            with conn() as c:
                c.execute(
                    """INSERT INTO nonconformities(
                        created_at, model_id, model_root, customer_supplier, mo, line, work_station, department, unit_head, responsibility,
                        root_cause, corrective_action, discovery_dept, source, defective_category, defective_item, defective_outflow,
                        defective_qty, inspection_qty, lot_qty, severity, description, reporter, cover_image
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        datetime.utcnow().isoformat(), int(row.id), row.model_root, customer.strip(), mo_nc.strip(),
                        line.strip(), station.strip(), dept.strip(), unit_head.strip(), resp.strip(),
                        "", "", discovery.strip(), source.strip(), cat.strip(), item.strip(), outflow,
                        float(dq or 0), float(iq or 0), float(lq or 0), severity, desc.strip(), username(), cover
                    ),
                )
                nid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
                for rel in saved[1:] if saved else []:
                    c.execute("INSERT INTO nc_images(nc_id,image_path) VALUES(?,?)", (nid, rel))
                c.commit()
            bust()
            st.success("Non-conformity recorded.")
            post_teams_card(
                f"NC {severity} — {row.model_root} / {row.model_version}",
                [
                    f"MO: {mo_nc}",
                    f"Station: {station} | Line: {line}",
                    f"Qty: {dq}/{iq} (Lot {lq})",
                    f"Reporter: {username()}",
                    f"Desc: {desc[:300] + ('…' if len(desc)>300 else '')}",
                ],
            )

# ----------------------- History Tab -----------------
with tab_hist:
    st.subheader("📚 History")
    if not pick_model_id:
        st.info("Pick a model in the left sidebar.")
    else:
        row = models_df[models_df["id"] == pick_model_id].iloc[0]
        st.markdown(f"### {row.model_version}  •  `{row.model_root}`")

        st.markdown("#### First Piece")
        fp = fp_for_model(int(row.id))
        if fp.empty:
            st.caption("No FPA yet.")
        else:
            for _, r in fp.iterrows():
                with st.container(border=True):
                    st.caption(f"{r['created_at']} — MO: {r['mo']} — SN: {r['sn']} — {r['status']}")
                    st.write(r["review_notes"] or "")
                    cols = st.columns(2)
                    for i, key in enumerate(["top_image","bottom_image"]):
                        path = r[key]
                        if path:
                            p = DATA_DIR / path
                            if p.exists():
                                cols[i].image(str(p), use_container_width=True)

        st.markdown("#### Non-conformities (Critical → Major → Minor)")
        nc = nc_for_model(int(row.id))
        if nc.empty:
            st.caption("No NC yet.")
        else:
            for _, r in nc.iterrows():
                with st.container(border=True):
                    st.caption(
                        f"{r['created_at']} — Severity: {r['severity']} — MO: {r['mo']} "
                        f"— Station: {r['work_station']} — Qty: {r['defective_qty']}/{r['inspection_qty']}"
                    )
                    st.write(r["description"] or "")
                    if r["cover_image"]:
                        p = DATA_DIR / r["cover_image"]
                        if p.exists():
                            st.image(str(p), use_container_width=True)
                    # edit / delete (admins)
                    colA, colB = st.columns(2)
                    if colA.button("✏️ Edit", key=f"e{r['id']}"):
                        st.session_state["edit_nc"] = int(r["id"])
                    if colB.button("🗑️ Delete", key=f"d{r['id']}"):
                        with conn() as c:
                            c.execute("DELETE FROM nonconformities WHERE id=?", (int(r["id"]),))
                            c.commit()
                        bust()
                        st.experimental_rerun()

                    # simple inline edit for severity/description
                    if st.session_state.get("edit_nc") == int(r["id"]):
                        new_sev = st.selectbox("Severity", ["Critical","Major","Minor"],
                                               index=["Critical","Major","Minor"].index(r["severity"]),
                                               key=f"sev_{r['id']}")
                        new_desc = st.text_area("Description", value=r["description"] or "", key=f"desc_{r['id']}")
                        if st.button("Save changes", key=f"s{r['id']}"):
                            with conn() as c:
                                c.execute("UPDATE nonconformities SET severity=?, description=? WHERE id=?",
                                          (new_sev, new_desc.strip(), int(r["id"])))
                                c.commit()
                            st.session_state.pop("edit_nc", None)
                            bust()
                            st.success("Updated.")

# ------------------------ Admin Tab ------------------
with tab_admin:
    st.subheader("🛠️ Admin (folders • models • import)")
    st.caption("Lightweight management. No auth here; control access by who can run the app.")

    st.markdown("### Folders")
    cA, cB, cC = st.columns([2,2,1])
    with cA:
        newf = st.text_input("New folder name")
        if st.button("Create folder"):
            if newf.strip():
                with conn() as c:
                    c.execute("INSERT OR IGNORE INTO folders(name) VALUES(?)", (newf.strip(),))
                    c.commit()
                bust()
                st.success("Folder created.")
    with cB:
        if not folders_df.empty:
            oldf = st.selectbox("Rename which", options=folders_df["name"].tolist())
            newname = st.text_input("→ New name")
            if st.button("Rename folder"):
                with conn() as c:
                    c.execute("UPDATE folders SET name=? WHERE name=?", (newname.strip(), oldf))
                    c.commit()
                bust(); st.success("Renamed.")
    with cC:
        if not folders_df.empty:
            delf = st.selectbox("Delete", options=folders_df["name"].tolist(), key="del_folder")
            if st.button("Delete folder"):
                with conn() as c:
                    c.execute("DELETE FROM folders WHERE name=?", (delf,))
                    c.commit()
                bust(); st.success("Deleted.")

    st.markdown("### Models")
    m1,m2,m3 = st.columns([2,2,2])
    with m1:
        mv = st.text_input("Model version (full) e.g. 190A56980")
        mr = st.text_input("Model root (auto)", value=compute_model_root(mv), disabled=True)
        cust = st.text_input("Customer/Supplier")
        folder_for_new = st.selectbox("Folder", options=folders_df["name"].tolist(), index=0)
        if st.button("Add / Update model"):
            with conn() as c:
                folder_id = int(folders_df[folders_df["name"]==folder_for_new]["id"].iloc[0])
                c.execute(
                    "INSERT INTO models(model_version, model_root, customer_supplier, folder_id) VALUES(?,?,?,?) "
                    "ON CONFLICT(model_version) DO UPDATE SET model_root=excluded.model_root, "
                    "customer_supplier=excluded.customer_supplier, folder_id=excluded.folder_id",
                    (mv.strip(), compute_model_root(mv), cust.strip(), folder_id),
                )
                c.commit()
            bust(); st.success("Model saved/updated.")
    with m2:
        if not models_df.empty:
            move_model = st.selectbox("Move / rename model", options=models_df["model_version"].tolist())
            mv_new = st.text_input("→ New model version (optional)")
            move_to = st.selectbox("→ Move to folder", options=folders_df["name"].tolist(), key="moveto")
            if st.button("Apply move/rename"):
                with conn() as c:
                    md = models_df[models_df["model_version"]==move_model].iloc[0]
                    folder_id = int(folders_df[folders_df["name"]==move_to]["id"].iloc[0])
                    new_ver = (mv_new.strip() or md.model_version)
                    c.execute(
                        "UPDATE models SET model_version=?, model_root=?, folder_id=? WHERE id=?",
                        (new_ver, compute_model_root(new_ver), folder_id, int(md.id))
                    )
                    c.commit()
                bust(); st.success("Applied.")
    with m3:
        if not models_df.empty:
            del_model = st.selectbox("Delete model", options=models_df["model_version"].tolist(), key="delmodel")
            if st.button("Delete model (and its data)"):
                with conn() as c:
                    mid = int(models_df[models_df["model_version"]==del_model]["id"].iloc[0])
                    c.execute("DELETE FROM models WHERE id=?", (mid,))
                    c.commit()
                bust(); st.success("Model deleted.")

    st.markdown("### Import Non-conformities from Excel/CSV")
    st.caption("Header names auto-mapped if they contain these words: model, version, root, mo, line, station, dept, severity, desc, qty, etc.")
    up = st.file_uploader("Upload .xlsx or .csv", type=["xlsx","csv"])
    if up is not None:
        if up.name.lower().endswith(".csv"):
            df = pd.read_csv(up)
        else:
            df = pd.read_excel(up)
        st.dataframe(df.head(20), use_container_width=True)

        if st.button("Import rows"):
            imported = 0
            with conn() as c:
                for _, r in df.iterrows():
                    mv = str(r.get("ModelVersion") or r.get("model") or r.get("version") or "").strip()
                    mr = str(r.get("ModelRoot") or r.get("root") or compute_model_root(mv)).strip()
                    cust = str(r.get("CustomerSupplier") or r.get("customer") or "").strip()
                    if not (mv or mr):
                        continue
                    # upsert model into Unassigned
                    folder_id = 1
                    c.execute(
                        "INSERT INTO models(model_version, model_root, customer_supplier, folder_id) VALUES(?,?,?,?) "
                        "ON CONFLICT(model_version) DO UPDATE SET model_root=excluded.model_root, customer_supplier=excluded.customer_supplier",
                        (mv or mr, mr, cust, folder_id)
                    )
                    mid = c.execute("SELECT id FROM models WHERE model_version=?", (mv or mr,)).fetchone()[0]
                    # insert NC
                    c.execute(
                        "INSERT INTO nonconformities(created_at, model_id, model_root, customer_supplier, mo, line, work_station, department, "
                        "severity, description, reporter, defective_qty, inspection_qty, lot_qty) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            datetime.utcnow().isoformat(), mid, mr, cust,
                            str(r.get("MO") or r.get("mo") or ""), str(r.get("Line") or r.get("line") or ""),
                            str(r.get("WorkStation") or r.get("station") or ""), str(r.get("Department") or r.get("dept") or ""),
                            str(r.get("Severity") or r.get("severity") or "Major"),
                            str(r.get("Description") or r.get("desc") or ""),
                            username(),
                            float(r.get("DefectiveQty") or r.get("dq") or 0),
                            float(r.get("InspectionQty") or r.get("iq") or 0),
                            float(r.get("LotQty") or r.get("lq") or 0),
                        )
                    )
                    imported += 1
                c.commit()
            bust()
            st.success(f"Imported {imported} rows.")
