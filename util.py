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
