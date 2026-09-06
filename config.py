"""Configuration de l'application AP.

Toutes les valeurs sont surchargeables par variables d'environnement,
ce qui evite de modifier le code lors d'un deploiement.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Mot de passe partage de l'etablissement (voir README pour le changer).
APP_PASSWORD = os.environ.get("AP_PASSWORD", "stanislas")

# Cle de signature des cookies de session.
SECRET_KEY = os.environ.get("AP_SECRET_KEY", "dev-secret-a-changer-en-production")

# Base SQLite (fichier persistant).
DATABASE = os.environ.get("AP_DATABASE", os.path.join(BASE_DIR, "instance", "ap.sqlite3"))

# Dossier des donnees persistantes (base SQLite, classeur envoye).
INSTANCE_DIR = os.path.dirname(DATABASE)

# Classeur Excel servant de referentiel. Priorite :
#   1. la variable AP_EXCEL ;
#   2. le classeur envoye depuis la page Administration ;
#   3. le classeur pose a cote du code (usage local).
# Le classeur contient des donnees personnelles d'eleves : il n'est pas
# versionne et n'accompagne pas le code en production, il est envoye
# depuis l'application.
EXCEL_ENVOYE = os.path.join(INSTANCE_DIR, "AP.xlsx")


def chemin_excel():
    depuis_env = os.environ.get("AP_EXCEL")
    if depuis_env:
        return depuis_env
    if os.path.exists(EXCEL_ENVOYE):
        return EXCEL_ENVOYE
    return os.path.join(BASE_DIR, "AP.xlsx")


# Taille maximale acceptee pour le classeur envoye (10 Mo).
TAILLE_MAX_ENVOI = int(os.environ.get("AP_TAILLE_MAX", str(10 * 1024 * 1024)))

# Nombre de places par defaut sur un creneau AP.
CAPACITE_DEFAUT = int(os.environ.get("AP_CAPACITE", "15"))

# Matiere prioritaire a partir de la periode 2 (comparaison insensible a la casse).
MATIERE_PRIORITAIRE = os.environ.get("AP_MATIERE_PRIORITAIRE", "francais")

# Regle 2 (periodes >= 2) : True = un eleve inscrit a la periode precedente est
# seulement deprioritise ; False = il est totalement exclu.
# Point a confirmer avec la direction avant mise en production.
ROTATION_EXCLUT = os.environ.get("AP_ROTATION_EXCLUT", "0") == "1"
