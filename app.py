import os
import uuid
from datetime import datetime, date
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from supabase import create_client

app = Flask(__name__)
app.secret_key = os.urandom(24)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not SUPABASE_PUBLISHABLE_KEY:
    raise Exception("Variables SUPABASE_URL et SUPABASE_PUBLISHABLE_KEY manquantes")

supabase = create_client(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY)

# ---------- Fonctions utilitaires ----------
def get_current_user():
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

def log_action(action, details, candidat_id=None, montant=None):
    user = get_current_user()
    if user:
        supabase.table("audit_log").insert({
            "action": action,
            "details": details,
            "candidat_id": candidat_id,
            "montant": montant,
            "user_email": user["email"]
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
        "created_by": employe,
        "date_transaction": datetime.now().isoformat()
    }).execute()
    
    if montant < 0:
        add_notification(
            message=f"{employe} a retiré {abs(montant)} DA : {description}",
            type_notif="caisse",
            lien="/caisse"
        )
    return nouveau_solde

def get_candidats_by_statut(statut):
    return supabase.table("candidats").select("*").eq("statut", statut).order("created_at", desc=True).execute().data

def get_stats_jour():
    aujourdhui = date.today().isoformat()
    demain = (date.today().replace(day=date.today().day+1)).isoformat()
    nouveaux = supabase.table("candidats").select("id", count="exact").gte("created_at", aujourdhui).lt("created_at", demain).execute().count
    versements = supabase.table("transactions").select("montant").eq("type", "versement").gte("date_transaction", aujourdhui).lt("date_transaction", demain).execute().data
    total_versements = sum([abs(t["montant"]) for t in versements])
    retraits = supabase.table("transactions").select("montant").eq("type", "depense").gte("date_transaction", aujourdhui).lt("date_transaction", demain).execute().data
    total_retraits = sum([abs(t["montant"]) for t in retraits])
    transactions = supabase.table("transactions").select("*").gte("date_transaction", aujourdhui).lt("date_transaction", demain).order("date_transaction", desc=True).execute().data
    return {
        "date": aujourdhui,
        "nouveaux": nouveaux,
        "versements": total_versements,
        "retraits": total_retraits,
        "transactions": transactions
    }

def get_candidats_stats():
    total_actifs = supabase.table("candidats").select("id", count="exact").eq("statut", "actif").execute().count
    code = supabase.table("candidats").select("id", count="exact").eq("statut", "actif").eq("phase", "code").execute().count
    creneau = supabase.table("candidats").select("id", count="exact").eq("statut", "actif").eq("phase", "creneau").execute().count
    circuit = supabase.table("candidats").select("id", count="exact").eq("statut", "actif").eq("phase", "circuit").execute().count
    admis = supabase.table("candidats").select("id", count="exact").eq("statut", "admis").execute().count
    return {
        "total_actifs": total_actifs,
        "code": code,
        "creneau": creneau,
        "circuit": circuit,
        "admis": admis
    }

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

# ---------- Routes candidats ----------
def get_all_candidats():
    return supabase.table("candidats").select("*").eq("statut", "actif").order("created_at", desc=True).execute().data

def get_candidat_by_id(candidat_id):
    res = supabase.table("candidats").select("*").eq("id", candidat_id).execute()
    return res.data[0] if res.data else None

@app.route("/")
def index():
    if not get_current_user():
        return redirect(url_for("login"))
    if is_admin():
        stats = get_stats_jour()
        candidats_stats = get_candidats_stats()
        return render_template("dashboard.html", stats=stats, candidats_stats=candidats_stats, admin=True)
    else:
        return redirect(url_for("liste"))

@app.route("/liste")
def liste():
    if not get_current_user():
        return redirect(url_for("login"))
    candidats = get_all_candidats()
    return render_template("index.html", candidats=candidats)

@app.route("/admis")
def admis():
    if not get_current_user():
        return redirect(url_for("login"))
    admis = get_candidats_by_statut("admis")
    return render_template("admis.html", admis=admis)

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
            "created_by": get_current_user()["email"],
            "updated_by": get_current_user()["email"],
            "statut": "actif"
        }
        result = supabase.table("candidats").insert(data).execute()
        candidat_id = result.data[0]["id"]

        log_action("ajout_candidat", f"Ajout candidat {nom} (tel: {telephone}, phase: {phase}, tarif: {tarif}, versement initial: {versement})", candidat_id)
        add_notification(
            message=f"Nouveau candidat : {nom} par {get_current_user()['email'].split('@')[0]}",
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
        return redirect(url_for("liste"))
    return render_template("add_candidat.html")

@app.route("/candidat/<candidat_id>")
def candidat_detail(candidat_id):
    if not get_current_user():
        return redirect(url_for("login"))
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Candidat introuvable")
        return redirect(url_for("liste"))
    return render_template("candidat_detail.html", c=candidat, admin=is_admin())

@app.route("/changer_phase/<candidat_id>", methods=["POST"])
def changer_phase(candidat_id):
    if not get_current_user():
        flash("Veuillez vous connecter")
        return redirect(url_for("login"))
    
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Candidat introuvable")
        return redirect(url_for("liste"))
    
    phase_actuelle = candidat["phase"]
    phases = ["code", "creneau", "circuit"]
    if phase_actuelle in phases:
        index = phases.index(phase_actuelle)
        if index < len(phases) - 1:
            nouvelle_phase = phases[index + 1]
            supabase.table("candidats").update({
                "phase": nouvelle_phase,
                "updated_by": get_current_user()["email"]
            }).eq("id", candidat_id).execute()
            
            log_action("changement_phase", f"Candidat {candidat['nom']} passe de {phase_actuelle} à {nouvelle_phase}", candidat_id)
            add_notification(
                message=f"📌 {candidat['nom']} a validé la phase {phase_actuelle} et passe à {nouvelle_phase}",
                type_notif="candidat",
                lien=f"/candidat/{candidat_id}"
            )
            flash(f"{candidat['nom']} passe à la phase {nouvelle_phase}.")
        else:
            flash(f"{candidat['nom']} est déjà en phase circuit. Cliquez sur 'Obtenir le permis' pour finaliser.")
    else:
        flash("Phase inconnue")
    return redirect(url_for("candidat_detail", candidat_id=candidat_id))

@app.route("/obtenir_permis/<candidat_id>", methods=["POST"])
def obtenir_permis(candidat_id):
    if not get_current_user():
        flash("Veuillez vous connecter")
        return redirect(url_for("login"))
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Candidat introuvable")
        return redirect(url_for("liste"))
    supabase.table("candidats").update({
        "statut": "admis",
        "date_obtention": datetime.now().isoformat(),
        "valide_par": get_current_user()["email"]
    }).eq("id", candidat_id).execute()
    log_action("obtention_permis", f"Candidat {candidat['nom']} a obtenu son permis (validé par {get_current_user()['email'].split('@')[0]})", candidat_id)
    add_notification(
        message=f"🎉 {candidat['nom']} a obtenu son permis ! Validé par {get_current_user()['email'].split('@')[0]}",
        type_notif="candidat",
        lien=f"/candidat/{candidat_id}"
    )
    flash(f"Félicitations ! {candidat['nom']} a obtenu son permis.")
    return redirect(url_for("candidat_detail", candidat_id=candidat_id))

@app.route("/revoquer_permis/<candidat_id>", methods=["POST"])
def revoquer_permis(candidat_id):
    if not get_current_user():
        flash("Veuillez vous connecter")
        return redirect(url_for("login"))
    
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Candidat introuvable")
        return redirect(url_for("admis"))
    
    supabase.table("candidats").update({
        "statut": "actif",
        "date_obtention": None,
        "valide_par": None,
        "updated_by": get_current_user()["email"]
    }).eq("id", candidat_id).execute()
    
    log_action("revoquer_permis", f"Permis révoqué pour {candidat['nom']} (par {get_current_user()['email'].split('@')[0]})", candidat_id)
    add_notification(
        message=f"⚠️ Le permis de {candidat['nom']} a été révoqué par {get_current_user()['email'].split('@')[0]}",
        type_notif="candidat",
        lien=f"/candidat/{candidat_id}"
    )
    flash(f"Le permis de {candidat['nom']} a été révoqué. Il est de nouveau dans la liste des actifs.")
    return redirect(url_for("candidat_detail", candidat_id=candidat_id))

@app.route("/edit/<candidat_id>", methods=["GET", "POST"])
def edit_candidat(candidat_id):
    if not get_current_user():
        return redirect(url_for("login"))
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Introuvable")
        return redirect(url_for("liste"))
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
        difference = 0
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
            "updated_at": datetime.now().isoformat(),
            "updated_by": get_current_user()["email"]
        }).eq("id", candidat_id).execute()

        log_action("modification_candidat", f"Modification candidat {candidat['nom']} (ID {candidat_id}) : nouveau nom={nom}, tel={telephone}, phase={phase}, tarif={tarif}, versement={versement} (différence caisse={difference})", candidat_id)

        flash("Modifié")
        return redirect(url_for("candidat_detail", candidat_id=candidat_id))
    return render_template("add_candidat.html", candidat=candidat)

@app.route("/add_paiement/<candidat_id>", methods=["POST"])
def add_paiement(candidat_id):
    if not get_current_user():
        flash("Veuillez vous connecter")
        return redirect(url_for("login"))
    
    candidat = get_candidat_by_id(candidat_id)
    if not candidat:
        flash("Candidat introuvable")
        return redirect(url_for("liste"))
    
    montant = float(request.form["montant"])
    if montant <= 0:
        flash("Le montant doit être positif")
        return redirect(url_for("candidat_detail", candidat_id=candidat_id))
    
    nouveau_versement = candidat["versement"] + montant
    
    supabase.table("candidats").update({
        "versement": nouveau_versement,
        "updated_by": get_current_user()["email"],
        "updated_at": datetime.now().isoformat()
    }).eq("id", candidat_id).execute()
    
    update_solde(
        montant=montant,
        description=f"Versement de {candidat['nom']}",
        type_transaction="versement",
        candidat_id=candidat_id,
        employe=get_current_user()["email"]
    )
    
    log_action("ajout_versement", f"Ajout de {montant} DA au versement de {candidat['nom']} (nouveau total: {nouveau_versement})", candidat_id)
    add_notification(
        message=f"💵 {candidat['nom']} a effectué un versement de {montant} DA",
        type_notif="candidat",
        lien=f"/candidat/{candidat_id}"
    )
    flash(f"Versement de {montant} DA ajouté pour {candidat['nom']}")
    return redirect(url_for("candidat_detail", candidat_id=candidat_id))

@app.route("/delete/<candidat_id>")
def delete_candidat(candidat_id):
    if not get_current_user():
        return redirect(url_for("login"))
    candidat = get_candidat_by_id(candidat_id)
    if candidat:
        log_action("suppression_candidat", f"Suppression candidat {candidat['nom']} (ID {candidat_id})", candidat_id)
    supabase.table("candidats").delete().eq("id", candidat_id).execute()
    flash("Supprimé")
    return redirect(url_for("liste"))

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
    transactions = supabase.table("transactions").select("*").order("date_transaction", desc=True).limit(100).execute().data
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
        log_action("retrait_caisse", f"Retrait de {abs(montant)} DA par {employe} : {description}")
        flash("Mouvement enregistré")
        return redirect(url_for("caisse"))
    return render_template("mouvement_form.html")

# ---------- Admin ----------
@app.route("/admin/reset_caisse", methods=["POST"])
def reset_caisse():
    if not is_admin():
        flash("Accès réservé à l'administrateur")
        return redirect(url_for("caisse"))
    caisse_row = supabase.table("caisse").select("id").execute().data[0]
    supabase.table("caisse").update({"solde": 0, "updated_at": datetime.now().isoformat()}).eq("id", caisse_row["id"]).execute()
    supabase.table("transactions").insert({
        "description": "Réinitialisation de la caisse par admin",
        "montant": 0,
        "type": "reset",
        "created_by": get_current_user()["email"],
        "date_transaction": datetime.now().isoformat()
    }).execute()
    log_action("reset_caisse", "Caisse réinitialisée à 0 DA")
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
    log_action("ajustement_caisse", f"Ajustement manuel de {montant} DA : {description}")
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
    resets = supabase.table("transactions").select("*").eq("type", "reset").order("date_transaction", desc=True).execute().data
    return render_template("admin_rapports.html", totals=totals, resets=resets)

@app.route("/admin/journal")
def admin_journal():
    if not is_admin():
        flash("Accès réservé à l'administrateur")
        return redirect(url_for("index"))
    logs = supabase.table("audit_log").select("*").order("created_at", desc=True).limit(200).execute().data
    return render_template("journal.html", logs=logs)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)