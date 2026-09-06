"""Acces base de donnees (SQLite) et schema."""
import os
import sqlite3

from flask import g

import config

SCHEMA = """
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------------
-- Referentiels : alimentes par l'import Excel, jamais saisis dans l'app
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS periodes (
    id          INTEGER PRIMARY KEY,
    nom         TEXT NOT NULL UNIQUE,
    description TEXT,
    ordre       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS creneaux (
    code                TEXT PRIMARY KEY,           -- AP1, AP2, ...
    accompagnant_nom    TEXT NOT NULL,
    accompagnant_prenom TEXT,
    matiere             TEXT,
    horaire             TEXT NOT NULL,              -- "Lundi 12h30-13h25"
    capacite            INTEGER NOT NULL,
    ordre               INTEGER NOT NULL,
    actif               INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS creneau_groupes (
    creneau_code TEXT NOT NULL REFERENCES creneaux(code) ON DELETE CASCADE,
    groupe       TEXT NOT NULL,
    PRIMARY KEY (creneau_code, groupe)
);

CREATE TABLE IF NOT EXISTS enseignants (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    nom    TEXT NOT NULL,
    prenom TEXT,
    discipline TEXT,
    actif  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (nom, prenom)
);

CREATE TABLE IF NOT EXISTS enseignant_groupes (
    enseignant_id INTEGER NOT NULL REFERENCES enseignants(id) ON DELETE CASCADE,
    groupe        TEXT NOT NULL,
    PRIMARY KEY (enseignant_id, groupe)
);

CREATE TABLE IF NOT EXISTS eleves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nom             TEXT NOT NULL,
    prenom          TEXT NOT NULL,
    date_naissance  TEXT,
    sexe            TEXT,
    classe          TEXT,
    prof_principal  TEXT,
    actif           INTEGER NOT NULL DEFAULT 1,
    UNIQUE (nom, prenom)
);

CREATE TABLE IF NOT EXISTS eleve_groupes (
    eleve_id INTEGER NOT NULL REFERENCES eleves(id) ON DELETE CASCADE,
    groupe   TEXT NOT NULL,
    PRIMARY KEY (eleve_id, groupe)
);

-- ------------------------------------------------------------------
-- Donnees saisies dans l'application : jamais ecrasees par un re-import
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS propositions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    periode_id    INTEGER NOT NULL REFERENCES periodes(id),
    eleve_id      INTEGER NOT NULL REFERENCES eleves(id),
    enseignant_id INTEGER NOT NULL REFERENCES enseignants(id),
    matiere       TEXT    NOT NULL,          -- discipline de l'enseignant proposant
    groupe        TEXT,                      -- groupe au titre duquel l'eleve est propose
    created_at    TEXT    NOT NULL,          -- horodatage UTC, base du 1er arrive 1er servi
    creneau_code  TEXT REFERENCES creneaux(code),  -- affecte par le moteur, NULL si non retenu
    statut        TEXT    NOT NULL DEFAULT 'en_attente',  -- retenu | rejete | en_attente
    motif         TEXT,                      -- motif de rejet, affiche au proposant
    UNIQUE (periode_id, eleve_id, enseignant_id, matiere)
);

CREATE INDEX IF NOT EXISTS idx_prop_periode  ON propositions(periode_id);
CREATE INDEX IF NOT EXISTS idx_prop_creneau  ON propositions(periode_id, creneau_code);
CREATE INDEX IF NOT EXISTS idx_prop_eleve    ON propositions(periode_id, eleve_id);

-- Journal des imports, pour tracabilite.
CREATE TABLE IF NOT EXISTS imports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    fichier    TEXT,
    resume     TEXT
);
"""


def connect(path=None):
    """Ouvre une connexion SQLite configuree (hors contexte Flask possible)."""
    path = path or config.DATABASE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Connexion liee a la requete Flask courante."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(conn=None):
    """Cree le schema s'il n'existe pas et migre l'ancien. Idempotent."""
    own = conn is None
    conn = conn or connect()
    _avant_schema(conn)
    conn.executescript(SCHEMA)
    _apres_schema(conn)
    conn.commit()
    if own:
        conn.close()


def _colonnes(conn, table):
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def _avant_schema(conn):
    """Migration v1 -> v2 : la proposition porte desormais une matiere, et le
    creneau est choisi par le moteur (colonne nullable) au lieu d'etre choisi
    par l'enseignant. L'ancienne table est mise de cote pour etre recopiee."""
    colonnes = _colonnes(conn, "propositions")
    if colonnes and "matiere" not in colonnes:
        conn.execute("DROP TABLE IF EXISTS propositions_v1")
        conn.execute("ALTER TABLE propositions RENAME TO propositions_v1")
        for index in ("idx_prop_periode", "idx_prop_creneau", "idx_prop_eleve"):
            conn.execute("DROP INDEX IF EXISTS %s" % index)


def _apres_schema(conn):
    """Recopie les propositions de l'ancienne table, si elle existe.

    La matiere est deduite du creneau qui avait ete choisi par l'enseignant.
    Le creneau lui-meme est efface : il sera reattribue par le moteur.
    """
    existe = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'propositions_v1'"
    ).fetchone()
    if not existe:
        return
    conn.execute(
        """INSERT OR IGNORE INTO propositions
               (periode_id, eleve_id, enseignant_id, matiere, groupe, created_at)
           SELECT v.periode_id, v.eleve_id, v.enseignant_id,
                  IFNULL(c.matiere, 'INCONNUE'), v.groupe, v.created_at
             FROM propositions_v1 v
             LEFT JOIN creneaux c ON c.code = v.creneau_code"""
    )
    conn.execute("DROP TABLE propositions_v1")


def init_app(app):
    app.teardown_appcontext(close_db)
