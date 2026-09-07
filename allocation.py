"""Moteur d'affectation : transforme les propositions en inscriptions.

L'enseignant propose un eleve *pour sa matiere*, pas pour un creneau precis :
c'est le moteur qui choisit, parmi les creneaux de cette matiere auxquels
l'eleve est eligible, celui qui convient.

Le calcul est entierement deterministe et rejoue a chaque modification :
aucune inscription n'est figee en base autrement que comme resultat du moteur.
Ajouter ou retirer une proposition reclasse donc correctement tous les eleves.

Une periode **figee** est laissee telle quelle : ses propositions gardent
leur statut et leur creneau, quoi qu'il arrive au classeur ensuite. Ses
inscrits restent pris en compte pour la regle de rotation de la periode
suivante. C'est le seul moyen de figer un resultat, tout le reste etant
recalcule.

Regles (cf. cahier des charges) :

Choix du creneau
  - parmi les creneaux de la matiere ouverts a l'eleve, on retient le
    **moins rempli** (a egalite, le premier dans l'ordre du classeur) ;
  - un creneau complet, ou place le meme jour qu'un AP deja retenu pour
    l'eleve, est ecarte : un eleve ne suit qu'un seul AP par jour ;
  - un horaire dont une autre matiere proposee pour le meme eleve a besoin
    (parce qu'elle n'a qu'un seul creneau possible) est evite tant qu'une
    alternative existe : sans cela, une matiere servie en premier bloquerait
    une matiere sans solution de repli ;
  - un eleve n'est inscrit qu'une fois par matiere et par periode.

Periode 1
  - 15 places max par creneau, premier arrive premier servi (horodatage).
  - Un eleve deja retenu un jour donne ne peut pas l'etre une seconde fois
    le meme jour : la proposition la plus ancienne l'emporte.

Periodes 2 a 5, ordre de priorite
  1. Le francais est prioritaire sur les autres matieres.
  2. Priorite aux eleves non inscrits en AP a la periode precedente.
  3. A egalite, premier arrive premier servi (horodatage).
"""
import config
import util
from util import normaliser

STATUT_RETENU = "retenu"
STATUT_REJETE = "rejete"


def est_prioritaire(matiere):
    """Vrai si la matiere beneficie de la priorite (francais)."""
    return normaliser(matiere) == normaliser(config.MATIERE_PRIORITAIRE)


def _periodes(conn):
    return conn.execute("SELECT * FROM periodes ORDER BY ordre").fetchall()


def recompute_all(conn, commit=True):
    """Recalcule toutes les periodes, dans l'ordre.

    Les periodes >= 2 dependent du resultat de la periode precedente : elles
    doivent donc etre traitees en cascade, d'ou le recalcul global.
    """
    creneaux = conn.execute(
        "SELECT * FROM creneaux WHERE actif = 1 ORDER BY ordre"
    ).fetchall()
    eligibilite = {}
    inscrits_precedents = set()
    for periode in _periodes(conn):
        inscrits_precedents = _recompute_periode(
            conn, periode, inscrits_precedents, creneaux, eligibilite
        )
    if commit:
        conn.commit()


def _creneaux_ouverts(conn, cache, eleve_id):
    """Codes des creneaux ouverts a cet eleve (par groupe ou par classe)."""
    if eleve_id not in cache:
        cache[eleve_id] = {
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT cg.creneau_code FROM creneau_groupes cg
                    WHERE cg.groupe IN (SELECT groupe FROM eleve_groupes
                                         WHERE eleve_id = :eid
                                        UNION SELECT classe FROM eleves WHERE id = :eid)""",
                {"eid": eleve_id},
            )
        }
    return cache[eleve_id]


def _horaires_contraints(propositions, candidats_par_prop):
    """Horaires dont une matiere a imperativement besoin, par eleve.

    Resultat : (eleve_id, matiere normalisee) -> horaires reserves *aux autres*
    matieres proposees pour cet eleve. Une matiere qui n'a qu'un seul creneau
    possible n'a aucune marge : les matieres qui en ont doivent lui ceder
    l'horaire.
    """
    besoins = {}   # eleve_id -> liste de (matiere normalisee, horaire)
    for prop in propositions:
        candidats = candidats_par_prop[prop["id"]]
        if len(candidats) == 1:
            besoins.setdefault(prop["eleve_id"], []).append(
                (normaliser(prop["matiere"]), candidats[0]["horaire"])
            )

    contraints = {}
    for prop in propositions:
        matiere = normaliser(prop["matiere"])
        contraints[(prop["eleve_id"], matiere)] = [
            horaire
            for autre, horaire in besoins.get(prop["eleve_id"], [])
            if autre != matiere
        ]
    return contraints


def est_figee(periode):
    """Vrai si la periode est cloturee. Tolere une base pas encore migree."""
    return "figee" in periode.keys() and bool(periode["figee"])


def _inscrits(conn, periode):
    """Eleves retenus d'une periode, lus en base sans rien recalculer."""
    return {
        r[0]
        for r in conn.execute(
            "SELECT eleve_id FROM propositions WHERE periode_id = ? AND statut = ?",
            (periode["id"], STATUT_RETENU),
        )
    }


def _recompute_periode(conn, periode, inscrits_precedents, creneaux, eligibilite):
    """Affecte les propositions d'une periode. Retourne les eleves retenus."""
    if est_figee(periode):
        # Rien n'est recalcule : on se contente de transmettre les inscrits a
        # la periode suivante, qui en a besoin pour la regle de rotation.
        return _inscrits(conn, periode)

    propositions = conn.execute(
        """SELECT p.*, e.actif AS eleve_actif
             FROM propositions p
             JOIN eleves e ON e.id = p.eleve_id
            WHERE p.periode_id = ?""",
        (periode["id"],),
    ).fetchall()

    premiere_periode = periode["ordre"] <= 1

    def tri(prop):
        if premiere_periode:
            return (0, 0, prop["created_at"], prop["id"])
        return (
            0 if est_prioritaire(prop["matiere"]) else 1,
            1 if prop["eleve_id"] in inscrits_precedents else 0,
            prop["created_at"],
            prop["id"],
        )

    occupation = {}          # creneau_code -> nb de places prises
    retenu_par_matiere = {}  # (eleve_id, matiere normalisee) -> creneau_code
    # eleve_id -> [(horaire, creneau_code)] deja retenus. Une liste et non un
    # index par libelle : le conflit se juge sur le jour, pas sur le texte.
    horaires_retenus = {}

    def horaire_pris(eleve_id, horaire):
        """Code du creneau deja retenu qui empeche cet horaire, sinon None."""
        for pris, code in horaires_retenus.get(eleve_id, ()):
            if util.horaires_incompatibles(pris, horaire):
                return code
        return None
    resultats = []           # (statut, creneau_code, motif, proposition_id)
    inscrits = set()

    candidats_par_prop = {}
    for prop in propositions:
        ouverts = _creneaux_ouverts(conn, eligibilite, prop["eleve_id"])
        candidats_par_prop[prop["id"]] = [
            c
            for c in creneaux
            if c["code"] in ouverts
            and normaliser(c["matiere"]) == normaliser(prop["matiere"])
        ]
    horaires_contraints = _horaires_contraints(propositions, candidats_par_prop)

    for prop in sorted(propositions, key=tri):
        code, statut, motif = None, STATUT_REJETE, None
        cle_matiere = (prop["eleve_id"], normaliser(prop["matiere"]))
        candidats = candidats_par_prop[prop["id"]]

        if not prop["eleve_actif"]:
            motif = "Eleve retire du referentiel"
        elif not candidats:
            motif = "Aucun creneau de %s n'est ouvert a cet eleve" % prop["matiere"]
        elif cle_matiere in retenu_par_matiere:
            # Deux enseignants de la meme matiere ont propose le meme eleve :
            # une seule inscription, la seconde proposition reste informative.
            motif = "Deja retenu en %s sur %s" % (
                prop["matiere"],
                retenu_par_matiere[cle_matiere],
            )
        elif (
            not premiere_periode
            and config.ROTATION_EXCLUT
            and prop["eleve_id"] in inscrits_precedents
        ):
            motif = "Eleve deja inscrit en AP a la periode precedente"
        else:
            libres = [
                c
                for c in candidats
                if occupation.get(c["code"], 0) < c["capacite"]
                and horaire_pris(prop["eleve_id"], c["horaire"]) is None
            ]
            if libres:
                # On laisse si possible leur horaire aux matieres qui n'ont
                # qu'un seul creneau possible pour cet eleve.
                reserves = horaires_contraints.get(cle_matiere, ())
                souples = [
                    c
                    for c in libres
                    if not any(
                        util.horaires_incompatibles(c["horaire"], h) for h in reserves
                    )
                ]
                # Repartition de la charge : le creneau le moins rempli d'abord,
                # puis l'ordre du classeur pour rester deterministe.
                choisi = min(
                    souples or libres,
                    key=lambda c: (occupation.get(c["code"], 0), c["ordre"]),
                )
                code, statut, motif = choisi["code"], STATUT_RETENU, None
                occupation[code] = occupation.get(code, 0) + 1
                horaires_retenus.setdefault(prop["eleve_id"], []).append(
                    (choisi["horaire"], code)
                )
                retenu_par_matiere[cle_matiere] = code
                inscrits.add(prop["eleve_id"])
            elif any(
                horaire_pris(prop["eleve_id"], c["horaire"]) for c in candidats
            ):
                bloquant = next(
                    horaire_pris(prop["eleve_id"], c["horaire"])
                    for c in candidats
                    if horaire_pris(prop["eleve_id"], c["horaire"])
                )
                motif = (
                    "Eleve deja pris par un AP le meme jour (%s)" % bloquant
                )
            else:
                motif = "Tous les creneaux de %s sont complets (%s)" % (
                    prop["matiere"],
                    ", ".join(c["code"] for c in candidats),
                )

        resultats.append((statut, code, motif, prop["id"]))

    conn.executemany(
        "UPDATE propositions SET statut = ?, creneau_code = ?, motif = ? WHERE id = ?",
        resultats,
    )
    return inscrits
