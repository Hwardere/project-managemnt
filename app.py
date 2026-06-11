#!/usr/bin/env python3
"""
Property Manager — a small, dependency-free web app.

Two roles:
  - admin  : can add / edit / delete properties and documents
  - worker : can edit any field and upload/replace documents, but cannot
             add new properties or delete anything

Data is stored in ./data/db.json ; uploaded files in ./data/uploads/<id>/<topic>.
Run:  python3 app.py   (then open http://localhost:8000)
"""

import os, json, cgi, html, secrets, threading, mimetypes, datetime, io, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "db.json")
TASKS_PATH = os.path.join(DATA_DIR, "tasks.json")
SHEET_PATH = os.path.join(DATA_DIR, "rent_tracker.json")

PORT = int(os.environ.get("PORT", "8000"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
WORKER_PASSWORD = os.environ.get("WORKER_PASSWORD", "team123")
MAX_UPLOAD = 25 * 1024 * 1024  # 25 MB

# Fixed document topics — one file each. Editable here.
TOPICS = [
    {"key": "deed",             "label": "Deed"},
    {"key": "insurance_policy", "label": "Insurance policy"},
    {"key": "tax_records",      "label": "Tax records"},
    {"key": "survey",           "label": "Survey"},
    {"key": "mortgage",         "label": "Mortgage / Loan docs"},
    {"key": "inspection",       "label": "Inspection report"},
    {"key": "warranty",         "label": "Warranty"},
]
LEASE_TOPIC = {"key": "lease", "label": "Lease agreement"}  # shown under Occupancy
ALL_TOPIC_KEYS = {t["key"] for t in TOPICS} | {LEASE_TOPIC["key"]}

# ----------------------------------------------------------------------------
# Storage
# ----------------------------------------------------------------------------
_lock = threading.Lock()
sessions = {}  # token -> role


def _ensure_dirs():
    os.makedirs(UPLOAD_DIR, exist_ok=True)


def _seed():
    return [
        {
            "id": _uid(),
            "address": "142 Maple Avenue, Austin, TX 78704",
            "nickname": "Maple duplex",
            "type": "Duplex / Multi-family",
            "insurance": {"provider": "State Farm", "policy": "SF-4471902",
                          "exp": "2026-07-15", "renew": "2026-07-01",
                          "premium": "1820", "pay": "2026-06-25"},
            "landscaping": {"company": "GreenScape LLC", "freq": "Bi-weekly",
                            "next": "2026-06-14", "pay": "2026-06-30"},
            "utilities": [
                {"type": "Electric", "provider": "Austin Energy", "account": "AE-88210345", "pay": "2026-06-18"},
                {"type": "Water", "provider": "Austin Water", "account": "AW-2200781", "pay": "2026-06-22"},
            ],
            "occupancy": {"occupied": True, "tenant": "J. Rivera",
                          "leaseStart": "2026-01-01", "leaseEnd": "2026-12-31", "rent": "2100"},
            "docs": {},
        },
        {
            "id": _uid(),
            "address": "907 Oakcrest Drive, Round Rock, TX 78664",
            "nickname": "Oakcrest rental",
            "type": "Single-family",
            "insurance": {"provider": "Allstate", "policy": "AL-99231-B",
                          "exp": "2026-06-20", "renew": "2026-06-10",
                          "premium": "1390", "pay": "2026-06-12"},
            "landscaping": {"company": "LawnPro", "freq": "Weekly",
                            "next": "2026-06-11", "pay": "2026-06-15"},
            "utilities": [
                {"type": "Electric", "provider": "Oncor", "account": "ON-33409921", "pay": "2026-06-19"},
            ],
            "occupancy": {"occupied": False, "tenant": "", "leaseStart": "", "leaseEnd": "", "rent": ""},
            "docs": {},
        },
    ]


def _uid():
    return "p_" + secrets.token_hex(5)


def load_db():
    if not os.path.exists(DB_PATH):
        db = _seed()
        save_db(db)
        return db
    with open(DB_PATH, "r") as f:
        return json.load(f)


def save_db(db):
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(db, f, indent=2)
    os.replace(tmp, DB_PATH)


def get_prop(db, pid):
    return next((p for p in db if p["id"] == pid), None)


def _tid():
    return "t_" + secrets.token_hex(5)


def _seed_tasks(db):
    p0 = db[0]["id"] if len(db) > 0 else ""
    p1 = db[1]["id"] if len(db) > 1 else ""
    return [
        {"id": _tid(), "title": "Replace HVAC filter", "propertyId": p0,
         "due": "2026-06-12", "notes": "Unit in hallway closet.", "done": False,
         "doneAt": "", "created": "2026-06-08"},
        {"id": _tid(), "title": "Get insurance renewal signed", "propertyId": p1,
         "due": "2026-06-11", "notes": "", "done": False, "doneAt": "", "created": "2026-06-08"},
        {"id": _tid(), "title": "Order new lockboxes (all properties)", "propertyId": "",
         "due": "2026-06-20", "notes": "Bulk order of 5.", "done": False,
         "doneAt": "", "created": "2026-06-08"},
    ]


def load_tasks():
    if not os.path.exists(TASKS_PATH):
        t = _seed_tasks(load_db())
        save_tasks(t)
        return t
    with open(TASKS_PATH, "r") as f:
        return json.load(f)


def save_tasks(tasks):
    tmp = TASKS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(tasks, f, indent=2)
    os.replace(tmp, TASKS_PATH)


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "PropertyManager/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # ---- helpers ----
    def _role(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "pm_session":
                    return sessions.get(v)
        return None

    def _send_json(self, obj, status=200, extra_headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n == 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _unauth(self):
        self._send_json({"error": "unauthorized"}, 401)

    # ---- GET ----
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/" or path == "/index.html":
            return self._serve_index()
        if path == "/api/bootstrap":
            role = self._role()
            if not role:
                return self._unauth()
            return self._send_json({"role": role, "topics": TOPICS, "leaseTopic": LEASE_TOPIC})
        if path == "/api/backup":
            if self._role() != "admin":
                return self._send_json({"error": "admin only"}, 403)
            return self._send_backup()
        if path == "/api/tasks":
            if not self._role():
                return self._unauth()
            return self._send_json(load_tasks())
        if path == "/api/sheet":
            if not self._role():
                return self._unauth()
            if os.path.exists(SHEET_PATH):
                with open(SHEET_PATH, "r") as f:
                    return self._send_json(json.load(f))
            return self._send_json({"empty": True})
        if path == "/api/properties":
            if not self._role():
                return self._unauth()
            db = load_db()
            summary = [{"id": p["id"], "address": p["address"],
                        "nickname": p.get("nickname", ""), "type": p.get("type", ""),
                        "occupied": p.get("occupancy", {}).get("occupied", False)} for p in db]
            summary.sort(key=lambda x: x["address"].lower())
            return self._send_json(summary)
        if path.startswith("/api/properties/"):
            parts = path.split("/")
            # /api/properties/<id>
            if len(parts) == 4:
                if not self._role():
                    return self._unauth()
                db = load_db()
                p = get_prop(db, parts[3])
                return self._send_json(p) if p else self._send_json({"error": "not found"}, 404)
            # /api/properties/<id>/docs/<topic>
            if len(parts) == 6 and parts[4] == "docs":
                return self._serve_doc(parts[3], parts[5])
        return self._send_json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/login":
            data = self._read_json()
            pw = data.get("password", "")
            role = None
            if pw == ADMIN_PASSWORD:
                role = "admin"
            elif pw == WORKER_PASSWORD:
                role = "worker"
            if not role:
                return self._send_json({"error": "Wrong password"}, 401)
            token = secrets.token_hex(16)
            sessions[token] = role
            cookie = f"pm_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000"
            return self._send_json({"role": role}, 200, {"Set-Cookie": cookie})

        if path == "/api/logout":
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                if part.strip().startswith("pm_session="):
                    sessions.pop(part.strip().split("=", 1)[1], None)
            return self._send_json({"ok": True}, 200,
                                   {"Set-Cookie": "pm_session=; Path=/; Max-Age=0"})

        role = self._role()
        if not role:
            return self._unauth()

        if path == "/api/properties":
            if role != "admin":
                return self._send_json({"error": "Only admins can add properties"}, 403)
            data = self._read_json()
            if not data.get("address", "").strip():
                return self._send_json({"error": "Address required"}, 400)
            data["id"] = _uid()
            data.setdefault("docs", {})
            with _lock:
                db = load_db()
                db.append(data)
                save_db(db)
            return self._send_json(data)

        if path == "/api/restore":
            if role != "admin":
                return self._send_json({"error": "admin only"}, 403)
            return self._do_restore()
        if path == "/api/tasks":
            data = self._read_json()
            if not data.get("title", "").strip():
                return self._send_json({"error": "Task title required"}, 400)
            task = {
                "id": _tid(),
                "title": data.get("title", "").strip(),
                "propertyId": data.get("propertyId", ""),
                "due": data.get("due", ""),
                "notes": data.get("notes", ""),
                "done": False, "doneAt": "",
                "created": datetime.date.today().isoformat(),
            }
            with _lock:
                tasks = load_tasks()
                tasks.append(task)
                save_tasks(tasks)
            return self._send_json(task)

        # /api/properties/<id>/docs/<topic>  (upload)
        parts = path.split("/")
        if len(parts) == 6 and parts[1] == "api" and parts[2] == "properties" and parts[4] == "docs":
            return self._upload_doc(parts[3], parts[5], role)

        return self._send_json({"error": "not found"}, 404)

    # ---- PUT (edit) ----
    def do_PUT(self):
        role = self._role()
        if not role:
            return self._unauth()
        path = urlsplit(self.path).path
        parts = path.split("/")
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "properties":
            pid = parts[3]
            data = self._read_json()
            with _lock:
                db = load_db()
                p = get_prop(db, pid)
                if not p:
                    return self._send_json({"error": "not found"}, 404)
                # workers and admins may edit fields; preserve id, docs, units,
                # and imported accounts (utilities are managed via re-import)
                for key in ("address", "nickname", "type", "insurance",
                            "landscaping", "occupancy"):
                    if key in data:
                        p[key] = data[key]
                save_db(db)
            return self._send_json(p)
        if len(parts) == 4 and parts[1] == "api" and parts[2] == "tasks":
            tkid = parts[3]
            data = self._read_json()
            with _lock:
                tasks = load_tasks()
                t = next((x for x in tasks if x["id"] == tkid), None)
                if not t:
                    return self._send_json({"error": "not found"}, 404)
                for key in ("title", "propertyId", "due", "notes"):
                    if key in data:
                        t[key] = data[key]
                if "done" in data:
                    t["done"] = bool(data["done"])
                    t["doneAt"] = datetime.date.today().isoformat() if t["done"] else ""
                save_tasks(tasks)
            return self._send_json(t)
        return self._send_json({"error": "not found"}, 404)

    # ---- DELETE ----
    def do_DELETE(self):
        role = self._role()
        if not role:
            return self._unauth()
        if role != "admin":
            return self._send_json({"error": "Only admins can delete"}, 403)
        path = urlsplit(self.path).path
        parts = path.split("/")
        # delete property
        if len(parts) == 4 and parts[2] == "properties":
            pid = parts[3]
            with _lock:
                db = load_db()
                p = get_prop(db, pid)
                if not p:
                    return self._send_json({"error": "not found"}, 404)
                db = [x for x in db if x["id"] != pid]
                save_db(db)
            d = os.path.join(UPLOAD_DIR, pid)
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    os.remove(os.path.join(d, fn))
                os.rmdir(d)
            with _lock:
                tasks = load_tasks()
                remaining = [t for t in tasks if t.get("propertyId") != pid]
                if len(remaining) != len(tasks):
                    save_tasks(remaining)
            return self._send_json({"ok": True})
        # delete a task
        if len(parts) == 4 and parts[2] == "tasks":
            tkid = parts[3]
            with _lock:
                tasks = load_tasks()
                save_tasks([t for t in tasks if t["id"] != tkid])
            return self._send_json({"ok": True})
        # delete a single document
        if len(parts) == 6 and parts[4] == "docs":
            return self._delete_doc(parts[3], parts[5])
        return self._send_json({"error": "not found"}, 404)

    # ---- documents ----
    def _upload_doc(self, pid, topic, role):
        if topic not in ALL_TOPIC_KEYS:
            return self._send_json({"error": "unknown topic"}, 400)
        clen = int(self.headers.get("Content-Length", "0"))
        if clen > MAX_UPLOAD + 8192:
            return self._send_json({"error": "File too large (max 25 MB)"}, 413)
        form = cgi.FieldStorage(
            fp=self.rfile, headers=self.headers,
            environ={"REQUEST_METHOD": "POST",
                     "CONTENT_TYPE": self.headers.get("Content-Type", "")})
        if "file" not in form:
            return self._send_json({"error": "no file"}, 400)
        item = form["file"]
        raw = item.file.read()
        if len(raw) > MAX_UPLOAD:
            return self._send_json({"error": "File too large (max 25 MB)"}, 413)
        with _lock:
            db = load_db()
            p = get_prop(db, pid)
            if not p:
                return self._send_json({"error": "not found"}, 404)
            d = os.path.join(UPLOAD_DIR, pid)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, topic), "wb") as f:
                f.write(raw)
            p.setdefault("docs", {})[topic] = {
                "name": item.filename or topic,
                "type": item.type or "application/octet-stream",
                "size": len(raw),
            }
            save_db(db)
            meta = p["docs"][topic]
        return self._send_json(meta)

    def _serve_doc(self, pid, topic):
        if not self._role():
            return self._unauth()
        db = load_db()
        p = get_prop(db, pid)
        if not p or topic not in p.get("docs", {}):
            return self._send_json({"error": "not found"}, 404)
        meta = p["docs"][topic]
        fpath = os.path.join(UPLOAD_DIR, pid, topic)
        if not os.path.exists(fpath):
            return self._send_json({"error": "not found"}, 404)
        with open(fpath, "rb") as f:
            raw = f.read()
        self.send_response(200)
        self.send_header("Content-Type", meta.get("type", "application/octet-stream"))
        self.send_header("Content-Length", str(len(raw)))
        fname = meta.get("name", topic).replace('"', "")
        self.send_header("Content-Disposition", f'inline; filename="{fname}"')
        self.end_headers()
        self.wfile.write(raw)

    def _delete_doc(self, pid, topic):
        with _lock:
            db = load_db()
            p = get_prop(db, pid)
            if not p:
                return self._send_json({"error": "not found"}, 404)
            p.get("docs", {}).pop(topic, None)
            save_db(db)
        fpath = os.path.join(UPLOAD_DIR, pid, topic)
        if os.path.exists(fpath):
            os.remove(fpath)
        return self._send_json({"ok": True})

    # ---- backup / restore (admin) ----
    def _send_backup(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("db.json", "tasks.json", "rent_tracker.json"):
                fp = os.path.join(DATA_DIR, name)
                if os.path.exists(fp):
                    z.write(fp, name)
            if os.path.isdir(UPLOAD_DIR):
                for root, _, files in os.walk(UPLOAD_DIR):
                    for fn in files:
                        full = os.path.join(root, fn)
                        z.write(full, os.path.relpath(full, DATA_DIR))
        raw = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Disposition",
                         'attachment; filename="property-manager-backup.zip"')
        self.end_headers()
        self.wfile.write(raw)

    def _do_restore(self):
        form = cgi.FieldStorage(
            fp=self.rfile, headers=self.headers,
            environ={"REQUEST_METHOD": "POST",
                     "CONTENT_TYPE": self.headers.get("Content-Type", "")})
        if "file" not in form:
            return self._send_json({"error": "no file"}, 400)
        raw = form["file"].file.read()
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            return self._send_json({"error": "not a valid .zip"}, 400)
        allowed = {"db.json", "tasks.json", "rent_tracker.json"}
        restored = 0
        with _lock:
            for member in zf.namelist():
                if member.endswith("/"):
                    continue
                norm = os.path.normpath(member)
                if norm.startswith("..") or os.path.isabs(norm):
                    continue  # path-traversal guard
                if not (norm in allowed or norm.startswith("uploads" + os.sep)):
                    continue
                dest = os.path.join(DATA_DIR, norm)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(zf.read(member))
                restored += 1
        return self._send_json({"ok": True, "restored": restored})

    # ---- frontend ----
    def _serve_index(self):
        with open(os.path.join(HERE, "index.html"), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    _ensure_dirs()
    load_db()  # create seed if missing
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("=" * 56)
    print("  Property Manager running")
    print(f"  Open:    http://localhost:{PORT}")
    print(f"  Admin password : {ADMIN_PASSWORD}")
    print(f"  Worker password: {WORKER_PASSWORD}")
    print("  (set ADMIN_PASSWORD / WORKER_PASSWORD env vars to change)")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
