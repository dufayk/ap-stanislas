"""Point d'entree WSGI pour PythonAnywhere.

A recopier dans le fichier WSGI propose par PythonAnywhere
(onglet Web > "WSGI configuration file"), en remplacant COMPTE par votre
nom d'utilisateur et les deux valeurs marquees A CHANGER.

Ce fichier n'est pas lu en local : `python app.py` et waitress utilisent
directement app.py.
"""
import os
import sys

COMPTE = "COMPTE"  # <- votre identifiant PythonAnywhere

# 1. Rendre le code de l'application importable.
DOSSIER_CODE = "/home/%s/ap" % COMPTE
if DOSSIER_CODE not in sys.path:
    sys.path.insert(0, DOSSIER_CODE)

# 2. Configuration. Ces valeurs ne sortent pas de votre compte prive.
#    Le mot de passe partage : celui que vous communiquerez aux enseignants.
os.environ.setdefault("AP_PASSWORD", "A CHANGER")

#    La cle de signature des cookies de session. Une chaine aleatoire longue,
#    differente du mot de passe, que personne n'a besoin de connaitre.
os.environ.setdefault("AP_SECRET_KEY", "A CHANGER")

# 3. Base SQLite et classeur envoye : hors du dossier de code, pour survivre
#    aux mises a jour de l'application.
os.environ.setdefault("AP_DATABASE", "/home/%s/ap-donnees/ap.sqlite3" % COMPTE)

from app import app as application  # noqa: E402  (import apres la configuration)
