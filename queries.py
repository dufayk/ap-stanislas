"""Requetes de lecture partagees par les differents ecrans.

Regle de rattachement eleve <-> creneau : un eleve est eligible a un AP si
l'un de ses groupes figure dans les colonnes Eleves..Eleves5 du creneau,
ou si sa classe y figure directement (cas de "1STMG" sur AP7).


Un enseignant ne peut proposer un eleve que sur un creneau de sa propre
discipline : la colonne Discipline de l'onglet ENSEIGNANTS est comparee a la
colonne MATIERE de l'onglet CRENEAUX (comparaison insensible a la casse et
aux accents).
"""
import util

# Un eleve appartient a un "groupe" au sens large : ses groupes de barrette
# ou sa classe. Cette expression est reutilisee partout.
GROUPES_ELEVE = """
    SELECT groupe FROM eleve_groupes WHERE eleve_id = e.id
    UNION SELECT e.classe
"""


def periodes(conn):
    return conn.execute("SELECT * FROM periodes ORDER BY ordre").fetchall()


def periode(conn, periode_id):
    return conn.execute("SELECT * FROM periodes WHERE id = ?", (periode_id,)).fetchone()


def enseignants(conn):
    return conn.execute(
        "SELECT * FROM enseignants WHERE actif = 1 ORDER BY nom, prenom"
    ).fetchall()


def enseignant(conn, ens_id):
    return conn.execute("SELECT * FROM enseignants WHERE id = ?", (ens_id,)).fetchone()


def groupes_enseignant(conn, ens_id):
    return [
        r["groupe"]
        for r in conn.execute(
            "SELECT groupe FROM enseignant_groupes WHERE enseignant_id = ? ORDER BY groupe",
            (ens_id,),
        )
    ]


def accompagnants(conn):
    """Liste des professeurs accompagnants (onglet CRENEAUX), dedoublonnee."""
    return conn.execute(
        """SELECT accompagnant_nom AS nom, accompagnant_prenom AS prenom,
                  COUNT(*) AS nb_creneaux
             FROM creneaux WHERE actif = 1
            GROUP BY accompagnant_nom, accompagnant_prenom
            ORDER BY accompagnant_nom, accompagnant_prenom"""
    ).fetchall()


def creneaux_accompagnant(conn, nom, prenom):
    return conn.execute(
        """SELECT * FROM creneaux
            WHERE actif = 1 AND accompagnant_nom = ? AND IFNULL(accompagnant_prenom, '') = ?
            ORDER BY ordre""",
        (nom, prenom or ""),
    ).fetchall()


def eleves_du_groupe(conn, groupe):
    """Eleves rattaches a un code de groupe (groupe de barrette ou classe)."""
    return conn.execute(
        """SELECT DISTINCT e.* FROM eleves e
            WHERE e.actif = 1
              AND ? IN (SELECT groupe FROM eleve_groupes WHERE eleve_id = e.id
                        UNION SELECT e.classe)
            ORDER BY e.nom, e.prenom""",
        (groupe,),
    ).fetchall()


def creneaux_eligibles(conn, eleve_id, discipline=None):
    """Creneaux AP auxquels un eleve donne est eligible.

    Si `discipline` est fournie (celle de l'enseignant proposant), la liste est
    restreinte aux creneaux de cette matiere. Le filtre est applique en Python
    car SQLite ne sait pas comparer en ignorant les accents.
    """
    creneaux = conn.execute(
        """SELECT DISTINCT c.* FROM creneaux c
             JOIN creneau_groupes cg ON cg.creneau_code = c.code
            WHERE c.actif = 1
              AND cg.groupe IN (SELECT groupe FROM eleve_groupes WHERE eleve_id = :eid
                                UNION SELECT classe FROM eleves WHERE id = :eid)
            ORDER BY c.ordre""",
        {"eid": eleve_id},
    ).fetchall()
    if discipline is None:
        return creneaux
    return [c for c in creneaux if util.meme_discipline(discipline, c["matiere"])]


def propositions_enseignant(conn, periode_id, ens_id):
    """Propositions faites par un enseignant, indexees par eleve.

    Un enseignant ne propose que dans sa discipline : il y a donc au plus une
    proposition par eleve et par periode.
    """
    rows = conn.execute(
        """SELECT p.*, c.horaire, c.accompagnant_nom, c.accompagnant_prenom
             FROM propositions p
             LEFT JOIN creneaux c ON c.code = p.creneau_code
            WHERE p.periode_id = ? AND p.enseignant_id = ?""",
        (periode_id, ens_id),
    ).fetchall()
    return {r["eleve_id"]: r for r in rows}


def occupation_creneaux(conn, periode_id):
    """Nombre de places prises par creneau pour une periode."""
    rows = conn.execute(
        """SELECT creneau_code, COUNT(DISTINCT eleve_id) AS n
             FROM propositions
            WHERE periode_id = ? AND statut = 'retenu'
            GROUP BY creneau_code""",
        (periode_id,),
    ).fetchall()
    return {r["creneau_code"]: r["n"] for r in rows}


def inscrits_creneau(conn, periode_id, creneau_code):
    """Eleves retenus sur un creneau, avec groupe concerne et proposant."""
    return conn.execute(
        """SELECT e.nom, e.prenom, e.classe, p.groupe, p.created_at,
                  ens.nom AS ens_nom, ens.prenom AS ens_prenom
             FROM propositions p
             JOIN eleves e      ON e.id = p.eleve_id
             JOIN enseignants ens ON ens.id = p.enseignant_id
            WHERE p.periode_id = ? AND p.creneau_code = ? AND p.statut = 'retenu'
            ORDER BY e.nom, e.prenom""",
        (periode_id, creneau_code),
    ).fetchall()


def non_retenus_matiere(conn, periode_id, matiere):
    """Propositions non retenues dans une matiere.

    Le creneau n'etant plus choisi par le proposant, un refus se rattache a la
    matiere et non a un creneau precis.
    """
    rows = conn.execute(
        """SELECT e.nom, e.prenom, e.classe, p.motif, p.matiere,
                  ens.nom AS ens_nom, ens.prenom AS ens_prenom
             FROM propositions p
             JOIN eleves e        ON e.id = p.eleve_id
             JOIN enseignants ens ON ens.id = p.enseignant_id
            WHERE p.periode_id = ? AND p.statut <> 'retenu'
            ORDER BY e.nom, e.prenom""",
        (periode_id,),
    ).fetchall()
    return [r for r in rows if util.normaliser(r["matiere"]) == util.normaliser(matiere)]


# ----------------------------------------------------------------------
# Phase 2 : recapitulatif par eleve (professeur principal / direction)
# ----------------------------------------------------------------------

def recap_eleves(conn, classe=None, prof_principal=None):
    """Une ligne par eleve, avec ses AP retenus periode par periode."""
    sql = "SELECT * FROM eleves WHERE actif = 1"
    params = []
    if classe:
        sql += " AND classe = ?"
        params.append(classe)
    if prof_principal:
        sql += " AND prof_principal = ?"
        params.append(prof_principal)
    sql += " ORDER BY classe, nom, prenom"
    eleves = conn.execute(sql, params).fetchall()

    inscriptions = conn.execute(
        """SELECT p.eleve_id, p.periode_id, p.creneau_code, c.matiere, c.horaire,
                  c.accompagnant_nom
             FROM propositions p
             JOIN creneaux c ON c.code = p.creneau_code
            WHERE p.statut = 'retenu'"""
    ).fetchall()

    par_eleve = {}
    for r in inscriptions:
        par_eleve.setdefault(r["eleve_id"], {})[r["periode_id"]] = r
    return eleves, par_eleve


def classes(conn):
    return [
        r["classe"]
        for r in conn.execute(
            "SELECT DISTINCT classe FROM eleves WHERE actif = 1 AND classe IS NOT NULL"
            " ORDER BY classe"
        )
    ]


def profs_principaux(conn):
    return [
        r["prof_principal"]
        for r in conn.execute(
            "SELECT DISTINCT prof_principal FROM eleves"
            " WHERE actif = 1 AND prof_principal IS NOT NULL ORDER BY prof_principal"
        )
    ]
