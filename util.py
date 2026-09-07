"""Petites fonctions partagees."""
import re
import unicodedata


def normaliser(texte):
    """Minuscules sans accents ni espaces superflus.

    Sert a comparer des libelles saisis a la main dans le classeur :
    "Francais", "FRANCAIS" et "Français" doivent etre reconnus comme identiques.
    """
    if not texte:
        return ""
    decompose = unicodedata.normalize("NFD", str(texte).strip())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn").lower()


def meme_discipline(discipline, matiere):
    """Vrai si la discipline d'un enseignant correspond a la matiere d'un creneau.

    Une discipline vide dans le classeur n'est pas bloquante : l'enseignant
    garde alors acces a tous les creneaux (a corriger dans le classeur).
    """
    if not discipline:
        return True
    return normaliser(discipline) == normaliser(matiere)


# ------------------------------------------------------------------
# Horaires de creneaux
# ------------------------------------------------------------------
# Les horaires viennent du classeur sous forme de texte libre :
# "Lundi 12h05-13h00", parfois sans heure de fin. Deux creneaux d'un meme
# jour peuvent se chevaucher partiellement (12h05-13h00 et 12h30-13h25) :
# comparer les libelles ne suffit donc pas a detecter le conflit.

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")

# Duree supposee d'un AP quand le classeur ne donne que l'heure de debut.
DUREE_AP_MINUTES = 55

_RE_HEURE = re.compile(r"(\d{1,2})\s*[h:]\s*(\d{2})?")


def analyser_horaire(texte):
    """Decoupe "Lundi 12h05-13h00" en (jour, debut, fin), en minutes.

    Retourne None si le libelle n'est pas exploitable (jour ou heure absents) :
    l'appelant retombe alors sur une comparaison de texte.
    """
    norm = normaliser(texte)
    if not norm:
        return None
    jour = next((j for j in _JOURS if j in norm), None)
    if jour is None:
        return None
    heures = [
        int(h) * 60 + int(m or 0) for h, m in _RE_HEURE.findall(norm)
    ]
    if not heures:
        return None
    debut = heures[0]
    fin = heures[1] if len(heures) > 1 else debut + DUREE_AP_MINUTES
    if fin <= debut:
        # Heure de fin absurde ou identique au debut : on suppose la duree type.
        fin = debut + DUREE_AP_MINUTES
    return (jour, debut, fin)


def horaires_incompatibles(a, b):
    """Vrai si un eleve ne peut pas suivre ces deux creneaux.

    Deux creneaux du meme jour sont incompatibles des qu'ils se recouvrent,
    meme partiellement. Quand un libelle n'est pas analysable, on se rabat sur
    l'egalite de texte : mieux vaut manquer un conflit que d'en inventer un.
    """
    ha, hb = analyser_horaire(a), analyser_horaire(b)
    if ha is None or hb is None:
        return normaliser(a) == normaliser(b)
    if ha[0] != hb[0]:
        return False
    return ha[1] < hb[2] and hb[1] < ha[2]
