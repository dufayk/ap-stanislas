"""Application web de gestion de l'Accompagnement Personnalise (AP).

Lycee Stanislas - Cannes.

Authentification : un unique mot de passe partage pour l'etablissement.
Apres saisie, l'utilisateur choisit lui-meme sa periode, son role et son
identite dans des listes deroulantes (pas de compte individuel).
"""
import datetime as dt
import os
import secrets

from werkzeug.utils import secure_filename

from flask import (Flask, abort, flash, redirect, render_template, request,
                   session, url_for)

import allocation
import config
import db as dbmod
import importer
import queries as q


def create_app():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=dt.timedelta(days=30),
        MAX_CONTENT_LENGTH=config.TAILLE_MAX_ENVOI,
    )
    dbmod.init_app(app)
    dbmod.init_db()
    register_routes(app)
    return app


def maintenant():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connecte():
    return session.get("auth") is True


def register_routes(app):

    # ------------------------------------------------------------------
    # Authentification
    # ------------------------------------------------------------------
    @app.before_request
    def exiger_mot_de_passe():
        if request.endpoint in ("login", "static"):
            return None
        if not connecte():
            return redirect(url_for("login", next=request.full_path))
        return None

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            saisi = request.form.get("mot_de_passe", "")
            if secrets.compare_digest(saisi, config.APP_PASSWORD):
                session.permanent = True
                session["auth"] = True
                return redirect(request.args.get("next") or url_for("accueil"))
            flash("Mot de passe incorrect.", "erreur")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------------
    # Accueil : choix de la periode et du role
    # ------------------------------------------------------------------
    @app.route("/", methods=["GET", "POST"])
    def accueil():
        conn = dbmod.get_db()
        periodes = q.periodes(conn)
        if request.method == "POST":
            periode_id = request.form.get("periode_id", type=int)
            role = request.form.get("role")
            if not periode_id or role not in ("enseignant", "accompagnant", "recap"):
                flash("Choisissez une periode et un role.", "erreur")
                return redirect(url_for("accueil"))
            session["periode_id"] = periode_id
            return redirect(url_for(role))
        return render_template(
            "accueil.html", periodes=periodes, periode_id=session.get("periode_id")
        )

    def periode_courante(conn):
        periode = q.periode(conn, session.get("periode_id") or 0)
        if periode is None:
            periodes = q.periodes(conn)
            if not periodes:
                abort(500, "Aucune periode : importez d'abord le classeur Excel.")
            periode = periodes[0]
            session["periode_id"] = periode["id"]
        return periode

    # ------------------------------------------------------------------
    # Role 1 : enseignant proposant
    # ------------------------------------------------------------------
    @app.route("/enseignant")
    def enseignant():
        conn = dbmod.get_db()
        periode = periode_courante(conn)
        liste = q.enseignants(conn)
        ont_propose = q.enseignants_ayant_propose(conn, periode["id"])

        ens_id = request.args.get("ens_id", type=int) or session.get("ens_id")
        ens = q.enseignant(conn, ens_id) if ens_id else None
        if ens is None:
            return render_template(
                "enseignant.html",
                periode=periode,
                enseignants=liste,
                ens=None,
                ont_propose=ont_propose,
            )
        session["ens_id"] = ens["id"]

        deja = q.propositions_enseignant(conn, periode["id"], ens["id"])
        occupation = q.occupation_creneaux(conn, periode["id"])

        groupes = []
        for code in q.groupes_enseignant(conn, ens["id"]):
            eleves = []
            for eleve in q.eleves_du_groupe(conn, code):
                # Creneaux possibles pour cet eleve dans la discipline du prof.
                # Ils sont affiches a titre indicatif : l'enseignant ne choisit
                # pas, le moteur retiendra le moins rempli.
                possibles = [
                    {"creneau": cre, "places": occupation.get(cre["code"], 0)}
                    for cre in q.creneaux_eligibles(conn, eleve["id"], ens["discipline"])
                ]
                eleves.append(
                    {
                        "eleve": eleve,
                        "possibles": possibles,
                        "prop": deja.get(eleve["id"]),
                    }
                )
            groupes.append({"code": code, "eleves": eleves})

        return render_template(
            "enseignant.html",
            periode=periode,
            enseignants=liste,
            ens=ens,
            groupes=groupes,
            ont_propose=ont_propose,
        )

    @app.route("/enseignant/enregistrer", methods=["POST"])
    def enseignant_enregistrer():
        conn = dbmod.get_db()
        periode = periode_courante(conn)
        ens_id = request.form.get("ens_id", type=int)
        ens = q.enseignant(conn, ens_id) if ens_id else None
        if ens is None:
            abort(400, "Enseignant inconnu.")

        # Ce que l'enseignant vient de cocher : "eleve_id:GROUPE"
        souhaite = {}
        for valeur in request.form.getlist("prop"):
            try:
                eleve_id, groupe = valeur.split(":", 1)
                eleve_id = int(eleve_id)
            except ValueError:
                continue
            if _proposition_valide(conn, ens, eleve_id, groupe):
                souhaite[eleve_id] = groupe

        existant = q.propositions_enseignant(conn, periode["id"], ens["id"])

        for eleve_id, prop in existant.items():
            if eleve_id not in souhaite:
                conn.execute("DELETE FROM propositions WHERE id = ?", (prop["id"],))
        for eleve_id, groupe in souhaite.items():
            if eleve_id not in existant:
                conn.execute(
                    """INSERT INTO propositions
                       (periode_id, eleve_id, enseignant_id, matiere, groupe,
                        created_at, statut)
                       VALUES (?, ?, ?, ?, ?, ?, 'en_attente')""",
                    (periode["id"], eleve_id, ens["id"], ens["discipline"], groupe,
                     maintenant()),
                )

        allocation.recompute_all(conn)
        flash("Propositions enregistrees.", "ok")
        return redirect(url_for("enseignant", ens_id=ens["id"]))

    def _proposition_valide(conn, ens, eleve_id, groupe):
        """Verifie que l'enseignant encadre bien ce groupe et que l'eleve en
        fait partie (garde-fou cote serveur, la page pouvant etre falsifiee).

        Le creneau n'est pas verifie ici : il n'est pas choisi par l'enseignant
        mais attribue par le moteur, qui n'ouvre que les creneaux de la matiere
        auxquels l'eleve est eligible.
        """
        if groupe not in q.groupes_enseignant(conn, ens["id"]):
            return False
        appartient = conn.execute(
            """SELECT 1 FROM eleves e
                WHERE e.id = ? AND e.actif = 1
                  AND ? IN (SELECT groupe FROM eleve_groupes WHERE eleve_id = e.id
                            UNION SELECT e.classe)""",
            (eleve_id, groupe),
        ).fetchone()
        return bool(appartient)

    # ------------------------------------------------------------------
    # Role 2 : professeur accompagnant (consultation)
    # ------------------------------------------------------------------
    @app.route("/accompagnant")
    def accompagnant():
        conn = dbmod.get_db()
        periode = periode_courante(conn)
        liste = q.accompagnants(conn)

        cle = request.args.get("acc") or session.get("acc")
        choisi = None
        for row in liste:
            if "%s|%s" % (row["nom"], row["prenom"] or "") == cle:
                choisi = row
                break
        if choisi is None:
            return render_template(
                "accompagnant.html", periode=periode, accompagnants=liste, choisi=None
            )
        session["acc"] = cle

        blocs, matieres = [], []
        for cre in q.creneaux_accompagnant(conn, choisi["nom"], choisi["prenom"]):
            blocs.append(
                {
                    "creneau": cre,
                    "retenus": q.inscrits_creneau(conn, periode["id"], cre["code"]),
                }
            )
            if cre["matiere"] and cre["matiere"] not in matieres:
                matieres.append(cre["matiere"])

        # Un refus n'est plus rattache a un creneau precis mais a une matiere.
        refuses = [
            {"matiere": m, "lignes": q.non_retenus_matiere(conn, periode["id"], m)}
            for m in matieres
        ]

        return render_template(
            "accompagnant.html",
            periode=periode,
            accompagnants=liste,
            choisi=choisi,
            cle=cle,
            blocs=blocs,
            refuses=refuses,
        )

    # ------------------------------------------------------------------
    # Role 3 (phase 2) : recapitulatif par eleve
    # ------------------------------------------------------------------
    @app.route("/recap")
    def recap():
        conn = dbmod.get_db()
        periodes = q.periodes(conn)
        classe = request.args.get("classe") or None
        pp = request.args.get("pp") or None
        groupe = request.args.get("groupe") or None
        prescripteur = request.args.get("prescripteur", type=int) or None
        eleves, par_eleve = q.recap_eleves(conn, classe, pp, groupe, prescripteur)
        return render_template(
            "recap.html",
            periodes=periodes,
            eleves=eleves,
            par_eleve=par_eleve,
            classes=q.classes(conn),
            profs=q.profs_principaux(conn),
            groupes=q.groupes(conn),
            enseignants=q.enseignants(conn),
            classe=classe,
            pp=pp,
            groupe=groupe,
            prescripteur=prescripteur,
        )

    # ------------------------------------------------------------------
    # Administration : re-import du classeur
    # ------------------------------------------------------------------
    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        conn = dbmod.get_db()
        if request.method == "POST":
            try:
                chemin = _classeur_envoye(request.files.get("classeur"))
                resume = importer.import_excel(chemin, conn=conn)
                allocation.recompute_all(conn)
                flash("Import termine : %s" % resume, "ok")
            except Exception as exc:  # noqa: BLE001 - message affiche a l'utilisateur
                conn.rollback()
                flash("Echec de l'import : %s" % exc, "erreur")
            return redirect(url_for("admin"))
        imports = conn.execute(
            "SELECT * FROM imports ORDER BY id DESC LIMIT 10"
        ).fetchall()
        creneaux = conn.execute(
            "SELECT * FROM creneaux WHERE actif = 1 ORDER BY ordre"
        ).fetchall()
        chemin = config.chemin_excel()
        return render_template(
            "admin.html",
            imports=imports,
            creneaux=creneaux,
            excel=chemin,
            excel_present=os.path.exists(chemin),
        )

    def _classeur_envoye(fichier):
        """Enregistre le classeur envoye et retourne son chemin.

        Retourne None si aucun fichier n'a ete joint : on reimporte alors le
        dernier classeur connu. Le fichier remplace le precedent, de sorte
        qu'une seule copie des donnees eleves subsiste sur le serveur.
        """
        if fichier is None or not fichier.filename:
            return None
        nom = secure_filename(fichier.filename)
        if not nom.lower().endswith((".xlsx", ".xlsm")):
            raise ValueError("Le fichier doit etre un classeur Excel (.xlsx).")
        os.makedirs(config.INSTANCE_DIR, exist_ok=True)
        fichier.save(config.EXCEL_ENVOYE)
        return config.EXCEL_ENVOYE


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
