"""Import du classeur Excel (4 onglets) vers la base SQLite.

Le classeur est la source de verite pour les referentiels
(periodes, creneaux, enseignants, eleves). Les propositions saisies dans
l'application ne sont jamais touchees : les lignes de referentiel disparues
du classeur sont seulement desactivees (actif = 0), jamais supprimees.

Utilisation en ligne de commande :
    python importer.py            # importe le fichier defini dans config.py
    python importer.py autre.xlsx
"""
import datetime as dt
import sys

import openpyxl

import config
import db as dbmod
from util import normaliser

ONGLETS = ("PERIODE", "CRENEAUX", "ENSEIGNANTS", "ELEVES")


def clean(value):
    """Nettoie une cellule : strip systematique, None si vide.

    Les codes de groupes du classeur comportent des espaces parasites en tete
    (ex. " PPH-CHGR1_RB") qui casseraient les jointures.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip() or None


def _rows(ws):
    return list(ws.iter_rows(values_only=True))


def _entetes(row):
    """Index de chaque en-tete, normalise (casse, accents, espaces)."""
    return {normaliser(cell): i for i, cell in enumerate(row) if cell}


def _index(entetes, noms, defaut=None):
    """Position de la premiere colonne trouvee parmi `noms`.

    Les colonnes sont reperees par leur intitule et non par leur rang : ajouter
    ou supprimer une colonne dans le classeur (la date de naissance, par
    exemple) ne casse donc pas l'import. `defaut` sert de secours si aucun
    intitule ne correspond.
    """
    for nom in noms:
        if normaliser(nom) in entetes:
            return entetes[normaliser(nom)]
    return defaut


def _valeur(row, index):
    if index is None or index >= len(row):
        return None
    return clean(row[index])


def _tail_values(row, start):
    """Valeurs nettoyees et dedoublonnees a partir de la colonne `start`.

    Le nombre de colonnes de groupes n'est pas fige : tout ce qui suit est lu.
    """
    if start is None:
        return []
    out = []
    for cell in row[start:]:
        val = clean(cell)
        if val and val not in out:
            out.append(val)
    return out


def import_periodes(conn, ws):
    """Onglet PERIODE : 2 colonnes, sans en-tete."""
    n = 0
    for i, row in enumerate(_rows(ws), start=1):
        nom = clean(row[0] if len(row) > 0 else None)
        if not nom:
            continue
        desc = clean(row[1] if len(row) > 1 else None)
        conn.execute(
            """INSERT INTO periodes (nom, description, ordre) VALUES (?, ?, ?)
               ON CONFLICT(nom) DO UPDATE SET description = excluded.description,
                                              ordre = excluded.ordre""",
            (nom, desc, i),
        )
        n += 1
    return n


def import_creneaux(conn, ws):
    """Onglet CRENEAUX : AP | accompagnant | Colonne1 | MATIERE | Creneau | Eleves..."""
    conn.execute("UPDATE creneaux SET actif = 0")
    lignes = _rows(ws)
    entetes = _entetes(lignes[0]) if lignes else {}
    i_code = _index(entetes, ["AP"], 0)
    i_nom = _index(entetes, ["accompagnant"], 1)
    i_prenom = _index(entetes, ["Colonne1", "prenom"], 2)
    i_matiere = _index(entetes, ["MATIERE"], 3)
    i_horaire = _index(entetes, ["Creneau", "Horaire"], 4)
    i_groupes = _index(entetes, ["Eleves"], 5)

    n = 0
    for i, row in enumerate(lignes[1:], start=1):
        code = _valeur(row, i_code)
        horaire = _valeur(row, i_horaire)
        if not code or not horaire:
            continue
        conn.execute(
            """INSERT INTO creneaux (code, accompagnant_nom, accompagnant_prenom,
                                     matiere, horaire, capacite, ordre, actif)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(code) DO UPDATE SET
                   accompagnant_nom    = excluded.accompagnant_nom,
                   accompagnant_prenom = excluded.accompagnant_prenom,
                   matiere             = excluded.matiere,
                   horaire             = excluded.horaire,
                   ordre               = excluded.ordre,
                   actif               = 1""",
            (
                code,
                _valeur(row, i_nom) or "?",
                _valeur(row, i_prenom),
                _valeur(row, i_matiere),
                horaire,
                config.CAPACITE_DEFAUT,
                i,
            ),
        )
        # Les groupes eligibles sont entierement redefinis a chaque import.
        conn.execute("DELETE FROM creneau_groupes WHERE creneau_code = ?", (code,))
        for groupe in _tail_values(row, i_groupes):
            conn.execute(
                "INSERT OR IGNORE INTO creneau_groupes (creneau_code, groupe) VALUES (?, ?)",
                (code, groupe),
            )
        n += 1
    return n


def import_enseignants(conn, ws):
    """Onglet ENSEIGNANTS : Nom | Prenom | Discipline | ELEVES | ELEVES2 | ..."""
    conn.execute("UPDATE enseignants SET actif = 0")
    lignes = _rows(ws)
    entetes = _entetes(lignes[0]) if lignes else {}
    i_nom = _index(entetes, ["Nom"], 0)
    i_prenom = _index(entetes, ["Prenom"], 1)
    i_discipline = _index(entetes, ["Discipline"], 2)
    i_groupes = _index(entetes, ["ELEVES"], 3)

    n = 0
    for row in lignes[1:]:
        nom = _valeur(row, i_nom)
        if not nom:
            continue
        prenom = _valeur(row, i_prenom)
        conn.execute(
            """INSERT INTO enseignants (nom, prenom, discipline, actif) VALUES (?, ?, ?, 1)
               ON CONFLICT(nom, prenom) DO UPDATE SET discipline = excluded.discipline,
                                                      actif = 1""",
            (nom, prenom, _valeur(row, i_discipline)),
        )
        ens_id = conn.execute(
            "SELECT id FROM enseignants WHERE nom = ? AND prenom IS ?", (nom, prenom)
        ).fetchone()["id"]
        conn.execute("DELETE FROM enseignant_groupes WHERE enseignant_id = ?", (ens_id,))
        for groupe in _tail_values(row, i_groupes):
            conn.execute(
                "INSERT OR IGNORE INTO enseignant_groupes (enseignant_id, groupe) VALUES (?, ?)",
                (ens_id, groupe),
            )
        n += 1
    return n


def import_eleves(conn, ws):
    """Onglet ELEVES : Nom | Prenom | Sexe | Classe | PP | Groupes...

    Les colonnes sont reperees par leur intitule : "Ne(e) le" et "Sexe" sont
    facultatives et peuvent etre retirees du classeur sans rien casser.
    Toutes les colonnes situees apres "Groupes / Parties" sont lues comme des
    groupes supplementaires. La ligne 2 du classeur est vide : elle est
    ignoree comme toute ligne sans nom.
    """
    conn.execute("UPDATE eleves SET actif = 0")
    lignes = _rows(ws)
    entetes = _entetes(lignes[0]) if lignes else {}
    i_nom = _index(entetes, ["Nom"], 0)
    i_prenom = _index(entetes, ["Prenom"], 1)
    i_naissance = _index(entetes, ["Ne(e) le", "Date de naissance"])
    i_sexe = _index(entetes, ["Sexe"])
    i_classe = _index(entetes, ["Classe"], 4)
    i_pp = _index(entetes, ["Professeur Principal"], 5)
    i_groupes = _index(entetes, ["Groupes / Parties", "Groupes"], 6)

    n = 0
    for row in lignes[1:]:
        nom = _valeur(row, i_nom)
        prenom = _valeur(row, i_prenom)
        if not nom or not prenom:
            continue
        conn.execute(
            """INSERT INTO eleves (nom, prenom, date_naissance, sexe, classe,
                                   prof_principal, actif)
               VALUES (?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(nom, prenom) DO UPDATE SET
                   date_naissance = excluded.date_naissance,
                   sexe           = excluded.sexe,
                   classe         = excluded.classe,
                   prof_principal = excluded.prof_principal,
                   actif          = 1""",
            (
                nom,
                prenom,
                _valeur(row, i_naissance),
                _valeur(row, i_sexe),
                _valeur(row, i_classe),
                _valeur(row, i_pp),
            ),
        )
        eleve_id = conn.execute(
            "SELECT id FROM eleves WHERE nom = ? AND prenom = ?", (nom, prenom)
        ).fetchone()["id"]
        conn.execute("DELETE FROM eleve_groupes WHERE eleve_id = ?", (eleve_id,))
        for groupe in _tail_values(row, i_groupes):
            conn.execute(
                "INSERT OR IGNORE INTO eleve_groupes (eleve_id, groupe) VALUES (?, ?)",
                (eleve_id, groupe),
            )
        n += 1
    return n


def import_excel(path=None, conn=None):
    """Importe les 4 onglets. Retourne un dict de comptages."""
    path = path or config.chemin_excel()
    own = conn is None
    conn = conn or dbmod.connect()
    dbmod.init_db(conn)

    wb = openpyxl.load_workbook(path, data_only=True)
    manquants = [o for o in ONGLETS if o not in wb.sheetnames]
    if manquants:
        raise ValueError("Onglet(s) absent(s) du classeur : " + ", ".join(manquants))

    resume = {
        "periodes": import_periodes(conn, wb["PERIODE"]),
        "creneaux": import_creneaux(conn, wb["CRENEAUX"]),
        "enseignants": import_enseignants(conn, wb["ENSEIGNANTS"]),
        "eleves": import_eleves(conn, wb["ELEVES"]),
    }
    conn.execute(
        "INSERT INTO imports (created_at, fichier, resume) VALUES (?, ?, ?)",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            str(path),
            str(resume),
        ),
    )
    conn.commit()
    if own:
        conn.close()
    return resume


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else None
    print(import_excel(chemin))
