"""Petites fonctions partagees."""
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
# "Lundi 12h05-13h00". Un eleve ne suit qu'un seul AP par jour : seul le
# jour est donc compare, les heures n'entrent pas dans la regle.

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def jour_du_creneau(texte):
    """Jour d'un libelle d'horaire, en minuscules sans accent.

    Retourne None si aucun jour de la semaine n'y figure : l'appelant retombe
    alors sur une comparaison de texte.
    """
    norm = normaliser(texte)
    if not norm:
        return None
    return next((j for j in _JOURS if j in norm), None)


def horaires_incompatibles(a, b):
    """Vrai si un eleve ne peut pas suivre ces deux creneaux.

    Un eleve n'a droit qu'a un AP par jour : deux creneaux du meme jour sont
    incompatibles, qu'ils se chevauchent ou non. Quand le jour n'est pas
    lisible, on se rabat sur l'egalite de texte : mieux vaut manquer un
    conflit que d'en inventer un sur une saisie douteuse.
    """
    ja, jb = jour_du_creneau(a), jour_du_creneau(b)
    if ja is None or jb is None:
        return normaliser(a) == normaliser(b)
    return ja == jb
