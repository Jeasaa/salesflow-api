from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import libsql_client
import hashlib
import jwt
import os
from datetime import datetime, timedelta

# ━━━ CONFIG ━━━
TURSO_URL = "https://app-jeasaa.aws-eu-west-1.turso.io"
TURSO_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3NzMwNjM0ODksImlkIjoiMDE5Y2QyY2UtZmUwMS03YjYyLTk4MjEtN2M1NjcxNjBiMDBmIiwicmlkIjoiMmVlNTYwNTYtMmJkZC00NmMzLTllZmEtODY3Yjc1MGI5ZmMyIn0.W5_YArM9r3v7hd8fy65ZRf5ya2xrPhmmmref4NdCUmhR8j6XytZ2_g73yALIJC8C5h9n-1BzBkix9X3FQ_yOAA"
JWT_SECRET = "salesflow-secret-key-change-me-2025"
TECHNIQUES = ["FTID", "LIT", "RTS", "DNA", "EB"]

app = FastAPI(title="SalesFlow API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ━━━ DB ━━━
_client = libsql_client.create_client_sync(url=TURSO_URL, auth_token=TURSO_TOKEN)

def run(sql, params=None):
    return _client.execute(sql, params or [])

def query(sql, params=None):
    rs = _client.execute(sql, params or [])
    if not rs.columns: return []
    return [dict(zip(rs.columns, row)) for row in rs.rows]

def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None

def query_val(sql, params=None):
    rs = _client.execute(sql, params or [])
    return rs.rows[0][0] if rs.rows else None

# ━━━ INIT ━━━
def init_db():
    tables = [
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT,username TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT DEFAULT 'admin',created_at TEXT)",
        "CREATE TABLE IF NOT EXISTS acheteurs (id INTEGER PRIMARY KEY AUTOINCREMENT,nom TEXT NOT NULL UNIQUE,parrain TEXT DEFAULT 'Aucun',commission_parrain REAL DEFAULT 0.0,identifiant_boutique TEXT DEFAULT '',mdp_boutique TEXT DEFAULT '',date_creation TEXT)",
        "CREATE TABLE IF NOT EXISTS commandes (id INTEGER PRIMARY KEY AUTOINCREMENT,date TEXT,acheteur_id INTEGER NOT NULL,boutique TEXT NOT NULL,montant_total REAL NOT NULL,commission_mode TEXT DEFAULT 'pct',commission_vendeur_pct REAL DEFAULT 0.0,commission_vendeur_eur REAL NOT NULL,commission_parrain_eur REAL DEFAULT 0.0,notes TEXT DEFAULT '',statut TEXT DEFAULT 'En cours',technique TEXT DEFAULT '',cout_total REAL DEFAULT 0.0)",
        "CREATE TABLE IF NOT EXISTS notes_acheteurs (id INTEGER PRIMARY KEY AUTOINCREMENT,acheteur_id INTEGER NOT NULL,contenu TEXT NOT NULL,date_creation TEXT,auteur TEXT DEFAULT 'admin')",
        "CREATE TABLE IF NOT EXISTS objectifs (id INTEGER PRIMARY KEY AUTOINCREMENT,mois TEXT NOT NULL UNIQUE,objectif_ca REAL DEFAULT 0.0,objectif_commandes INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS demandes (id INTEGER PRIMARY KEY AUTOINCREMENT,date_soumission TEXT,nom_client TEXT NOT NULL,boutique TEXT NOT NULL,montant REAL NOT NULL,identifiant_boutique TEXT DEFAULT '',mdp_boutique TEXT DEFAULT '',notes_client TEXT DEFAULT '',statut TEXT DEFAULT 'En attente')",
        "CREATE TABLE IF NOT EXISTS couts_techniques (id INTEGER PRIMARY KEY AUTOINCREMENT,nom TEXT NOT NULL UNIQUE,prix REAL NOT NULL DEFAULT 0.0)",
    ]
    for t in tables:
        try: run(t)
        except: pass
    if query_val("SELECT COUNT(*) FROM users") == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run("INSERT INTO users (username,password_hash,role,created_at) VALUES (?,?,?,?)",
            ["admin", hashlib.sha256("admin".encode()).hexdigest(), "admin", now])

init_db()

# ━━━ AUTH ━━━
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def create_token(username):
    return jwt.encode({"sub": username, "exp": datetime.utcnow() + timedelta(days=30)}, JWT_SECRET, algorithm="HS256")

def get_current_user(token: str = Depends(lambda: None)):
    # Simplified - in production use proper OAuth2
    pass

class LoginReq(BaseModel):
    username: str
    password: str

class ChangePwReq(BaseModel):
    old_password: str
    new_password: str
    username: str

@app.post("/api/login")
def login(req: LoginReq):
    user = query_one("SELECT * FROM users WHERE username=? AND password_hash=?", [req.username, hash_pw(req.password)])
    if not user: raise HTTPException(401, "Identifiants incorrects")
    return {"token": create_token(req.username), "username": req.username}

@app.post("/api/change-password")
def change_password(req: ChangePwReq):
    user = query_one("SELECT * FROM users WHERE username=? AND password_hash=?", [req.username, hash_pw(req.old_password)])
    if not user: raise HTTPException(401, "Mot de passe actuel incorrect")
    run("UPDATE users SET password_hash=? WHERE username=?", [hash_pw(req.new_password), req.username])
    return {"ok": True}

# ━━━ DASHBOARD ━━━
@app.get("/api/dashboard")
def dashboard():
    # Une seule requête pour tous les KPIs
    stats = query_one("""SELECT 
        (SELECT COUNT(*) FROM acheteurs) as acheteurs,
        (SELECT COUNT(*) FROM commandes) as commandes,
        (SELECT COALESCE(SUM(montant_total),0) FROM commandes) as ca,
        (SELECT COALESCE(SUM(cout_total),0) FROM commandes) as couts,
        (SELECT COALESCE(SUM(commission_vendeur_eur),0) FROM commandes WHERE statut IN ('En cours','Livrée')) as comm_en_cours,
        (SELECT COALESCE(SUM(cout_total),0) FROM commandes WHERE statut IN ('En cours','Livrée')) as couts_en_cours,
        (SELECT COALESCE(SUM(commission_vendeur_eur),0) FROM commandes WHERE statut='Validée') as recu,
        (SELECT COUNT(*) FROM demandes WHERE statut='En attente') as pending
    """)
    recent = query("SELECT c.id,c.date,a.nom as acheteur,c.boutique,c.montant_total,c.commission_vendeur_eur,c.technique,c.cout_total,c.statut FROM commandes c JOIN acheteurs a ON c.acheteur_id=a.id ORDER BY c.date DESC LIMIT 10")
    return {
        "acheteurs": stats['acheteurs'], "commandes": stats['commandes'], 
        "ca": float(stats['ca']), "couts": float(stats['couts']),
        "net_a_venir": float(stats['comm_en_cours']) - float(stats['couts_en_cours']), 
        "recu": float(stats['recu']), "pending": int(stats['pending']), "recent": recent
    }

# ━━━ COÛTS TECHNIQUES ━━━
@app.get("/api/couts")
def get_couts():
    return query("SELECT * FROM couts_techniques ORDER BY nom")

class CoutReq(BaseModel):
    nom: str
    prix: float

@app.post("/api/couts")
def set_cout(req: CoutReq):
    existing = query_val("SELECT id FROM couts_techniques WHERE nom=?", [req.nom])
    if existing: run("UPDATE couts_techniques SET prix=? WHERE nom=?", [req.prix, req.nom])
    else: run("INSERT INTO couts_techniques (nom,prix) VALUES (?,?)", [req.nom, req.prix])
    return {"ok": True}

@app.delete("/api/couts/{nom}")
def del_cout(nom: str):
    run("DELETE FROM couts_techniques WHERE nom=?", [nom])
    return {"ok": True}

def get_prix(nom):
    v = query_val("SELECT prix FROM couts_techniques WHERE nom=?", [nom])
    return float(v) if v else 0.0

def calc_cout(tech):
    if not tech: return 0.0
    return sum(get_prix(t.strip()) for t in tech.split(",") if t.strip())

# ━━━ ACHETEURS ━━━
@app.get("/api/acheteurs")
def get_acheteurs():
    return query("SELECT * FROM acheteurs ORDER BY nom")

@app.get("/api/acheteurs/{aid}")
def get_acheteur(aid: int):
    ach = query_one("SELECT * FROM acheteurs WHERE id=?", [aid])
    if not ach: raise HTTPException(404)
    cmds = query("SELECT c.id,c.date,c.boutique,c.montant_total,c.commission_vendeur_eur,c.technique,c.cout_total,c.statut,c.notes FROM commandes c WHERE c.acheteur_id=? ORDER BY c.date DESC", [aid])
    notes = query("SELECT * FROM notes_acheteurs WHERE acheteur_id=? ORDER BY date_creation DESC", [aid])
    return {"acheteur": ach, "commandes": cmds, "notes": notes}

class AcheteurReq(BaseModel):
    nom: str
    parrain: str = "Aucun"
    commission_parrain: float = 0.0
    identifiant_boutique: str = ""
    mdp_boutique: str = ""

@app.post("/api/acheteurs")
def add_acheteur(req: AcheteurReq):
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        run("INSERT INTO acheteurs (nom,parrain,commission_parrain,identifiant_boutique,mdp_boutique,date_creation) VALUES (?,?,?,?,?,?)",
            [req.nom.strip(), req.parrain.strip() or "Aucun", req.commission_parrain, req.identifiant_boutique.strip(), req.mdp_boutique.strip(), now])
        return {"ok": True}
    except: raise HTTPException(400, "Ce nom existe déjà")

@app.put("/api/acheteurs/{aid}")
def mod_acheteur(aid: int, req: AcheteurReq):
    try:
        run("UPDATE acheteurs SET nom=?,parrain=?,commission_parrain=?,identifiant_boutique=?,mdp_boutique=? WHERE id=?",
            [req.nom.strip(), req.parrain.strip() or "Aucun", req.commission_parrain, req.identifiant_boutique.strip(), req.mdp_boutique.strip(), aid])
        return {"ok": True}
    except: raise HTTPException(400, "Ce nom existe déjà")

@app.delete("/api/acheteurs/{aid}")
def del_acheteur(aid: int):
    run("DELETE FROM notes_acheteurs WHERE acheteur_id=?", [aid])
    run("DELETE FROM commandes WHERE acheteur_id=?", [aid])
    run("DELETE FROM acheteurs WHERE id=?", [aid])
    return {"ok": True}

# ━━━ NOTES ━━━
class NoteReq(BaseModel):
    contenu: str
    auteur: str = "admin"

@app.post("/api/acheteurs/{aid}/notes")
def add_note(aid: int, req: NoteReq):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run("INSERT INTO notes_acheteurs (acheteur_id,contenu,date_creation,auteur) VALUES (?,?,?,?)", [aid, req.contenu.strip(), now, req.auteur])
    return {"ok": True}

@app.delete("/api/notes/{nid}")
def del_note(nid: int):
    run("DELETE FROM notes_acheteurs WHERE id=?", [nid])
    return {"ok": True}

# ━━━ COMMANDES ━━━
@app.get("/api/commandes")
def get_commandes():
    return query("""SELECT c.id,c.date,a.nom as acheteur,c.boutique,c.montant_total,
        c.commission_mode,c.commission_vendeur_pct,c.commission_vendeur_eur,
        a.parrain,c.commission_parrain_eur,c.notes,c.statut,
        c.technique,c.cout_total,c.acheteur_id
        FROM commandes c JOIN acheteurs a ON c.acheteur_id=a.id ORDER BY c.date DESC""")

class CommandeReq(BaseModel):
    acheteur_nom: str
    boutique: str
    montant_total: float
    commission_mode: str = "pct"
    commission_pct: float = 0.0
    commission_eur: float = 0.0
    notes: str = ""
    statut: str = "En cours"
    technique: str = ""
    identifiant: str = ""
    mdp: str = ""

def get_or_create_ach(nom, ident="", mdp=""):
    row = query_val("SELECT id FROM acheteurs WHERE nom=?", [nom.strip()])
    if row:
        aid = int(row)
        if ident.strip():
            run("UPDATE acheteurs SET identifiant_boutique=? WHERE id=? AND (identifiant_boutique IS NULL OR identifiant_boutique='')", [ident.strip(), aid])
        if mdp.strip():
            run("UPDATE acheteurs SET mdp_boutique=? WHERE id=? AND (mdp_boutique IS NULL OR mdp_boutique='')", [mdp.strip(), aid])
        return aid
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rs = run("INSERT INTO acheteurs (nom,identifiant_boutique,mdp_boutique,date_creation) VALUES (?,?,?,?)", [nom.strip(), ident.strip(), mdp.strip(), now])
    return rs.last_insert_rowid

def calc_comm(mont, mode, pct, eur, parrain, cp):
    if mode == "pct": cv = mont * (pct / 100); pf = pct
    else: cv = eur; pf = (eur / mont * 100) if mont > 0 else 0
    cpar = cv * (cp / 100) if parrain != "Aucun" and cp > 0 else 0
    return cv, cpar, pf

@app.post("/api/commandes")
def add_commande(req: CommandeReq):
    aid = get_or_create_ach(req.acheteur_nom, req.identifiant, req.mdp)
    ach = query_one("SELECT * FROM acheteurs WHERE id=?", [aid])
    cv, cp, pf = calc_comm(req.montant_total, req.commission_mode, req.commission_pct, req.commission_eur, ach['parrain'], ach['commission_parrain'])
    cout = calc_cout(req.technique)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run("""INSERT INTO commandes (date,acheteur_id,boutique,montant_total,commission_mode,commission_vendeur_pct,commission_vendeur_eur,commission_parrain_eur,notes,statut,technique,cout_total) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [now, aid, req.boutique.strip(), req.montant_total, req.commission_mode, pf, cv, cp, req.notes.strip(), req.statut, req.technique, cout])
    return {"ok": True, "commission": cv, "cout": cout}

class CommandeEditReq(BaseModel):
    boutique: str
    montant_total: float
    commission_mode: str = "pct"
    commission_pct: float = 0.0
    commission_eur: float = 0.0
    notes: str = ""
    statut: str = "En cours"
    technique: str = ""

@app.put("/api/commandes/{cid}")
def mod_commande(cid: int, req: CommandeEditReq):
    aid = query_val("SELECT acheteur_id FROM commandes WHERE id=?", [cid])
    if not aid: raise HTTPException(404)
    ach = query_one("SELECT * FROM acheteurs WHERE id=?", [int(aid)])
    cv, cp, pf = calc_comm(req.montant_total, req.commission_mode, req.commission_pct, req.commission_eur, ach['parrain'], ach['commission_parrain'])
    cout = calc_cout(req.technique)
    run("UPDATE commandes SET boutique=?,montant_total=?,commission_mode=?,commission_vendeur_pct=?,commission_vendeur_eur=?,commission_parrain_eur=?,notes=?,statut=?,technique=?,cout_total=? WHERE id=?",
        [req.boutique.strip(), req.montant_total, req.commission_mode, pf, cv, cp, req.notes.strip(), req.statut, req.technique, cout, cid])
    return {"ok": True, "commission": cv, "cout": cout}

@app.delete("/api/commandes/{cid}")
def del_commande(cid: int):
    run("DELETE FROM commandes WHERE id=?", [cid])
    return {"ok": True}

# ━━━ DEMANDES (public) ━━━
class DemandeReq(BaseModel):
    nom_client: str
    boutique: str
    montant: float
    identifiant_boutique: str = ""
    mdp_boutique: str = ""
    notes_client: str = ""

@app.post("/api/demandes/submit")
def submit_demande(req: DemandeReq):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run("INSERT INTO demandes (date_soumission,nom_client,boutique,montant,identifiant_boutique,mdp_boutique,notes_client,statut) VALUES (?,?,?,?,?,?,?,'En attente')",
        [now, req.nom_client.strip(), req.boutique.strip(), req.montant, req.identifiant_boutique.strip(), req.mdp_boutique.strip(), req.notes_client.strip()])
    return {"ok": True}

@app.get("/api/demandes")
def get_demandes(statut: Optional[str] = None):
    if statut: return query("SELECT * FROM demandes WHERE statut=? ORDER BY date_soumission DESC", [statut])
    return query("SELECT * FROM demandes ORDER BY date_soumission DESC")

class ValiderReq(BaseModel):
    commission_mode: str = "pct"
    commission_pct: float = 0.0
    commission_eur: float = 0.0
    technique: str = ""
    notes_admin: str = ""

@app.post("/api/demandes/{did}/valider")
def valider_demande(did: int, req: ValiderReq):
    dem = query_one("SELECT * FROM demandes WHERE id=?", [did])
    if not dem: raise HTTPException(404)
    notes = f"{dem['notes_client']} | {req.notes_admin}".strip(" | ")
    aid = get_or_create_ach(dem['nom_client'], dem['identifiant_boutique'], dem['mdp_boutique'])
    ach = query_one("SELECT * FROM acheteurs WHERE id=?", [aid])
    cv, cp, pf = calc_comm(dem['montant'], req.commission_mode, req.commission_pct, req.commission_eur, ach['parrain'], ach['commission_parrain'])
    cout = calc_cout(req.technique)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run("""INSERT INTO commandes (date,acheteur_id,boutique,montant_total,commission_mode,commission_vendeur_pct,commission_vendeur_eur,commission_parrain_eur,notes,statut,technique,cout_total) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        [now, aid, dem['boutique'], dem['montant'], req.commission_mode, pf, cv, cp, notes, "En cours", req.technique, cout])
    run("UPDATE demandes SET statut='Validée' WHERE id=?", [did])
    return {"ok": True, "commission": cv, "cout": cout}

@app.post("/api/demandes/{did}/rejeter")
def rejeter_demande(did: int):
    run("UPDATE demandes SET statut='Rejetée' WHERE id=?", [did])
    return {"ok": True}

# ━━━ OBJECTIFS ━━━
@app.get("/api/objectifs")
def get_objectifs():
    return query("SELECT * FROM objectifs ORDER BY mois DESC")

class ObjectifReq(BaseModel):
    mois: str
    objectif_ca: float = 0.0
    objectif_commandes: int = 0

@app.post("/api/objectifs")
def set_objectif(req: ObjectifReq):
    existing = query_val("SELECT id FROM objectifs WHERE mois=?", [req.mois])
    if existing: run("UPDATE objectifs SET objectif_ca=?,objectif_commandes=? WHERE mois=?", [req.objectif_ca, req.objectif_commandes, req.mois])
    else: run("INSERT INTO objectifs (mois,objectif_ca,objectif_commandes) VALUES (?,?,?)", [req.mois, req.objectif_ca, req.objectif_commandes])
    return {"ok": True}

# ━━━ COMMISSIONS ━━━
@app.get("/api/commissions")
def get_commissions():
    parrains = query("""SELECT a.parrain as parrain, COUNT(DISTINCT a.nom) as filleuls, COUNT(c.id) as commandes,
        COALESCE(SUM(c.montant_total),0) as ca, COALESCE(SUM(c.commission_parrain_eur),0) as commissions
        FROM commandes c JOIN acheteurs a ON c.acheteur_id=a.id WHERE a.parrain != 'Aucun'
        GROUP BY a.parrain ORDER BY commissions DESC""")
    boutiques = query("""SELECT c.boutique, COUNT(c.id) as commandes, COALESCE(SUM(c.montant_total),0) as ca,
        COALESCE(SUM(c.commission_vendeur_eur),0) as commission, COALESCE(SUM(c.cout_total),0) as couts
        FROM commandes c GROUP BY c.boutique ORDER BY commission DESC""")
    return {"parrains": parrains, "boutiques": boutiques}

# ━━━ SEARCH ━━━
@app.get("/api/search")
def search(q: str):
    t = f"%{q}%"
    ach = query("SELECT 'acheteur' as type, id, nom as titre, '' as detail FROM acheteurs WHERE nom LIKE ?", [t])
    cmd = query("SELECT 'commande' as type, c.id, c.boutique as titre, a.nom as detail FROM commandes c JOIN acheteurs a ON c.acheteur_id=a.id WHERE c.boutique LIKE ? OR a.nom LIKE ?", [t, t])
    return ach + cmd

@app.get("/api/techniques")
def get_techniques():
    return TECHNIQUES
