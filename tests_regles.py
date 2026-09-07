"""Tests des regles metier d'affectation.

Lancement :  python tests_regles.py
Les tests utilisent une base SQLite en memoire et un referentiel synthetique :
ils ne touchent ni AP.xlsx ni la base de production.
"""
import sqlite3
import unittest

import allocation
import db as dbmod
import util


def _memoire():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(dbmod.SCHEMA)
    return conn


class BaseRegles(unittest.TestCase):
    """Referentiel de test.

    Deux creneaux de francais a des horaires differents (AP-FR1 lundi,
    AP-FR2 mardi) pour tester la repartition, un creneau de maths au meme
    horaire que AP-FR1 pour tester les conflits, et un creneau de physique.
    Tous les eleves sont dans le groupe G1, sauf indication contraire.
    """

    CRENEAUX = [
        ("AP-FR1", "Francais", "Lundi 12h30", 15, 1),
        ("AP-FR2", "Francais", "Mardi 12h30", 15, 2),
        ("AP-MA1", "MATHEMATIQUES", "Lundi 12h30", 15, 3),
        ("AP-PC1", "PHYSIQUE CHIMIE", "Jeudi 12h30", 15, 4),
    ]

    def setUp(self):
        self.conn = _memoire()
        c = self.conn
        for i in range(1, 6):
            c.execute(
                "INSERT INTO periodes (id, nom, description, ordre) VALUES (?, ?, '', ?)",
                (i, "Periode %d" % i, i),
            )
        for code, matiere, horaire, cap, ordre in self.CRENEAUX:
            c.execute(
                """INSERT INTO creneaux (code, accompagnant_nom, accompagnant_prenom,
                                         matiere, horaire, capacite, ordre)
                   VALUES (?, 'ACC', 'A', ?, ?, ?, ?)""",
                (code, matiere, horaire, cap, ordre),
            )
            c.execute(
                "INSERT INTO creneau_groupes (creneau_code, groupe) VALUES (?, 'G1')",
                (code,),
            )
        c.execute(
            "INSERT INTO enseignants (id, nom, prenom, discipline)"
            " VALUES (1, 'PROF', 'UN', 'Francais')"
        )
        c.execute(
            "INSERT INTO enseignants (id, nom, prenom, discipline)"
            " VALUES (2, 'PROF', 'DEUX', 'Francais')"
        )
        c.execute(
            "INSERT INTO enseignants (id, nom, prenom, discipline)"
            " VALUES (3, 'PROF', 'TROIS', 'MATHEMATIQUES')"
        )
        for i in range(1, 61):
            c.execute(
                "INSERT INTO eleves (id, nom, prenom, classe) VALUES (?, ?, 'X', 'C1')",
                (i, "ELEVE%02d" % i),
            )
            c.execute(
                "INSERT INTO eleve_groupes (eleve_id, groupe) VALUES (?, 'G1')", (i,)
            )
        c.commit()

    def proposer(self, periode, matiere, eleve, instant, enseignant=1):
        self.conn.execute(
            """INSERT INTO propositions (periode_id, eleve_id, enseignant_id,
                                         matiere, groupe, created_at)
               VALUES (?, ?, ?, ?, 'G1', ?)""",
            (periode, eleve, enseignant, matiere, instant),
        )

    def resultat(self, periode, eleve, enseignant=1):
        row = self.conn.execute(
            """SELECT statut, creneau_code, motif FROM propositions
                WHERE periode_id = ? AND eleve_id = ? AND enseignant_id = ?""",
            (periode, eleve, enseignant),
        ).fetchone()
        return row["statut"], row["creneau_code"], row["motif"]

    def occupation(self, periode, creneau):
        return self.conn.execute(
            """SELECT COUNT(*) FROM propositions
                WHERE periode_id = ? AND creneau_code = ? AND statut = 'retenu'""",
            (periode, creneau),
        ).fetchone()[0]


class TestChoixDuCreneau(BaseRegles):
    def test_repartition_sur_le_creneau_le_moins_rempli(self):
        for i in range(1, 11):
            self.proposer(1, "Francais", i, "2025-09-01T10:%02d:00" % i)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.occupation(1, "AP-FR1"), 5)
        self.assertEqual(self.occupation(1, "AP-FR2"), 5)
        # Alternance stricte : 1er sur FR1, 2e sur FR2, 3e sur FR1...
        self.assertEqual(self.resultat(1, 1)[1], "AP-FR1")
        self.assertEqual(self.resultat(1, 2)[1], "AP-FR2")
        self.assertEqual(self.resultat(1, 3)[1], "AP-FR1")

    def test_deborde_sur_le_second_creneau_quand_le_premier_est_plein(self):
        # 25 eleves pour 2 x 15 places : tout le monde passe, repartition 13/12.
        for i in range(1, 26):
            self.proposer(1, "Francais", i, "2025-09-01T10:%02d:00" % i)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.occupation(1, "AP-FR1"), 13)
        self.assertEqual(self.occupation(1, "AP-FR2"), 12)

    def test_creneaux_tous_complets(self):
        for i in range(1, 32):
            self.proposer(1, "Francais", i, "2025-09-01T10:%02d:00" % i)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.occupation(1, "AP-FR1"), 15)
        self.assertEqual(self.occupation(1, "AP-FR2"), 15)
        statut, code, motif = self.resultat(1, 31)
        self.assertEqual(statut, "rejete")
        self.assertIsNone(code)
        self.assertIn("complets", motif)

    def test_matiere_sans_creneau_ouvert(self):
        self.proposer(1, "PHYSIQUE CHIMIE", 1, "2025-09-01T08:00:00")
        self.conn.execute("DELETE FROM creneau_groupes WHERE creneau_code = 'AP-PC1'")
        allocation.recompute_all(self.conn)
        statut, code, motif = self.resultat(1, 1)
        self.assertEqual(statut, "rejete")
        self.assertIn("Aucun creneau", motif)

    def test_une_seule_inscription_par_matiere(self):
        # Deux professeurs de francais proposent le meme eleve.
        self.proposer(1, "Francais", 1, "2025-09-01T08:00:00", enseignant=1)
        self.proposer(1, "Francais", 1, "2025-09-01T09:00:00", enseignant=2)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 1, 1)[0], "retenu")
        statut, code, motif = self.resultat(1, 1, 2)
        self.assertEqual(statut, "rejete")
        self.assertIn("Deja retenu", motif)
        self.assertEqual(self.occupation(1, "AP-FR1") + self.occupation(1, "AP-FR2"), 1)

    def test_deux_matieres_differentes_cumulables(self):
        self.proposer(1, "Francais", 1, "2025-09-01T08:00:00", enseignant=1)
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T09:00:00", enseignant=3)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 1, 1)[0], "retenu")
        self.assertEqual(self.resultat(1, 1, 3)[0], "retenu")


class TestConflitsHoraires(BaseRegles):
    def test_le_moteur_evite_l_horaire_deja_pris(self):
        # Maths d'abord (lundi), puis francais : le moteur doit choisir AP-FR2
        # (mardi) plutot que AP-FR1 (lundi, meme horaire que les maths).
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T08:00:00", enseignant=3)
        self.proposer(1, "Francais", 1, "2025-09-01T09:00:00", enseignant=1)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 1, 3)[1], "AP-MA1")
        self.assertEqual(self.resultat(1, 1, 1)[1], "AP-FR2")

    def test_rejet_quand_aucun_horaire_libre(self):
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T08:00:00", enseignant=3)
        self.proposer(1, "Francais", 1, "2025-09-01T09:00:00", enseignant=1)
        allocation.recompute_all(self.conn)
        statut, code, motif = self.resultat(1, 1, 1)
        self.assertEqual(statut, "rejete")
        self.assertIn("le meme jour", motif)

    def _creneau(self, code, matiere, horaire, ordre=9):
        self.conn.execute(
            """INSERT INTO creneaux (code, accompagnant_nom, accompagnant_prenom,
                                     matiere, horaire, capacite, ordre)
               VALUES (?, 'ACC', 'A', ?, ?, 15, ?)""",
            (code, matiere, horaire, ordre),
        )
        self.conn.execute(
            "INSERT INTO creneau_groupes (creneau_code, groupe) VALUES (?, 'G1')",
            (code,),
        )

    def test_horaires_differents_le_meme_jour_bloques(self):
        # AP-MA2 (12h05-13h00) et AP-FR1 (12h30-13h25) tombent tous deux un
        # lundi : un eleve ne suit qu'un AP par jour, le second est rejete.
        self.conn.execute("DELETE FROM creneaux WHERE code IN ('AP-MA1', 'AP-FR2')")
        self._creneau("AP-MA2", "MATHEMATIQUES", "Lundi 12h05-13h00")
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T08:00:00", enseignant=3)
        self.proposer(1, "Francais", 1, "2025-09-01T09:00:00", enseignant=1)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 1, 3)[1], "AP-MA2")
        statut, code, motif = self.resultat(1, 1, 1)
        self.assertEqual(statut, "rejete")
        self.assertIn("le meme jour", motif)

    def test_matinee_et_midi_le_meme_jour_bloques(self):
        # Deux creneaux du lundi tres eloignes : la regle porte sur le jour,
        # pas sur un chevauchement d'horaires.
        self.conn.execute("DELETE FROM creneaux WHERE code IN ('AP-MA1', 'AP-FR2')")
        self._creneau("AP-MA2", "MATHEMATIQUES", "Lundi 08h00-08h55")
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T08:00:00", enseignant=3)
        self.proposer(1, "Francais", 1, "2025-09-01T09:00:00", enseignant=1)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 1, 3)[1], "AP-MA2")
        self.assertEqual(self.resultat(1, 1, 1)[0], "rejete")

    def test_trois_ap_sur_une_periode(self):
        # Trois matieres sur trois jours differents : rien ne plafonne le
        # nombre d'AP d'un eleve, seules les matieres et les jours comptent.
        self.conn.execute(
            "INSERT INTO enseignants (id, nom, prenom, discipline)"
            " VALUES (4, 'PROF', 'QUATRE', 'PHYSIQUE CHIMIE')"
        )
        self.proposer(1, "Francais", 1, "2025-09-01T08:00:00", enseignant=1)
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T09:00:00", enseignant=3)
        self.proposer(1, "PHYSIQUE CHIMIE", 1, "2025-09-01T10:00:00", enseignant=4)
        allocation.recompute_all(self.conn)
        codes = [self.resultat(1, 1, e)[1] for e in (1, 3, 4)]
        self.assertEqual(codes, ["AP-FR2", "AP-MA1", "AP-PC1"])
        self.assertEqual(len(set(codes)), 3)

    def test_premier_arrive_premier_servi_en_periode_1(self):
        # En periode 1 le francais n'a aucune priorite : les maths, proposees
        # avant, prennent l'horaire du lundi.
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        self.proposer(1, "MATHEMATIQUES", 1, "2025-09-01T08:00:00", enseignant=3)
        self.proposer(1, "Francais", 1, "2025-09-01T09:00:00", enseignant=1)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 1, 3)[0], "retenu")
        self.assertEqual(self.resultat(1, 1, 1)[0], "rejete")


class TestHoraires(unittest.TestCase):
    """Lecture du jour dans les horaires du classeur : un AP par jour."""

    def test_jour_lu_dans_le_libelle(self):
        self.assertEqual(util.jour_du_creneau("Lundi 12h05-13h00"), "lundi")
        self.assertEqual(util.jour_du_creneau("MARDI 12h30"), "mardi")
        self.assertEqual(util.jour_du_creneau("Jeudi"), "jeudi")

    def test_libelle_sans_jour(self):
        self.assertIsNone(util.jour_du_creneau("12h30-13h25"))
        self.assertIsNone(util.jour_du_creneau(""))

    def test_meme_jour_incompatible_quels_que_soient_les_horaires(self):
        self.assertTrue(
            util.horaires_incompatibles("Lundi 08h00-08h55", "Lundi 12h30-13h25")
        )
        self.assertTrue(
            util.horaires_incompatibles("Lundi 12h05-13h00", "Lundi 12h30-13h25")
        )

    def test_jours_differents_compatibles(self):
        self.assertFalse(
            util.horaires_incompatibles("Lundi 12h05-13h00", "Mardi 12h30-13h25")
        )

    def test_repli_sur_l_egalite_quand_le_jour_est_illisible(self):
        self.assertTrue(util.horaires_incompatibles("12h30", "12h30"))
        self.assertFalse(util.horaires_incompatibles("12h30", "14h00"))


class TestPeriodesSuivantes(BaseRegles):
    def test_francais_prioritaire(self):
        # Meme situation qu'en periode 1, mais a partir de la periode 2 le
        # francais l'emporte bien qu'il ait ete propose apres.
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        self.proposer(2, "MATHEMATIQUES", 1, "2025-11-01T08:00:00", enseignant=3)
        self.proposer(2, "Francais", 1, "2025-11-01T09:00:00", enseignant=1)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(2, 1, 1)[0], "retenu")
        self.assertEqual(self.resultat(2, 1, 3)[0], "rejete")

    def test_priorite_aux_non_inscrits_periode_precedente(self):
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        self.conn.execute("UPDATE creneaux SET capacite = 1 WHERE code = 'AP-FR1'")
        self.proposer(1, "Francais", 1, "2025-09-01T08:00:00")
        self.proposer(2, "Francais", 1, "2025-11-01T08:00:00")
        self.proposer(2, "Francais", 2, "2025-11-01T09:00:00")
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(2, 2)[0], "retenu")
        self.assertEqual(self.resultat(2, 1)[0], "rejete")

    def test_egalite_retour_au_premier_arrive(self):
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        self.conn.execute("UPDATE creneaux SET capacite = 1 WHERE code = 'AP-FR1'")
        self.proposer(2, "Francais", 1, "2025-11-01T08:00:00")
        self.proposer(2, "Francais", 2, "2025-11-01T09:00:00")
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(2, 1)[0], "retenu")
        self.assertEqual(self.resultat(2, 2)[0], "rejete")

    def test_cascade_entre_periodes(self):
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        self.conn.execute("UPDATE creneaux SET capacite = 1 WHERE code = 'AP-FR1'")
        self.proposer(2, "Francais", 1, "2025-11-01T08:00:00")
        self.proposer(3, "Francais", 1, "2026-01-01T08:00:00")
        self.proposer(3, "Francais", 2, "2026-01-01T09:00:00")
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(2, 1)[0], "retenu")
        self.assertEqual(self.resultat(3, 2)[0], "retenu")
        self.assertEqual(self.resultat(3, 1)[0], "rejete")

    def test_retrait_dune_proposition_libere_la_place(self):
        self.conn.execute("DELETE FROM creneaux WHERE code = 'AP-FR2'")
        for i in range(1, 17):
            self.proposer(1, "Francais", i, "2025-09-01T10:%02d:00" % i)
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 16)[0], "rejete")
        self.conn.execute("DELETE FROM propositions WHERE eleve_id = 1")
        allocation.recompute_all(self.conn)
        self.assertEqual(self.resultat(1, 16)[0], "retenu")


class TestDiscipline(unittest.TestCase):
    """Un enseignant ne propose que dans sa propre discipline."""

    def test_comparaison_insensible_casse_et_accents(self):
        self.assertTrue(util.meme_discipline("Français", "FRANCAIS"))
        self.assertTrue(util.meme_discipline("PHYSIQUE CHIMIE", "physique chimie"))
        self.assertFalse(util.meme_discipline("MATHEMATIQUES", "Français"))

    def test_discipline_vide_ne_bloque_pas(self):
        self.assertTrue(util.meme_discipline(None, "Français"))

    def test_creneaux_eligibles_filtres_par_discipline(self):
        import queries

        conn = _memoire()
        conn.execute(
            """INSERT INTO creneaux (code, accompagnant_nom, matiere, horaire,
                                     capacite, ordre)
               VALUES ('AP-FR1', 'A', 'Français', 'Lundi', 15, 1)"""
        )
        conn.execute(
            """INSERT INTO creneaux (code, accompagnant_nom, matiere, horaire,
                                     capacite, ordre)
               VALUES ('AP-MA1', 'A', 'MATHEMATIQUES', 'Lundi', 15, 2)"""
        )
        for code in ("AP-FR1", "AP-MA1"):
            conn.execute(
                "INSERT INTO creneau_groupes (creneau_code, groupe) VALUES (?, 'G1')",
                (code,),
            )
        conn.execute("INSERT INTO eleves (id, nom, prenom, classe) VALUES (1, 'A', 'B', 'C1')")
        conn.execute("INSERT INTO eleve_groupes (eleve_id, groupe) VALUES (1, 'G1')")

        tous = {c["code"] for c in queries.creneaux_eligibles(conn, 1)}
        self.assertEqual(tous, {"AP-FR1", "AP-MA1"})
        maths = {c["code"] for c in queries.creneaux_eligibles(conn, 1, "MATHEMATIQUES")}
        self.assertEqual(maths, {"AP-MA1"})


class TestDonneesReelles(unittest.TestCase):
    """Verifie la regle de rattachement sur la base issue de AP.xlsx."""

    def test_eligibilite_par_classe(self):
        import os

        import config
        import queries

        if not os.path.exists(config.DATABASE):
            self.skipTest("base de production absente")
        conn = dbmod.connect()
        eleve = conn.execute(
            "SELECT id FROM eleves WHERE classe = '1STMG' LIMIT 1"
        ).fetchone()
        if eleve is None:
            self.skipTest("aucun eleve de 1STMG")
        # AP7 rattache ses eleves par classe et non par groupe de barrette.
        eligibles = {c["code"] for c in queries.creneaux_eligibles(conn, eleve["id"])}
        self.assertIn("AP7", eligibles)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
