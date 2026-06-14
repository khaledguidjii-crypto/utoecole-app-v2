import os
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from supabase import create_client

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Variables d'environnement
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
    raise Exception("Variables SUPABASE_URL et SUPABASE_PUBLISHABLE_KEY manquantes")

supabase = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)

# ---------- Fonctions utilitaires ----------
def get_current_user():
    """Retourne l'utilisateur connecté depuis la session"""
    return session.get("user")

def is_admin():
    user = get_current_user()
    return user and user.get("email") == "khaled@autoecole.com"

def add_notification(message, type_notif, lien=None):
    supabase.table("notifications").insert({
        "message": message,
        "type": type_notif,
        "lien": lien,
        "lu": False
    }).execute()

def update_solde(montant, description, type_transaction, candidat_id=None, employe="systeme"):
    caisse_rows = supabase.table("caisse").select("id, solde").execute().data
    if not caisse_rows:
        new_row = supabase.table("caisse").insert({"solde": 0}).execute().data
        caisse_id = new_row[0]["id"]
        solde_actuel = 0
    else:
        caisse_id = caisse_rows[0]["id"]
        solde_actuel = caisse_rows[0]["solde"]
    
    nouveau_solde = solde_actuel + montant
    
    supabase.table("caisse").update({
        "solde": nouveau_solde,
        "updated_at": datetime.now().isoformat()
    }).eq("id", caisse_id).execute()
    
    supabase.table("transactions").insert({
        "description": description,
        "montant": montant,
        "type": type_transaction,
        "candidat_id": candidat_id,
        "created_by": employe
    }).execute()
    
    if montant < 0:
        add_notification(
            message=f"{employe} a retiré {abs(montant)} DA : {description}",
            type_notif="caisse",
            lien="/caisse"
        )
    return nouveau_solde

# ---------- Authentification ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        try:
            resp = supabase.auth.sign_in_with_password({"email": email, "password": password})
            session["user"] = {
                "id": resp.user.id,
                "email": resp.user.email,
                "role": "admin" if resp.user.email == "khaled@autoecole.com" else "staff"
            }
            flash(f"Bienvenue {email} !")
            return redirect(url_for("index"))
        except Exception as e:
            flash("Identifiants incorrects")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    supabase.auth.sign_out()
    flash("Déconnecté")
    return redirect(url_for("login"))

# ---------- Routes principales ----------
def get_all_candidats():
    return supabase.table("candidats").select("*").order("created_at", desc=True).execute().data

def get_candidat_by_id(candidat_id):
    res = supabase.table("candidats").select("*").eq("id", candidat_id).execute()
    return res.data[0] if res.data else None

@app.route("/")
def index():
    if not get_current_user():
        return redirect(url_for("login"))
    candidats = get_all_candidats()
    return render_template("index.html", candidats=candidats)

@app.route("/add", methods=["GET", "POST"])
def add_candidat():
    if not get_current_user():
        return redirect(url_for("login"))
    if request.method == "POST":
        nom = request.form["nom"]
        telephone = request.form["telephone"]
        phase = request.form["phase"]
        tarif = float(request.form["tarif"])
        versement = float(request.form["versement"])
        photo_url = None

        if "photo" in request.files:
            file = request.files["photo"]
            if file and file.filename:
                ext = file.filename.rsplit(".", 1)[-1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                file_bytes = file.read()
                if len(file_bytes) > 5_000_000:
                    flash("Photo trop grande (max 5 Mo)")
                    return redirect(url_for("add_candidat"))
                supabase.storage.from_("photos_candidats").upload(
                    filename, file_bytes, {"content-type": file.mimetype or "image/jpeg"}
                )
                photo_url = supabase.storage.from_("photos_candidats").get_public_url(filename)

        data = {
            "nom": nom,
            "telephone": telephone,
            "phase": phase,
            "tarif": tarif,
            "versement": versement,
            "photo_url": photo_url,
            "created_by": get_current_user()["email"]  # stocke l'email complet
        }
        result = supabase.table("candidats").insert(data).execute()
        candidat_id = result.data[0]["id"]

        add_notification(
            message=f"Nouveau candidat : {nom} (tél. {telephone}) par {get_current_user()['email'].split('@')[0]}",
            type_notif="candidat",
            lien=f"/candidat/{candidat_id}"
        )
        if versement > 0:
            update_solde(
                montant=versement,
                description=f"Versement initial de {nom}",
                type_transaction="versement",
                candidat_id=candidat_id,
                employe=get_current_user()["email"]
            )
        flash("Candidat ajouté")
        return redirect(url_for("index"))
    return render_template("add_candidat.html")

@app.route("/candidat/<candidat_id>")
def candidat_detail(candidat_id):
    if not get_current_user():
        return redirect(url_for("login"))
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Candidat introuvable")
        return redirect(url_for("index"))
    return render_template("candidat_detail.html", c=candidat)

@app.route("/edit/<candidat_id>", methods=["GET", "POST"])
def edit_candidat(candidat_id):
    if not get_current_user():
        return redirect(url_for("login"))
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Introuvable")
        return redirect(url_for("index"))
    if request.method == "POST":
        nom = request.form["nom"]
        telephone = request.form["telephone"]
        phase = request.form["phase"]
        tarif = float(request.form["tarif"])
        versement = float(request.form["versement"])
        photo_url = candidat["photo_url"]

        if "photo" in request.files:
            file = request.files["photo"]
            if file and file.filename:
                ext = file.filename.rsplit(".", 1)[-1].lower()
                filename = f"{uuid.uuid4()}.{ext}"
                file_bytes = file.read()
                if len(file_bytes) > 5_000_000:
                    flash("Photo trop grande (max 5 Mo)")
                    return redirect(url_for("edit_candidat", candidat_id=candidat_id))
                supabase.storage.from_("photos_candidats").upload(
                    filename, file_bytes, {"content-type": file.mimetype or "image/jpeg"}
                )
                photo_url = supabase.storage.from_("photos_candidats").get_public_url(filename)

        ancien_versement = candidat["versement"]
        if versement != ancien_versement:
            difference = versement - ancien_versement
            update_solde(
                montant=difference,
                description=f"Ajustement versement pour {nom}",
                type_transaction="versement",
                candidat_id=candidat_id,
                employe=get_current_user()["email"]
            )

        supabase.table("candidats").update({
            "nom": nom,
            "telephone": telephone,
            "phase": phase,
            "tarif": tarif,
            "versement": versement,
            "photo_url": photo_url,
            "updated_at": datetime.now().isoformat()
        }).eq("id", candidat_id).execute()

        flash("Modifié")
        return redirect(url_for("candidat_detail", candidat_id=candidat_id))
    return render_template("add_candidat.html", candidat=candidat)

@app.route("/delete/<candidat_id>")
def delete_candidat(candidat_id):
    if not get_current_user():
        return redirect(url_for("login"))
    supabase.table("candidats").delete().eq("id", candidat_id).execute()
    flash("Supprimé")
    return redirect(url_for("index"))

# ---------- Caisse et notifications ----------
@app.route("/api/notifications/count")
def notifications_count():
    if not get_current_user():
        return jsonify({"count": 0})
    result = supabase.table("notifications").select("id", count="exact").eq("lu", False).execute()
    return jsonify({"count": result.count})

@app.route("/notifications")
def notifications_list():
    if not get_current_user():
        return redirect(url_for("login"))
    notifs = supabase.table("notifications").select("*").order("created_at", desc=True).execute().data
    supabase.table("notifications").update({"lu": True}).eq("lu", False).execute()
    return render_template("notifications.html", notifications=notifs)

@app.route("/caisse")
def caisse():
    if not get_current_user():
        return redirect(url_for("login"))
    solde_row = supabase.table("caisse").select("solde").execute().data
    solde = solde_row[0]["solde"] if solde_row else 0
    transactions = supabase.table("transactions").select("*").order("date", desc=True).limit(100).execute().data
    return render_template("caisse.html", solde=solde, transactions=transactions, admin=is_admin())

@app.route("/add_mouvement", methods=["GET", "POST"])
def add_mouvement():
    if not get_current_user():
        return redirect(url_for("login"))
    if request.method == "POST":
        description = request.form["description"]
        montant = -abs(float(request.form["montant"]))
        employe = request.form["employe"]
        update_solde(montant, description, "depense", employe=employe)
        flash("Mouvement enregistré")
        return redirect(url_for("caisse"))
    return render_template("mouvement_form.html")

# ---------- Admin : réinitialisation, ajustement caisse et rapports ----------
@app.route("/admin/reset_caisse", methods=["POST"])
def reset_caisse():
    if not is_admin():
        flash("Accès réservé à l'administrateur (Khaled)")
        return redirect(url_for("caisse"))
    caisse_row = supabase.table("caisse").select("id").execute().data[0]
    supabase.table("caisse").update({"solde": 0, "updated_at": datetime.now().isoformat()}).eq("id", caisse_row["id"]).execute()
    supabase.table("transactions").insert({
        "description": "Réinitialisation de la caisse par admin",
        "montant": 0,
        "type": "reset",
        "created_by": get_current_user()["email"]
    }).execute()
    add_notification("Caisse réinitialisée par l'administrateur", "caisse", "/caisse")
    flash("Caisse remise à zéro")
    return redirect(url_for("caisse"))

@app.route("/admin/ajuster_caisse", methods=["POST"])
def ajuster_caisse():
    if not is_admin():
        flash("Accès réservé à l'administrateur")
        return redirect(url_for("caisse"))
    
    montant = float(request.form["montant"])
    description = request.form["description"]
    employe = "admin (" + get_current_user()["email"].split('@')[0] + ")"
    
    update_solde(montant, description, "ajustement_admin", employe=employe)
    
    flash(f"Caisse ajustée de {montant} DA : {description}")
    return redirect(url_for("caisse"))

@app.route("/admin/rapports")
def admin_rapports():
    if not is_admin():
        flash("Accès réservé à l'administrateur")
        return redirect(url_for("index"))
    retraits = supabase.table("transactions").select("created_by, montant").eq("type", "depense").execute().data
    totals = {}
    for r in retraits:
        emp = r["created_by"]
        montant = abs(r["montant"])
        totals[emp] = totals.get(emp, 0) + montant
    resets = supabase.table("transactions").select("*").eq("type", "reset").order("date", desc=True).execute().data
    return render_template("admin_rapports.html", totals=totals, resets=resets)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)