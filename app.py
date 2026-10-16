from __future__ import annotations
import os, sqlite3, json, csv, io, shutil, hashlib, hmac, secrets, base64, smtplib, math
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import inch
from email.message import EmailMessage

BASE = Path(__file__).resolve().parent
# Railway persistent storage support. When TMP_STORAGE_DIR is set (for example /data),
# all customer records, signatures, uploads, PDFs, and backups live on the persistent volume.
STORAGE = Path(os.getenv("TMP_STORAGE_DIR", str(BASE))).resolve()
DATA = STORAGE / "data"
UPLOADS = STORAGE / "uploads"
REPORTS = STORAGE / "reports"
for p in (DATA, UPLOADS, REPORTS): p.mkdir(parents=True, exist_ok=True)
DB = DATA / "master_plumber.db"
# Seed the persistent database with the packaged starter DB on first boot.
PACKAGED_DB = BASE / "data" / "master_plumber.db"
if not DB.exists() and PACKAGED_DB.exists() and PACKAGED_DB != DB:
    shutil.copy2(PACKAGED_DB, DB)
SECRET = os.getenv("TMP_SECRET_KEY", "replace-this-secret-" + secrets.token_hex(16))
ADMIN_USER = os.getenv("TMP_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("TMP_ADMIN_PASSWORD", "changeme-now")

app = FastAPI(title="The Master Plumber")
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=False, same_site="lax")
app.mount("/static", StaticFiles(directory=BASE/"static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS), name="uploads")
templates = Jinja2Templates(directory=BASE/"templates")

@app.get("/health")
def health(): return {"ok": True}

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def now(): return datetime.now().isoformat(timespec="seconds")

def pw_hash(pw: str, salt: Optional[str]=None):
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return salt + "$" + dk.hex()

def pw_verify(pw: str, stored: str):
    salt, hx = stored.split("$", 1)
    return hmac.compare_digest(pw_hash(pw, salt).split("$",1)[1], hx)

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS customers(
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, email TEXT, billing_address TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS properties(
      id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
      address TEXT NOT NULL, city TEXT, state TEXT, zip TEXT, notes TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY, job_number TEXT UNIQUE NOT NULL, customer_id INTEGER NOT NULL REFERENCES customers(id),
      property_id INTEGER NOT NULL REFERENCES properties(id), status TEXT NOT NULL DEFAULT 'Open',
      scheduled_date TEXT, issue_type TEXT, urgency TEXT, customer_concern TEXT, prior_recommendation TEXT,
      professional_opinion TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agreements(
      id INTEGER PRIMARY KEY, job_id INTEGER UNIQUE NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      scope_json TEXT, terms_text TEXT, agree_scope INTEGER, agree_no_referral INTEGER, agree_limits INTEGER,
      signature_name TEXT, signature_path TEXT, signed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS assessments(
      id INTEGER PRIMARY KEY, job_id INTEGER UNIQUE NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      performed_json TEXT, observed_conditions TEXT, line_findings TEXT, problem_location TEXT, pipe_material TEXT,
      condition_rating TEXT, limitations TEXT, recommendation TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS photos(
      id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      file_path TEXT NOT NULL, caption TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS pricing(
      id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, description TEXT, default_price REAL NOT NULL,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invoices(
      id INTEGER PRIMARY KEY, invoice_number TEXT UNIQUE NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id),
      status TEXT NOT NULL DEFAULT 'Draft', subtotal REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0,
      total REAL NOT NULL DEFAULT 0, amount_paid REAL NOT NULL DEFAULT 0, notes TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS invoice_items(
      id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
      pricing_id INTEGER REFERENCES pricing(id), description TEXT NOT NULL, quantity REAL NOT NULL DEFAULT 1,
      unit_price REAL NOT NULL DEFAULT 0, line_total REAL NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS job_services(
      id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
      pricing_id INTEGER REFERENCES pricing(id), code TEXT, description TEXT NOT NULL, scope_text TEXT,
      quantity REAL NOT NULL DEFAULT 1, unit_price REAL NOT NULL DEFAULT 0, line_total REAL NOT NULL DEFAULT 0,
      source TEXT NOT NULL DEFAULT 'catalog', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS followups(
      id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
      job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE, due_date TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'Due', sent_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    """)
    def ensure_column(table, column, ddl):
        cols={r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    ensure_column("jobs","job_type","TEXT NOT NULL DEFAULT 'Independent Assessment'")
    ensure_column("jobs","service_description","TEXT")
    ensure_column("pricing","category","TEXT")
    ensure_column("pricing","tier","TEXT")
    ensure_column("pricing","estimated_hours","REAL NOT NULL DEFAULT 0")
    ensure_column("pricing","parts_allowance","REAL NOT NULL DEFAULT 0")
    ensure_column("pricing","is_add_on","INTEGER NOT NULL DEFAULT 0")
    ensure_column("agreements","agree_price","INTEGER NOT NULL DEFAULT 0")
    ensure_column("agreements","agree_terms","INTEGER NOT NULL DEFAULT 0")
    ensure_column("agreements","agree_esign","INTEGER NOT NULL DEFAULT 0")
    ensure_column("agreements","agree_warranty","INTEGER NOT NULL DEFAULT 0")

    # Import/update the flat-rate service catalog used by the field app.
    catalog=BASE / "data" / "service_catalog.csv"
    if catalog.exists():
        with catalog.open(newline="",encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                code=(row.get("service_code") or "").strip()
                if not code: continue
                vals=(row.get("service_name") or code, row.get("description") or "", float(row.get("default_price") or 0),
                      row.get("category") or "", row.get("tier") or "", float(row.get("estimated_hours") or 0),
                      float(row.get("parts_allowance") or 0), int(float(row.get("is_add_on") or 0)), int(float(row.get("active") or 1)), now(), now(), code)
                exists=conn.execute("SELECT id FROM pricing WHERE code=?",(code,)).fetchone()
                if exists:
                    conn.execute("UPDATE pricing SET name=?,description=?,default_price=?,category=?,tier=?,estimated_hours=?,parts_allowance=?,is_add_on=?,active=?,updated_at=? WHERE code=?", vals[:9]+(vals[10],vals[11]))
                else:
                    conn.execute("INSERT INTO pricing(name,description,default_price,category,tier,estimated_hours,parts_allowance,is_add_on,active,created_at,updated_at,code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", vals[:-1]+(code,))

    if not conn.execute("SELECT 1 FROM users WHERE username=?", (ADMIN_USER,)).fetchone():
        conn.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)", (ADMIN_USER,pw_hash(ADMIN_PASSWORD),now()))
    defaults = [
      ("ASSESS","Independent Sewer / Water Line Assessment","Independent professional assessment and written findings",495.00),
      ("CAMERA","Sewer Camera Inspection","Camera inspection used as part of an assessment",225.00),
      ("LOCATE","Electronic Line Locating","Electronic locating and surface marking",175.00),
      ("SECOND","Second Opinion Review","Review of prior recommendation, estimate, photos, and/or video",295.00),
      ("ADDHR","Additional Field Time (hour)","Additional assessment time beyond the included scope",150.00),
      ("TRAVEL","Extended Travel Charge","Travel outside standard service area when approved in advance",75.00),
    ]
    for code,name,desc,price in defaults:
        conn.execute("INSERT OR IGNORE INTO pricing(code,name,description,default_price,created_at,updated_at) VALUES(?,?,?,?,?,?)",(code,name,desc,price,now(),now()))
    settings = {
      "business_name":"The Master Plumber", "phone":"720-307-5753", "email":"info@themasterplumber.co",
      "website":"themasterplumber.co", "license":"MP 189571", "service_area":"Denver Metro Area",
      "tax_rate":"0", "job_prefix":"TMP", "invoice_prefix":"TMP-INV",
      "labor_rate":"175", "service_overhead":"110", "parts_markup":"0.40", "target_margin":"0.45"
    }
    for k,v in settings.items(): conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
    conn.commit(); conn.close()
init_db()

ASSESSMENT_TERMS = """1. INDEPENDENT ASSESSMENT ONLY. The Master Plumber is being hired to inspect, document, locate, and provide an independent professional opinion concerning the sewer service line and/or underground water service line described in the authorized scope. The Master Plumber does not perform the underground sewer or water-line excavation, repair, replacement, restoration, or landscaping work that may later be recommended.

2. NO REPAIR INCENTIVE OR REFERRAL COMPENSATION. The Master Plumber does not receive commissions, referral fees, kickbacks, or other compensation from a repair contractor and has no financial interest in whether the customer chooses to repair or replace the line. The customer remains responsible for choosing any repair contractor and deciding whether to obtain additional opinions.

3. SCOPE AND LIMITATIONS. The assessment is limited to the services specifically listed in this agreement and to conditions reasonably accessible and observable at the time of service. Concealed, inaccessible, intermittent, or out-of-scope conditions may not be identified. Camera and locating results may be affected by access, standing water, debris, offsets, pipe condition, pipe size or material, equipment reach, depth, soil, signal interference, utilities, site conditions, or other physical limitations.

4. LOCATION INFORMATION. Electronic locating and surface markings are professional estimates, not survey-grade measurements. The customer and any repair contractor remain responsible for required utility locates, excavation safety, permits, and verification before digging.

5. REPORT AND PROFESSIONAL OPINION. Findings, photos, video, measurements, and written recommendations reflect conditions observed at the time of the assessment. They are intended to help the customer make an informed decision and are not a guarantee of future pipe performance or a warranty that every defect has been identified.

6. ACCESS AND CUSTOMER RESPONSIBILITIES. The customer authorizes reasonable access to the property, cleanouts, valves, fixtures, mechanical areas, and other locations necessary to perform the authorized assessment. The customer will disclose known hazards, prior repairs, active leaks, and relevant property conditions. Destructive access, excavation, removal of finishes, and restoration are excluded unless separately stated in writing.

7. PRICE, PAYMENT, AND CHANGES. The approved price covers only the listed scope. Additional camera time, locating, return trips, unusual access, or other services outside the listed scope require the customer's approval before additional charges are incurred. Payment is due as stated on the agreement or invoice.

8. CUSTOMER RIGHTS. Nothing in this agreement waives any nonwaivable right or remedy provided by applicable law. If a transaction is subject to a statutory or regulatory cancellation right, the customer retains that right. Any legally required request for immediate emergency performance must be documented separately when applicable.

9. ELECTRONIC RECORDS AND ENTIRE AGREEMENT. The customer agrees that an electronic signature and electronic copy of this agreement may be used as the original. The authorized scope, approved price, these terms, and any written change order constitute the agreement for this service. Changes must be approved in writing or electronically by both parties."""

PLUMBING_TERMS = """1. AUTHORIZATION AND SCOPE. The customer authorizes The Master Plumber to perform only the plumbing services specifically listed in this agreement at the service property identified above. The listed service description, included scope, quantity, and approved price control what work is authorized. Work not listed is not included.

2. FLAT-RATE PRICE AND CHANGE ORDERS. The approved flat-rate price covers the stated scope under reasonably observable conditions. Concealed conditions, code-required corrections, failed or inoperable shutoff valves, unsafe existing work, unusual access, customer-requested changes, permit or inspection requirements, additional materials, or conditions that materially change the work are not automatically included. The Master Plumber will explain the additional work and obtain customer approval before adding charges, except for work reasonably necessary to leave the property in a safe condition when immediate action is required and the customer cannot be reached.

3. ACCESS, SHUTOFFS, AND EXISTING CONDITIONS. The customer will provide safe and reasonable access to the work area and authorize necessary water, gas, electrical, and fixture shutoffs related to the approved work. Existing piping, valves, fixtures, finishes, or connections may be brittle, corroded, deteriorated, improperly installed, or otherwise prone to failure when disturbed. The Master Plumber is responsible for damage caused by its failure to perform the authorized work with reasonable care, but not for preexisting defects or failures that could not reasonably be identified before work began.

4. PERMITS, CODE, AND INSPECTIONS. When a permit or inspection is legally required, the work will be performed subject to the applicable authority having jurisdiction. Permit fees, inspection fees, code upgrades, or corrections to existing noncompliant conditions are included only when specifically stated in the scope or price. The customer will not be charged for additional code-related work without approval.

5. MATERIALS AND CUSTOMER-SUPPLIED EQUIPMENT. Materials supplied by The Master Plumber will be appropriate for the authorized work. Products, fixtures, appliances, water heaters, valves, parts, and other equipment carry the warranty provided by the applicable manufacturer, and the manufacturer's written warranty controls product coverage, duration, registration, parts availability, and replacement. If the customer supplies a product, the customer is responsible for product selection, completeness, compatibility, and manufacturer defects unless The Master Plumber specifically agrees otherwise in writing. Additional labor caused by missing, defective, incompatible, or damaged customer-supplied materials may require an approved change.

6. OPENINGS, FINISHES, AND CLEANUP. Unless specifically included in the written scope, pricing does not include finished drywall repair, texture, paint, tile, cabinetry, flooring, masonry, landscaping, concrete, or other finish restoration. The Master Plumber will perform normal work-area cleanup associated with the authorized plumbing work.

7. ONE-YEAR LABOR WARRANTY AND SERVICE CONCERNS. The Master Plumber warrants its labor and workmanship on ordinary plumbing services for one (1) year from the date the authorized work is completed. If a covered workmanship issue is reported during that period, The Master Plumber will be given a reasonable opportunity to inspect the work and, if the issue is covered, correct its workmanship without an additional labor charge. This labor warranty applies only to the work performed by The Master Plumber and does not extend or replace a product manufacturer's warranty. Product defects, manufacturer failures, normal wear, misuse, abuse, freezing, water-quality or water-pressure conditions, preexisting conditions, failures outside the authorized scope, and work altered or repaired by others are not covered by the one-year labor warranty unless The Master Plumber expressly agrees otherwise in writing. Products and equipment carry their own manufacturer warranty. After the one-year labor warranty expires, labor to diagnose, remove, reinstall, or replace a product covered by a manufacturer warranty is chargeable unless otherwise stated in writing.

8. PAYMENT. Payment is due according to the approved agreement and invoice. Deposits, progress payments, or other payment arrangements apply only when shown in writing. The customer is responsible for charges the customer or the customer's authorized representative approves. The Master Plumber will provide an itemized record of approved changes.

9. CANCELLATION AND CUSTOMER RIGHTS. Nothing in this agreement waives any nonwaivable right or remedy provided by applicable law. If a transaction is subject to a statutory or regulatory cancellation right, the customer retains that right. If the customer requests immediate emergency work in circumstances where a separate written emergency request or waiver is legally required, that request must be documented separately; this agreement by itself is not intended to waive a statutory cancellation right.

10. UNDERGROUND SEWER AND WATER-SERVICE WORK. Ordinary plumbing services do not include excavation, repair, replacement, or restoration of underground sewer service lines or underground water service lines unless The Master Plumber later changes its business model and expressly agrees to that work in a separate written contract. The company's sewer and underground water-line services are currently limited to independent assessment, camera inspection, locating, documentation, and professional advice.

11. ELECTRONIC SIGNATURES AND ENTIRE AGREEMENT. The customer agrees that an electronic signature and electronic copy of this agreement may be used as the original. The authorized scope, approved price, these terms, and any written change order constitute the agreement for this service. Verbal statements do not change the agreement unless confirmed in writing or electronically."""

def require_login(request: Request):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=401)

def setting(conn, key, default=""):
    r=conn.execute("SELECT value FROM settings WHERE key=?",(key,)).fetchone(); return r[0] if r else default

def next_seq(conn, table, field, prefix):
    year=date.today().year
    like=f"{prefix}-{year}-%"
    r=conn.execute(f"SELECT {field} FROM {table} WHERE {field} LIKE ? ORDER BY id DESC LIMIT 1",(like,)).fetchone()
    n=1
    if r:
        try: n=int(r[0].split("-")[-1])+1
        except: pass
    return f"{prefix}-{year}-{n:05d}"

def context(request, **kwargs):
    c={"request":request, "current_user":request.session.get("username")}
    conn=db();
    c["biz"]={k:setting(conn,k) for k in ["business_name","phone","email","website","license","service_area"]}
    conn.close(); c.update(kwargs); return c

@app.exception_handler(401)
async def unauth_handler(request, exc): return RedirectResponse("/login",303)

@app.get("/login", response_class=HTMLResponse)
def login_page(request:Request): return templates.TemplateResponse(request, "login.html", context(request,error=None))

@app.post("/login")
def login(request:Request, username:str=Form(...), password:str=Form(...)):
    conn=db(); u=conn.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone(); conn.close()
    if not u or not pw_verify(password,u["password_hash"]):
        return templates.TemplateResponse(request, "login.html", context(request,error="Invalid username or password."), status_code=400)
    request.session["user_id"]=u["id"]; request.session["username"]=u["username"]
    return RedirectResponse("/",303)

@app.get("/logout")
def logout(request:Request): request.session.clear(); return RedirectResponse("/login",303)

@app.get("/", response_class=HTMLResponse)
def dashboard(request:Request):
    require_login(request); conn=db()
    stats={
      "open":conn.execute("SELECT COUNT(*) FROM jobs WHERE status!='Closed'").fetchone()[0],
      "customers":conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
      "unpaid":conn.execute("SELECT COALESCE(SUM(total-amount_paid),0) FROM invoices WHERE status NOT IN ('Paid','Void')").fetchone()[0],
      "month":conn.execute("SELECT COALESCE(SUM(amount_paid),0) FROM invoices WHERE substr(updated_at,1,7)=?",(date.today().isoformat()[:7],)).fetchone()[0],
      "followups":conn.execute("SELECT COUNT(*) FROM followups WHERE status!='Sent' AND due_date<=?",(date.today().isoformat(),)).fetchone()[0]
    }
    jobs=conn.execute("""SELECT j.*, c.name customer_name, p.address FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN properties p ON p.id=j.property_id ORDER BY j.id DESC LIMIT 12""").fetchall()
    due=conn.execute("""SELECT f.*,c.name customer_name,c.email,j.job_number FROM followups f JOIN customers c ON c.id=f.customer_id LEFT JOIN jobs j ON j.id=f.job_id WHERE f.status!='Sent' ORDER BY f.due_date LIMIT 8""").fetchall()
    conn.close(); return templates.TemplateResponse(request, "dashboard.html", context(request,stats=stats,jobs=jobs,due_followups=due))

@app.get("/jobs", response_class=HTMLResponse)
def jobs_list(request:Request, q:str=""):
    require_login(request); conn=db(); term=f"%{q}%"
    rows=conn.execute("""SELECT j.*, c.name customer_name, p.address FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN properties p ON p.id=j.property_id WHERE ?='' OR j.job_number LIKE ? OR c.name LIKE ? OR p.address LIKE ? ORDER BY j.id DESC""",(q,term,term,term)).fetchall(); conn.close()
    return templates.TemplateResponse(request, "jobs.html", context(request,jobs=rows,q=q))

@app.get("/jobs/new", response_class=HTMLResponse)
def job_new(request:Request):
    require_login(request); conn=db(); pricing=conn.execute("SELECT * FROM pricing WHERE active=1 ORDER BY id").fetchall(); conn.close()
    return templates.TemplateResponse(request, "job_new.html", context(request,pricing=pricing))

@app.post("/jobs/new")
async def job_create(request:Request,
 customer_name:str=Form(...), phone:str=Form(""), email:str=Form(""),
 property_address:str=Form(...), city:str=Form(""), state:str=Form("CO"), zip:str=Form(""),
 scheduled_date:str=Form(""), job_type:str=Form("Ordinary Plumbing"), issue_type:str=Form(""), urgency:str=Form("Routine"), customer_concern:str=Form(""), prior_recommendation:str=Form(""), service_description:str=Form("")):
    require_login(request); conn=db(); cur=conn.cursor();
    cur.execute("INSERT INTO customers(name,phone,email,created_at) VALUES(?,?,?,?)",(customer_name,phone,email,now())); cid=cur.lastrowid
    cur.execute("INSERT INTO properties(customer_id,address,city,state,zip,created_at) VALUES(?,?,?,?,?,?)",(cid,property_address,city,state,zip,now())); pid=cur.lastrowid
    jnum=next_seq(conn,"jobs","job_number",setting(conn,"job_prefix","TMP"))
    cur.execute("INSERT INTO jobs(job_number,customer_id,property_id,scheduled_date,job_type,issue_type,urgency,customer_concern,prior_recommendation,service_description,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(jnum,cid,pid,scheduled_date,job_type,issue_type,urgency,customer_concern,prior_recommendation,service_description,now(),now())); jid=cur.lastrowid
    conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}/scope",303)


def get_job_bundle(conn,jid):
    job=conn.execute("""SELECT j.*,c.name customer_name,c.phone,c.email,p.address,p.city,p.state,p.zip FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN properties p ON p.id=j.property_id WHERE j.id=?""",(jid,)).fetchone()
    if not job: raise HTTPException(404)
    agreement=conn.execute("SELECT * FROM agreements WHERE job_id=?",(jid,)).fetchone()
    assessment=conn.execute("SELECT * FROM assessments WHERE job_id=?",(jid,)).fetchone()
    photos=conn.execute("SELECT * FROM photos WHERE job_id=? ORDER BY id",(jid,)).fetchall()
    invoice=conn.execute("SELECT * FROM invoices WHERE job_id=? ORDER BY id DESC LIMIT 1",(jid,)).fetchone()
    return job,agreement,assessment,photos,invoice

@app.get("/jobs/{jid}", response_class=HTMLResponse)
def job_detail(request:Request,jid:int):
    require_login(request); conn=db(); job,a,s,photos,inv=get_job_bundle(conn,jid); conn.close()
    return templates.TemplateResponse(request, "job_detail.html", context(request,job=job,agreement=a,assessment=s,photos=photos,invoice=inv))

@app.get("/jobs/{jid}/scope", response_class=HTMLResponse)
def scope_page(request:Request,jid:int):
    require_login(request); conn=db(); job,a,sx,p,inv=get_job_bundle(conn,jid)
    catalog=conn.execute("SELECT * FROM pricing WHERE active=1 ORDER BY category,name").fetchall()
    services=conn.execute("SELECT * FROM job_services WHERE job_id=? ORDER BY id",(jid,)).fetchall()
    assumptions={k:float(setting(conn,k,d)) for k,d in [("labor_rate","175"),("service_overhead","110"),("parts_markup","0.40"),("target_margin","0.45")]}
    conn.close(); return templates.TemplateResponse(request, "scope.html", context(request,job=job,catalog=catalog,services=services,assumptions=assumptions))

@app.post("/jobs/{jid}/scope/add")
def scope_add(request:Request,jid:int, pricing_id:int=Form(0), quantity:float=Form(1), custom_description:str=Form(""), custom_scope:str=Form(""), labor_hours:float=Form(0), parts_cost:float=Form(0), custom_price:str=Form("")):
    require_login(request); conn=db(); pricing=None
    if pricing_id: pricing=conn.execute("SELECT * FROM pricing WHERE id=?",(pricing_id,)).fetchone()
    if pricing:
        desc=pricing["name"]; scope=pricing["description"] or ""; unit=float(custom_price) if custom_price.strip() else float(pricing["default_price"]); source="catalog"; code=pricing["code"]
    else:
        desc=custom_description.strip() or "Custom plumbing service"; scope=custom_scope.strip(); code="CUSTOM"; source="calculated"
        lr=float(setting(conn,"labor_rate","175")); overhead=float(setting(conn,"service_overhead","110")); markup=float(setting(conn,"parts_markup","0.40")); margin=float(setting(conn,"target_margin","0.45"))
        cost=(labor_hours*lr)+(parts_cost*(1+markup))+overhead
        suggested=cost/max(0.01,1-margin)
        unit=float(custom_price) if custom_price.strip() else math.ceil(suggested/5)*5
    lt=round(float(quantity)*unit,2)
    conn.execute("INSERT INTO job_services(job_id,pricing_id,code,description,scope_text,quantity,unit_price,line_total,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(jid,pricing_id or None,code,desc,scope,quantity,unit,lt,source,now()))
    conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}/scope",303)

@app.post("/jobs/{jid}/scope/{sid}/delete")
def scope_delete(request:Request,jid:int,sid:int):
    require_login(request); conn=db(); conn.execute("DELETE FROM job_services WHERE id=? AND job_id=?",(sid,jid)); conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}/scope",303)

@app.get("/jobs/{jid}/agreement", response_class=HTMLResponse)
def agreement_page(request:Request,jid:int):
    require_login(request); conn=db(); job,a,sx,p,i=get_job_bundle(conn,jid); services=conn.execute("SELECT * FROM job_services WHERE job_id=? ORDER BY id",(jid,)).fetchall(); total=sum(float(x["line_total"]) for x in services)
    terms=ASSESSMENT_TERMS if job["job_type"]=="Independent Assessment" else PLUMBING_TERMS
    conn.close(); return templates.TemplateResponse(request, "agreement.html", context(request,job=job,agreement=a,terms=terms,services=services,total=total,error=request.query_params.get("error")))

@app.post("/jobs/{jid}/agreement")
async def agreement_save(request:Request,jid:int, agree_scope:Optional[str]=Form(None), agree_price:Optional[str]=Form(None), agree_terms:Optional[str]=Form(None), agree_no_referral:Optional[str]=Form(None), agree_limits:Optional[str]=Form(None), agree_warranty:Optional[str]=Form(None), agree_esign:Optional[str]=Form(None), signature_name:str=Form(""), signature_data:str=Form("")):
    require_login(request); conn=db(); job=conn.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone(); services=conn.execute("SELECT * FROM job_services WHERE job_id=? ORDER BY id",(jid,)).fetchall(); path=None
    if not job:
        conn.close(); raise HTTPException(404)
    old=conn.execute("SELECT * FROM agreements WHERE job_id=?",(jid,)).fetchone()
    if not services:
        conn.close(); return RedirectResponse(f"/jobs/{jid}/agreement?error=Add%20at%20least%20one%20priced%20service%20before%20signing.",303)
    if not signature_name.strip():
        conn.close(); return RedirectResponse(f"/jobs/{jid}/agreement?error=Customer%20printed%20name%20is%20required.",303)
    if not signature_data.startswith("data:image/") and not (old and old["signature_path"]):
        conn.close(); return RedirectResponse(f"/jobs/{jid}/agreement?error=Customer%20signature%20is%20required.",303)
    if signature_data.startswith("data:image/"):
        raw=base64.b64decode(signature_data.split(",",1)[1]); fn=f"signature_job_{jid}.png"; (UPLOADS/fn).write_bytes(raw); path=fn
    if old and not path: path=old["signature_path"]
    terms=ASSESSMENT_TERMS if job["job_type"]=="Independent Assessment" else PLUMBING_TERMS
    scope_payload=[{"code":x["code"],"description":x["description"],"scope":x["scope_text"],"quantity":x["quantity"],"unit_price":x["unit_price"],"line_total":x["line_total"]} for x in services]
    no_ref=int(bool(agree_no_referral)) if job["job_type"]=="Independent Assessment" else 0
    warranty_ack=int(bool(agree_warranty)) if job["job_type"]!="Independent Assessment" else 0
    vals=(json.dumps(scope_payload),terms,int(bool(agree_scope)),int(bool(agree_price)),int(bool(agree_terms)),no_ref,int(bool(agree_limits)),warranty_ack,int(bool(agree_esign)),signature_name,path,now() if signature_name else None,now(),jid)
    if old:
        conn.execute("UPDATE agreements SET scope_json=?,terms_text=?,agree_scope=?,agree_price=?,agree_terms=?,agree_no_referral=?,agree_limits=?,agree_warranty=?,agree_esign=?,signature_name=?,signature_path=?,signed_at=?,updated_at=? WHERE job_id=?",vals)
    else:
        conn.execute("INSERT INTO agreements(scope_json,terms_text,agree_scope,agree_price,agree_terms,agree_no_referral,agree_limits,agree_warranty,agree_esign,signature_name,signature_path,signed_at,created_at,updated_at,job_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(vals[0],vals[1],vals[2],vals[3],vals[4],vals[5],vals[6],vals[7],vals[8],vals[9],vals[10],vals[11],now(),vals[12],jid))
    conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}",303)

@app.get("/jobs/{jid}/assessment", response_class=HTMLResponse)
def assessment_page(request:Request,jid:int):
    require_login(request); conn=db(); job,a,s,photos,i=get_job_bundle(conn,jid); conn.close()
    return templates.TemplateResponse(request, "assessment.html", context(request,job=job,assessment=s,photos=photos))

@app.post("/jobs/{jid}/assessment")
async def assessment_save(request:Request,jid:int, performed:str=Form(""), observed_conditions:str=Form(""), line_findings:str=Form(""), problem_location:str=Form(""), pipe_material:str=Form(""), condition_rating:str=Form(""), limitations:str=Form(""), recommendation:str=Form(""), professional_opinion:str=Form(""), photos:list[UploadFile]=File(default=[])):
    require_login(request); conn=db(); old=conn.execute("SELECT id FROM assessments WHERE job_id=?",(jid,)).fetchone(); perf=json.dumps([x for x in performed.split("|") if x])
    if old: conn.execute("UPDATE assessments SET performed_json=?,observed_conditions=?,line_findings=?,problem_location=?,pipe_material=?,condition_rating=?,limitations=?,recommendation=?,updated_at=? WHERE job_id=?",(perf,observed_conditions,line_findings,problem_location,pipe_material,condition_rating,limitations,recommendation,now(),jid))
    else: conn.execute("INSERT INTO assessments(job_id,performed_json,observed_conditions,line_findings,problem_location,pipe_material,condition_rating,limitations,recommendation,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(jid,perf,observed_conditions,line_findings,problem_location,pipe_material,condition_rating,limitations,recommendation,now(),now()))
    conn.execute("UPDATE jobs SET professional_opinion=?,updated_at=? WHERE id=?",(professional_opinion,now(),jid))
    for up in photos:
        if not up.filename: continue
        ext=Path(up.filename).suffix.lower() or ".jpg"; fn=f"job_{jid}_{datetime.now().strftime('%Y%m%d%H%M%S%f')}{ext}"; data=await up.read(); (UPLOADS/fn).write_bytes(data); conn.execute("INSERT INTO photos(job_id,file_path,created_at) VALUES(?,?,?)",(jid,fn,now()))
    conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}",303)

@app.post("/photos/{pid}/delete")
def photo_delete(request:Request,pid:int):
    require_login(request); conn=db(); p=conn.execute("SELECT * FROM photos WHERE id=?",(pid,)).fetchone();
    if p:
        try:(UPLOADS/p["file_path"]).unlink(missing_ok=True)
        except:pass
        jid=p["job_id"]; conn.execute("DELETE FROM photos WHERE id=?",(pid,)); conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}/assessment",303)
    conn.close(); raise HTTPException(404)

@app.get("/jobs/{jid}/invoice", response_class=HTMLResponse)
def invoice_page(request:Request,jid:int):
    require_login(request); conn=db(); job,a,s,photos,inv=get_job_bundle(conn,jid); pricing=conn.execute("SELECT * FROM pricing WHERE active=1 ORDER BY id").fetchall(); items=[]
    if inv: items=conn.execute("SELECT ii.*,p.code FROM invoice_items ii LEFT JOIN pricing p ON p.id=ii.pricing_id WHERE invoice_id=?",(inv["id"],)).fetchall()
    conn.close(); return templates.TemplateResponse(request, "invoice.html", context(request,job=job,invoice=inv,items=items,pricing=pricing))

@app.post("/jobs/{jid}/invoice")
def invoice_save(request:Request,jid:int, pricing_ids:list[int]=Form(default=[]), quantities:list[float]=Form(default=[]), unit_prices:list[float]=Form(default=[]), descriptions:list[str]=Form(default=[]), status:str=Form("Draft"), amount_paid:float=Form(0), notes:str=Form("")):
    require_login(request); conn=db(); inv=conn.execute("SELECT * FROM invoices WHERE job_id=? ORDER BY id DESC LIMIT 1",(jid,)).fetchone()
    subtotal=0; rows=[]
    n=max(len(descriptions),len(pricing_ids),len(unit_prices),len(quantities)) if any([descriptions,pricing_ids,unit_prices,quantities]) else 0
    for idx in range(n):
        desc=descriptions[idx] if idx<len(descriptions) else "Service"
        qty=float(quantities[idx] if idx<len(quantities) else 1)
        price=float(unit_prices[idx] if idx<len(unit_prices) else 0)
        pid=pricing_ids[idx] if idx<len(pricing_ids) else None
        lt=round(qty*price,2); subtotal+=lt; rows.append((pid,desc,qty,price,lt))
    tax_rate=float(setting(conn,"tax_rate","0") or 0); tax=round(subtotal*tax_rate/100,2); total=round(subtotal+tax,2)
    if inv:
        iid=inv["id"]; conn.execute("UPDATE invoices SET status=?,subtotal=?,tax=?,total=?,amount_paid=?,notes=?,updated_at=? WHERE id=?",(status,subtotal,tax,total,amount_paid,notes,now(),iid)); conn.execute("DELETE FROM invoice_items WHERE invoice_id=?",(iid,))
    else:
        ino=next_seq(conn,"invoices","invoice_number",setting(conn,"invoice_prefix","TMP-INV")); cur=conn.cursor(); cur.execute("INSERT INTO invoices(invoice_number,job_id,status,subtotal,tax,total,amount_paid,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(ino,jid,status,subtotal,tax,total,amount_paid,notes,now(),now())); iid=cur.lastrowid
    for r in rows: conn.execute("INSERT INTO invoice_items(invoice_id,pricing_id,description,quantity,unit_price,line_total) VALUES(?,?,?,?,?,?)",(iid,*r))
    conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}/invoice",303)

@app.get("/customers", response_class=HTMLResponse)
def customers_page(request:Request):
    require_login(request); conn=db(); rows=conn.execute("""SELECT c.*,COUNT(DISTINCT j.id) job_count,MAX(j.created_at) last_job FROM customers c LEFT JOIN jobs j ON j.customer_id=c.id GROUP BY c.id ORDER BY c.name""").fetchall(); conn.close()
    return templates.TemplateResponse(request, "customers.html", context(request,customers=rows))

@app.get("/customers/{cid}", response_class=HTMLResponse)
def customer_detail(request:Request,cid:int):
    require_login(request); conn=db(); c=conn.execute("SELECT * FROM customers WHERE id=?",(cid,)).fetchone();
    if not c: conn.close(); raise HTTPException(404)
    jobs=conn.execute("""SELECT j.*,p.address FROM jobs j JOIN properties p ON p.id=j.property_id WHERE j.customer_id=? ORDER BY j.id DESC""",(cid,)).fetchall(); followups=conn.execute("SELECT * FROM followups WHERE customer_id=? ORDER BY due_date DESC",(cid,)).fetchall(); conn.close()
    return templates.TemplateResponse(request, "customer_detail.html", context(request,customer=c,jobs=jobs,followups=followups))

@app.get("/jobs/{jid}/followups", response_class=HTMLResponse)
def followups_page(request:Request,jid:int):
    require_login(request); conn=db(); job,a,sx,p,inv=get_job_bundle(conn,jid); rows=conn.execute("SELECT * FROM followups WHERE job_id=? ORDER BY due_date",(jid,)).fetchall(); conn.close()
    return templates.TemplateResponse(request, "followups.html", context(request,job=job,followups=rows,error=request.query_params.get("error")))

@app.post("/jobs/{jid}/followups/add")
def followup_add(request:Request,jid:int,due_date:str=Form(...),subject:str=Form(...),body:str=Form(...)):
    require_login(request); conn=db(); job=conn.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone(); conn.execute("INSERT INTO followups(customer_id,job_id,due_date,subject,body,status,created_at,updated_at) VALUES(?,?,?,?,?,'Due',?,?)",(job["customer_id"],jid,due_date,subject,body,now(),now())); conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{jid}/followups",303)

def send_followup_email(to_email,subject,body):
    host=os.getenv("TMP_SMTP_HOST",""); user=os.getenv("TMP_SMTP_USER",""); pw=os.getenv("TMP_SMTP_PASSWORD",""); from_email=os.getenv("TMP_SMTP_FROM",user); port=int(os.getenv("TMP_SMTP_PORT","587"))
    if not host or not from_email: raise RuntimeError("Email is not configured. Set TMP_SMTP_HOST, TMP_SMTP_PORT, TMP_SMTP_USER, TMP_SMTP_PASSWORD and TMP_SMTP_FROM.")
    msg=EmailMessage(); msg["From"]=from_email; msg["To"]=to_email; msg["Subject"]=subject; msg.set_content(body)
    with smtplib.SMTP(host,port,timeout=20) as smtp:
        smtp.starttls()
        if user: smtp.login(user,pw)
        smtp.send_message(msg)

@app.post("/followups/{fid}/send")
def followup_send(request:Request,fid:int):
    require_login(request); conn=db(); row=conn.execute("""SELECT f.*,c.email,j.id job_id2 FROM followups f JOIN customers c ON c.id=f.customer_id LEFT JOIN jobs j ON j.id=f.job_id WHERE f.id=?""",(fid,)).fetchone()
    if not row: conn.close(); raise HTTPException(404)
    try:
        if not row["email"]: raise RuntimeError("Customer has no email address on file.")
        send_followup_email(row["email"],row["subject"],row["body"]); conn.execute("UPDATE followups SET status='Sent',sent_at=?,updated_at=? WHERE id=?",(now(),now(),fid)); conn.commit(); conn.close(); return RedirectResponse(f"/jobs/{row['job_id2']}/followups",303)
    except Exception as e:
        jid=row["job_id2"]; conn.close(); return RedirectResponse(f"/jobs/{jid}/followups?error={str(e).replace(' ','%20')}",303)

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request:Request):
    require_login(request); conn=db(); rows=conn.execute("SELECT * FROM pricing ORDER BY active DESC,id").fetchall(); conn.close(); return templates.TemplateResponse(request, "pricing.html", context(request,pricing=rows))

@app.post("/pricing/add")
def pricing_add(request:Request, code:str=Form(...), name:str=Form(...), description:str=Form(""), default_price:float=Form(...)):
    require_login(request); conn=db(); conn.execute("INSERT INTO pricing(code,name,description,default_price,created_at,updated_at) VALUES(?,?,?,?,?,?)",(code.upper(),name,description,default_price,now(),now())); conn.commit(); conn.close(); return RedirectResponse("/pricing",303)

@app.post("/pricing/{pid}/update")
def pricing_update(request:Request,pid:int,name:str=Form(...),description:str=Form(""),default_price:float=Form(...),active:Optional[str]=Form(None)):
    require_login(request); conn=db(); conn.execute("UPDATE pricing SET name=?,description=?,default_price=?,active=?,updated_at=? WHERE id=?",(name,description,default_price,int(bool(active)),now(),pid)); conn.commit(); conn.close(); return RedirectResponse("/pricing",303)

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request:Request):
    require_login(request); conn=db(); rows={r["key"]:r["value"] for r in conn.execute("SELECT * FROM settings").fetchall()}; conn.close(); return templates.TemplateResponse(request, "settings.html", context(request,settings=rows))

@app.post("/settings")
def settings_save(request:Request,business_name:str=Form(...),phone:str=Form(...),email:str=Form(...),website:str=Form(...),license:str=Form(...),service_area:str=Form(...),tax_rate:str=Form("0"),labor_rate:str=Form("175"),service_overhead:str=Form("110"),parts_markup:str=Form("0.40"),target_margin:str=Form("0.45")):
    require_login(request); conn=db(); vals=locals().copy();
    for k in ["business_name","phone","email","website","license","service_area","tax_rate","labor_rate","service_overhead","parts_markup","target_margin"]: conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,vals[k]))
    conn.commit(); conn.close(); return RedirectResponse("/settings",303)

@app.post("/settings/password")
def password_change(request:Request,current_password:str=Form(...),new_password:str=Form(...)):
    require_login(request); conn=db(); u=conn.execute("SELECT * FROM users WHERE id=?",(request.session["user_id"],)).fetchone()
    if not pw_verify(current_password,u["password_hash"]): conn.close(); return RedirectResponse("/settings?pw=bad",303)
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",(pw_hash(new_password),u["id"])); conn.commit(); conn.close(); return RedirectResponse("/settings?pw=ok",303)

@app.post("/settings/backup")
def backup(request:Request):
    require_login(request); out=DATA/f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"; shutil.copy2(DB,out); return FileResponse(out,filename=out.name)

@app.get("/export/jobs.csv")
def export_jobs(request:Request):
    require_login(request); conn=db(); rows=conn.execute("""SELECT j.job_number,j.status,j.scheduled_date,c.name,p.address,j.issue_type,j.urgency,j.created_at FROM jobs j JOIN customers c ON c.id=j.customer_id JOIN properties p ON p.id=j.property_id ORDER BY j.id""").fetchall(); conn.close()
    sio=io.StringIO(); w=csv.writer(sio); w.writerow(["Job #","Status","Date","Customer","Property","Issue","Urgency","Created"]); [w.writerow(tuple(r)) for r in rows]
    return StreamingResponse(iter([sio.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=jobs.csv"})

@app.get("/export/invoices.csv")
def export_invoices(request:Request):
    require_login(request); conn=db(); rows=conn.execute("""SELECT i.invoice_number,j.job_number,c.name,i.status,i.subtotal,i.tax,i.total,i.amount_paid,(i.total-i.amount_paid) balance,i.created_at FROM invoices i JOIN jobs j ON j.id=i.job_id JOIN customers c ON c.id=j.customer_id ORDER BY i.id""").fetchall(); conn.close()
    sio=io.StringIO(); w=csv.writer(sio); w.writerow(["Invoice #","Job #","Customer","Status","Subtotal","Tax","Total","Paid","Balance","Created"]); [w.writerow(tuple(r)) for r in rows]
    return StreamingResponse(iter([sio.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=invoices.csv"})

def safe_text(x): return str(x or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")

def build_report_pdf(jid:int, invoice=False):
    conn=db(); job,a,s,photos,inv=get_job_bundle(conn,jid)
    items=conn.execute("SELECT * FROM invoice_items WHERE invoice_id=?",(inv["id"],)).fetchall() if inv else []
    biz={k:setting(conn,k) for k in ["business_name","phone","email","website","license","service_area"]}; conn.close()
    out=REPORTS/(f"invoice_{inv['invoice_number']}.pdf" if invoice and inv else f"report_{job['job_number']}.pdf")
    styles=getSampleStyleSheet();
    styles.add(ParagraphStyle(name="Brand",parent=styles["Title"],textColor=colors.HexColor("#0668d8"),fontSize=20,leading=22,spaceAfter=8))
    styles.add(ParagraphStyle(name="H2b",parent=styles["Heading2"],textColor=colors.HexColor("#05365f"),fontSize=12,spaceBefore=8,spaceAfter=5))
    styles.add(ParagraphStyle(name="Small",parent=styles["BodyText"],fontSize=8,leading=10))
    doc=SimpleDocTemplate(str(out),pagesize=letter,rightMargin=.55*inch,leftMargin=.55*inch,topMargin=.45*inch,bottomMargin=.45*inch)
    flow=[]; logo=BASE/"static/logo.png"
    if logo.exists(): flow.append(RLImage(str(logo),width=2.35*inch,height=1.05*inch))
    flow += [Paragraph(biz["business_name"],styles["Brand"]),Paragraph(f"{biz['phone']} | {biz['email']} | {biz['website']} | {biz['license']}",styles["Small"]),Spacer(1,8)]
    if invoice and inv:
        flow += [Paragraph("INVOICE",styles["Title"]),Paragraph(f"Invoice: <b>{inv['invoice_number']}</b> &nbsp;&nbsp; Job: <b>{job['job_number']}</b>",styles["BodyText"])]
        meta=[["Bill To",job["customer_name"]],["Property",f"{job['address']}, {job['city']} {job['state']} {job['zip']}"],["Status",inv["status"]]]
        t=Table(meta,colWidths=[1.2*inch,5.5*inch]); t.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#e8f3ff')),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP'),('PADDING',(0,0),(-1,-1),6)])); flow += [Spacer(1,8),t,Spacer(1,10)]
        data=[["Description","Qty","Unit","Total"]]+[[Paragraph(safe_text(x['description']),styles['Small']),f"{x['quantity']:g}",f"${x['unit_price']:,.2f}",f"${x['line_total']:,.2f}"] for x in items]
        tt=Table(data,colWidths=[4.25*inch,.65*inch,.9*inch,.9*inch],repeatRows=1); tt.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#05365f')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('ALIGN',(1,1),(-1,-1),'RIGHT'),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('PADDING',(0,0),(-1,-1),6)])); flow.append(tt)
        totals=[["Subtotal",f"${inv['subtotal']:,.2f}"],["Tax",f"${inv['tax']:,.2f}"],["Total",f"${inv['total']:,.2f}"],["Paid",f"${inv['amount_paid']:,.2f}"],["Balance",f"${inv['total']-inv['amount_paid']:,.2f}"]]
        st=Table(totals,colWidths=[1.3*inch,1.2*inch],hAlign='RIGHT'); st.setStyle(TableStyle([('ALIGN',(0,0),(-1,-1),'RIGHT'),('FONTNAME',(0,-1),(-1,-1),'Helvetica-Bold'),('LINEABOVE',(0,-1),(-1,-1),1,colors.HexColor('#0668d8')),('PADDING',(0,0),(-1,-1),4)])); flow += [Spacer(1,8),st,Spacer(1,12),Paragraph(safe_text(inv['notes']),styles['Small'])]
    else:
        flow += [Paragraph("Independent Sewer & Water Line Assessment",styles["Title"]),Spacer(1,5)]
        meta=[["Job",job["job_number"]],["Customer",job["customer_name"]],["Property",f"{job['address']}, {job['city']} {job['state']} {job['zip']}"],["Assessment Date",job["scheduled_date"] or job["created_at"][:10]],["Issue Type",job["issue_type"] or ""]]
        t=Table(meta,colWidths=[1.25*inch,5.45*inch]); t.setStyle(TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#e8f3ff')),('FONTNAME',(0,0),(0,-1),'Helvetica-Bold'),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP'),('PADDING',(0,0),(-1,-1),5)])); flow.append(t)
        def sec(title,text):
            flow.append(Paragraph(title,styles['H2b'])); flow.append(Paragraph(safe_text(text) or '—',styles['BodyText']))
        sec("Reason for Assessment",job["customer_concern"]); sec("Prior Recommendation / Estimate",job["prior_recommendation"])
        if s:
            sec("Assessment Performed",", ".join(json.loads(s["performed_json"] or "[]"))); sec("Observed Conditions",s["observed_conditions"]); sec("Camera / Line Findings",s["line_findings"])
            detail=(f"Problem location: {s['problem_location'] or 'Not specified'}\n"
                    f"Pipe / line material: {s['pipe_material'] or 'Not specified'}\n"
                    f"Condition rating: {s['condition_rating'] or 'Not specified'}\n"
                    f"Limitations: {s['limitations'] or 'None noted'}")
            sec("Technical Details",detail); sec("Assessment / Recommended Next Step",s["recommendation"])
        sec("Independent Professional Opinion",job["professional_opinion"])
        flow.append(Spacer(1,8)); flow.append(Paragraph("Independence Statement",styles['H2b'])); flow.append(Paragraph("The Master Plumber does not perform sewer or water line repair or excavation work, does not recommend a specific repair company, and does not receive referral compensation. Our fee is for the assessment and advice only and does not change based on whether a repair is performed.",styles['BodyText']))
        if photos:
            flow.append(Paragraph("Photo Documentation",styles['H2b']))
            row=[]
            for p in photos[:6]:
                fp=UPLOADS/p['file_path']
                if fp.exists():
                    try: row.append(RLImage(str(fp),width=2.0*inch,height=1.5*inch))
                    except: pass
                if len(row)==3: flow.append(Table([row],colWidths=[2.15*inch]*3)); row=[]
            if row: flow.append(Table([row],colWidths=[2.15*inch]*len(row)))
        if a and a['signature_path'] and (UPLOADS/a['signature_path']).exists():
            flow += [Spacer(1,10),Paragraph("Customer Acknowledgment",styles['H2b']),Paragraph(f"Signed by: {safe_text(a['signature_name'])} on {safe_text(a['signed_at'])}",styles['Small']),RLImage(str(UPLOADS/a['signature_path']),width=2.3*inch,height=.7*inch)]
    doc.build(flow); return out

def build_agreement_pdf(jid:int):
    conn=db(); job,a,sx,photos,inv=get_job_bundle(conn,jid); services=conn.execute("SELECT * FROM job_services WHERE job_id=? ORDER BY id",(jid,)).fetchall(); biz={k:setting(conn,k) for k in ["business_name","phone","email","website","license"]}; conn.close()
    out=REPORTS/f"agreement_{job['job_number']}.pdf"
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Brand2",parent=styles["Title"],textColor=colors.HexColor("#0668d8"),fontSize=19,leading=21,spaceAfter=4))
    styles.add(ParagraphStyle(name="Small2",parent=styles["BodyText"],fontSize=8.5,leading=11,spaceAfter=3))
    styles.add(ParagraphStyle(name="TermHead",parent=styles["BodyText"],fontName="Helvetica-Bold",fontSize=8.6,leading=11,textColor=colors.HexColor("#092742"),spaceAfter=2))
    doc=SimpleDocTemplate(str(out),pagesize=letter,rightMargin=.55*inch,leftMargin=.55*inch,topMargin=.4*inch,bottomMargin=.45*inch)
    flow=[]; logo=BASE/"static/logo.png"
    if logo.exists(): flow.append(RLImage(str(logo),width=2.35*inch,height=1.04*inch))
    flow += [Paragraph("Agreement for Services",styles["Brand2"]),
             Paragraph(f"<b>Job:</b> {safe_text(job['job_number'])} &nbsp;&nbsp; <b>Type:</b> {safe_text(job['job_type'])}",styles["Small2"]),
             Paragraph(f"<b>Customer:</b> {safe_text(job['customer_name'])}",styles["Small2"]),
             Paragraph(f"<b>Service property:</b> {safe_text(job['address'])}, {safe_text(job['city'])} {safe_text(job['state'])} {safe_text(job['zip'])}",styles["Small2"]),
             Spacer(1,8)]
    data=[["Service / Included Scope","Qty","Price","Total"]]
    for x in services:
        code=(x['code'] or '').strip()
        label=(f"<b>{safe_text(code)}</b> - " if code else "")+safe_text(x['description'])
        scope=safe_text(x['scope_text'] or '')
        data.append([Paragraph(label+("<br/><font size=7>"+scope+"</font>" if scope else ""),styles['Small2']),f"{x['quantity']:g}",f"${x['unit_price']:,.2f}",f"${x['line_total']:,.2f}"])
    total=sum(float(x['line_total']) for x in services)
    data.append(["","","Approved Total",f"${total:,.2f}"])
    t=Table(data,colWidths=[4.2*inch,.55*inch,.9*inch,1.0*inch],repeatRows=1)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#05365f')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('GRID',(0,0),(-1,-1),.3,colors.lightgrey),('VALIGN',(0,0),(-1,-1),'TOP'),('ALIGN',(1,1),(-1,-1),'RIGHT'),('PADDING',(0,0),(-1,-1),5),('FONTNAME',(2,-1),(-1,-1),'Helvetica-Bold')]))
    flow += [t,Spacer(1,10),Paragraph("Terms & Conditions",styles['Heading2'])]
    terms=(a['terms_text'] if a else (ASSESSMENT_TERMS if job['job_type']=='Independent Assessment' else PLUMBING_TERMS))
    for para in terms.split("\n\n"):
        para=safe_text(para)
        # Bold the numbered heading before the first period, if present.
        if ". " in para:
            head,body=para.split(". ",1)
            if head[:1].isdigit():
                flow.append(KeepTogether([Paragraph(f"<b>{head}.</b> {body}",styles['Small2']),Spacer(1,4)]))
                continue
        flow.append(KeepTogether([Paragraph(para,styles['Small2']),Spacer(1,4)]))
    flow += [Spacer(1,4),Paragraph("Customer Acknowledgments",styles['Heading2'])]
    checks=[]
    if a:
        checks.append((bool(a['agree_scope']),"Authorized scope approved"))
        checks.append((bool(a['agree_price']),"Approved flat-rate price and change-order process"))
        checks.append((bool(a['agree_terms']),"Terms & Conditions reviewed and accepted"))
        if job['job_type']=='Independent Assessment': checks.append((bool(a['agree_no_referral']),"Independent assessment / no repair or referral compensation acknowledged"))
        checks.append((bool(a['agree_limits']),"Concealed conditions and out-of-scope changes acknowledged"))
        if job['job_type']!='Independent Assessment': checks.append((bool(a['agree_warranty']),"One-year labor/workmanship warranty and separate manufacturer product warranty acknowledged"))
        checks.append((bool(a['agree_esign']),"Electronic signature and electronic copy consent"))
    if checks:
        for ok,label in checks:
            flow.append(Paragraph(("[X]" if ok else "[ ]")+" "+safe_text(label),styles['Small2']))
    flow += [Spacer(1,6),Paragraph("<b>Customer Rights:</b> This agreement does not waive any nonwaivable right provided by law. If a legally required cancellation or rescission right applies, the customer retains that right. A separate legally required emergency request or waiver, if applicable, must be documented separately.",styles['Small2'])]
    if a and a['signature_name']:
        flow += [Spacer(1,8),Paragraph(f"Customer authorization: <b>{safe_text(a['signature_name'])}</b>",styles['Small2']),Paragraph(f"Signed: {safe_text(a['signed_at'])}",styles['Small2'])]
        if a['signature_path'] and (UPLOADS/a['signature_path']).exists(): flow.append(RLImage(str(UPLOADS/a['signature_path']),width=2.4*inch,height=.75*inch))
    flow += [Spacer(1,10),Paragraph(f"{safe_text(biz['business_name'])} | {safe_text(biz['phone'])} | {safe_text(biz['email'])} | {safe_text(biz['website'])} | {safe_text(biz['license'])}",styles['Small2'])]
    doc.build(flow); return out

@app.get("/jobs/{jid}/agreement.pdf")
def agreement_pdf(request:Request,jid:int): require_login(request); out=build_agreement_pdf(jid); return FileResponse(out,filename=out.name,media_type="application/pdf")

def send_email_with_attachment(to_email:str, subject:str, body:str, attachment:Path):
    host=os.getenv("TMP_SMTP_HOST")
    if not host: raise RuntimeError("Email is not configured. Add TMP_SMTP_HOST and the SMTP settings in Railway.")
    port=int(os.getenv("TMP_SMTP_PORT","587")); user=os.getenv("TMP_SMTP_USER",""); pw=os.getenv("TMP_SMTP_PASSWORD",""); sender=os.getenv("TMP_SMTP_FROM") or user
    if not sender:
        c=db(); sender=setting(c,"email"); c.close()
    msg=EmailMessage(); msg["From"]=sender; msg["To"]=to_email; msg["Subject"]=subject; msg.set_content(body)
    msg.add_attachment(attachment.read_bytes(),maintype="application",subtype="pdf",filename=attachment.name)
    with smtplib.SMTP(host,port,timeout=20) as smtp:
        smtp.starttls()
        if user: smtp.login(user,pw)
        smtp.send_message(msg)

@app.post("/jobs/{jid}/agreement/email")
def agreement_email(request:Request,jid:int):
    require_login(request); conn=db(); job,a,sx,photos,inv=get_job_bundle(conn,jid)
    customer_email=job["email"] if "email" in job.keys() else None
    if not customer_email:
        # get_job_bundle joins customer fields; keep a fallback for older data shape
        row=conn.execute("SELECT c.email FROM jobs j JOIN customers c ON c.id=j.customer_id WHERE j.id=?",(jid,)).fetchone(); customer_email=row["email"] if row else None
    conn.close()
    if not a or not a["signature_name"]: return RedirectResponse(f"/jobs/{jid}?email_error=Agreement%20must%20be%20signed%20first.",303)
    if not customer_email: return RedirectResponse(f"/jobs/{jid}?email_error=Customer%20has%20no%20email%20address%20on%20file.",303)
    try:
        pdf=build_agreement_pdf(jid)
        send_email_with_attachment(customer_email, f"The Master Plumber - Signed Agreement {job['job_number']}", f"Hello {job['customer_name']},\n\nAttached is your signed Agreement for Services from The Master Plumber.\n\nIf you have any questions, call 720-307-5753.\n\nThank you,\nThe Master Plumber", pdf)
        return RedirectResponse(f"/jobs/{jid}?email_ok=Signed%20agreement%20emailed%20to%20customer.",303)
    except Exception as e:
        from urllib.parse import quote
        return RedirectResponse(f"/jobs/{jid}?email_error={quote(str(e))}",303)

@app.get("/jobs/{jid}/report.pdf")
def report_pdf(request:Request,jid:int): require_login(request); out=build_report_pdf(jid,False); return FileResponse(out,filename=out.name,media_type="application/pdf")
@app.get("/jobs/{jid}/invoice.pdf")
def invoice_pdf(request:Request,jid:int): require_login(request); out=build_report_pdf(jid,True); return FileResponse(out,filename=out.name,media_type="application/pdf")
