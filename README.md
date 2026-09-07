# Accompagnement Personnalisé (AP) — Lycée Stanislas, Cannes

Application web de gestion des propositions et inscriptions en AP.
Stack volontairement minimale : **Python + Flask + SQLite**, aucun ORM,
aucun service externe. Un seul fichier de base (`instance/ap.sqlite3`).

---

## 1. Installation locale

```bash
pip install -r requirements.txt
python importer.py          # crée la base et importe AP.xlsx
python app.py               # http://localhost:5000
```

Mot de passe par défaut : `stanislas` (à changer, voir §4).

## 2. Ce que fait l'application

Après saisie du mot de passe partagé, l'utilisateur choisit une **période**
puis un **rôle** :

| Rôle | Écran | Contenu |
|---|---|---|
| Enseignant proposant | `/enseignant` | ses groupes, ses élèves, une case à cocher par élève, statut de chaque proposition et créneau attribué |
| Professeur accompagnant | `/accompagnant` | pour chacun de ses créneaux : élèves retenus, groupe concerné, enseignant proposant (consultation seule) |
| Prof principal / direction | `/recap` | une ligne par élève, une colonne par période, AP suivi |
| Administration | `/admin` | ré-import du classeur, liste des créneaux, journal des imports |

Aucun e-mail n'est envoyé : tout est consultable en ligne.

## 3. Règles d'affectation

Le moteur (`allocation.py`) est **déterministe et recalculé intégralement**
à chaque modification : ajouter ou retirer une proposition reclasse
automatiquement tous les élèves concernés.

**Qui propose quoi** — un enseignant ne propose que dans **sa discipline** :
la colonne `Discipline` de l'onglet ENSEIGNANTS est comparée à la colonne
`MATIERE` de l'onglet CRENEAUX (comparaison insensible à la casse et aux
accents). Une discipline vide dans le classeur n'est pas bloquante : elle
ouvre tous les créneaux — c'est le classeur qu'il faut alors corriger.

**Choix du créneau** — l'enseignant coche un élève, il ne choisit pas le
jour. Le moteur retient, parmi les créneaux de la matière ouverts à cet
élève :

- le **moins rempli** (à égalité, le premier dans l'ordre du classeur) ;
- en écartant les créneaux complets et ceux dont l'horaire **chevauche**
  celui d'un AP déjà retenu pour l'élève dans une autre matière. Les horaires
  sont comparés comme des intervalles : *Lundi 12h05‑13h00* et
  *Lundi 12h30‑13h25* se recouvrent de 30 minutes et sont donc incompatibles,
  bien que leurs libellés diffèrent ;
- en évitant, tant qu'une alternative existe, un horaire dont une autre
  matière proposée pour le même élève a besoin parce qu'elle n'a qu'un seul
  créneau possible. Sans cette précaution, le français (3 créneaux) servi
  en premier bloquerait les maths (1 seul créneau) au même horaire.

Un élève n'est inscrit qu'une fois par matière et par période : si deux
enseignants de la même matière le proposent, la seconde proposition est
signalée comme doublon, sans consommer de place.

**Période 1** — 15 places par créneau, premier arrivé premier servi
(horodatage de la proposition). Un élève retenu sur un créneau ne peut pas
l'être sur un créneau qui le chevauche : seule la proposition la plus
ancienne passe, l'autre est rejetée avec le motif affiché au proposant.

Quand le classeur ne donne qu'une heure de début, une durée de 55 min est
supposée (`util.DUREE_AP_MINUTES`). Un libellé d'horaire illisible (jour ou
heure absents) retombe sur une comparaison de texte : mieux vaut manquer un
conflit que d'en inventer un.

**Périodes 2 à 5** — ordre de priorité :
1. le français l'emporte sur les autres matières ;
2. puis les élèves non inscrits en AP à la période précédente ;
3. puis l'horodatage.

Les périodes sont calculées en cascade (la période N dépend du résultat
de la période N‑1).

> **À confirmer avec la direction avant mise en production.**
> La règle 2 ne fait que **déprioriser** un élève déjà inscrit à la période
> précédente ; il reste éligible s'il reste des places. Pour l'**exclure**
> totalement, passer la variable d'environnement `AP_ROTATION_EXCLUT=1`
> (ou `ROTATION_EXCLUT = True` dans `config.py`).

Les règles sont couvertes par des tests :

```bash
python tests_regles.py
```

## 4. Changer le mot de passe partagé

Le mot de passe est lu dans la variable d'environnement `AP_PASSWORD`.

- **En production** : définir `AP_PASSWORD` dans la configuration de
  l'hébergeur (Render, Railway, PythonAnywhere…), puis redémarrer l'app.
- **En local / à défaut** : modifier la valeur par défaut dans `config.py`
  (`APP_PASSWORD = os.environ.get("AP_PASSWORD", "stanislas")`).

Définir aussi `AP_SECRET_KEY` (chaîne aléatoire, ~40 caractères) en
production : elle signe les cookies de session. Changer cette clé
déconnecte tout le monde, ce qui est le moyen le plus simple de forcer une
reconnexion après un changement de mot de passe.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 5. Ré-importer un Excel mis à jour

Deux possibilités, équivalentes :

- depuis l'application : page **Administration** → choisir le fichier →
  *Importer*. C'est la voie normale en production : le classeur envoyé est
  stocké dans le dossier de données et remplace le précédent. Sans fichier
  joint, le dernier classeur envoyé est simplement relu ;
- en ligne de commande : `python importer.py` (ou `python importer.py chemin/AP.xlsx`).

Ce que fait l'import :

- il met à jour périodes, créneaux, enseignants, élèves et leurs groupes ;
- il applique un `.strip()` sur **tous** les codes de groupes (le classeur
  contient des espaces parasites du type `" PPH-CHGR1_RB"`) ;
- le nombre de colonnes de groupes n'est pas figé : toutes les colonnes
  après l'en-tête connu sont lues ;
- une ligne disparue du classeur est **désactivée** (`actif = 0`), jamais
  supprimée, pour ne pas casser les propositions existantes ;
- **les propositions déjà enregistrées ne sont jamais écrasées.**

Identités utilisées pour les rapprochements : `AP1…APn` pour les créneaux,
(`Nom`, `Prénom`) pour les enseignants et les élèves. Corriger un nom dans
le classeur crée donc une nouvelle fiche ; les propositions restent
attachées à l'ancienne.

## 6. Déploiement

L'application est un WSGI standard exposé sous `app:app`.

**Le classeur ne se déploie pas avec le code.** `AP.xlsx` est exclu du dépôt
(`.gitignore`) : une fois l'application en ligne, vous l'envoyez depuis la
page Administration. Les données élèves n'existent donc qu'à un seul
endroit sur le serveur, dans le dossier de données.

### PythonAnywhere (recommandé — gratuit, serveurs UE)

1. Créer un compte sur `eu.pythonanywhere.com` (offre *Beginner*, gratuite).
2. Onglet **Files** : créer le dossier `ap`, y envoyer tous les fichiers du
   projet **sauf** `AP.xlsx` et le dossier `instance/`. Créer aussi un
   dossier `ap-donnees` à la racine (il recevra la base et le classeur).
3. Onglet **Consoles** → *Bash* :
   ```bash
   pip3 install --user -r ap/requirements.txt
   ```
4. Onglet **Web** → *Add a new web app* → **Manual configuration** → Python 3.
5. Dans *WSGI configuration file*, coller le contenu de
   `wsgi_pythonanywhere.py` en remplaçant `COMPTE` par votre identifiant et
   les deux valeurs `A CHANGER` (mot de passe partagé, clé de session).
6. *Reload* l'application, puis ouvrir `https://COMPTE.eu.pythonanywhere.com`,
   aller sur **Administration** et envoyer `AP.xlsx`.

Mise à jour du code ensuite : renvoyer les fichiers modifiés puis *Reload*.

### Render / Railway (déploiement automatique depuis Git)

- Build : `pip install -r requirements.txt`
- Start : `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4`
- Variables : `AP_PASSWORD`, `AP_SECRET_KEY`, et `AP_DATABASE` pointant vers
  un **disque persistant** (ex. `/var/data/ap.sqlite3`). Sans disque
  persistant — le cas de l'offre gratuite de Render — la base **et** le
  classeur envoyé sont effacés à chaque redéploiement.
- Après le premier déploiement, ouvrir `/admin` et envoyer le classeur.

### Serveur de l'établissement

```bash
waitress-serve --host=0.0.0.0 --port=8000 app:app
```

Définir au préalable `AP_PASSWORD`, `AP_SECRET_KEY` et `AP_DATABASE`. C'est
l'option où les données ne quittent pas le lycée ; elle demande un poste
allumé en permanence et, pour un accès depuis l'extérieur, l'aide du service
informatique.

### Dans tous les cas

SQLite convient largement à 15–20 utilisateurs. Une seule précaution :
garder **un seul processus** avec plusieurs threads plutôt que plusieurs
processus concurrents en écriture (`--workers 1 --threads 4`).

Servir l'application en **HTTPS** : le mot de passe partagé circule dans le
formulaire de connexion. PythonAnywhere et Render le font par défaut ; sur
un serveur interne, il faut le configurer.

## 7. Structure du code

```
app.py            routes Flask, authentification, écrans
allocation.py     moteur d'affectation (règles de conflit)
queries.py        requêtes de lecture partagées
importer.py       import du classeur Excel -> SQLite
db.py             connexion, schéma SQL et migrations
util.py           comparaison des libellés (casse, accents)
config.py         paramètres (mot de passe, chemins, capacité, priorité)
tests_regles.py   tests des règles métier
templates/        pages Jinja2
static/style.css  feuille de style unique, mobile d'abord
```

Tables : `periodes`, `creneaux` (+ `creneau_groupes`), `enseignants`
(+ `enseignant_groupes`), `eleves` (+ `eleve_groupes`) pour les référentiels ;
`propositions` pour les données saisies ; `imports` pour la traçabilité.
Une proposition porte un élève, un enseignant, une **matière** et un
horodatage ; sa colonne `creneau_code` est un **résultat** écrit par le
moteur (`NULL` tant que l'élève n'est pas retenu).
La table `propositions` porte déjà `periode_id`, ce qui permet à l'écran
récapitulatif (rôle 3) de fonctionner sur toutes les périodes sans
changement de schéma.

## 8. Points d'attention sur les données actuelles

- **AP10** (`TPH-CHGR1_KD`, `TPH-CHGR2_SH`, `TPH-CHGR3_RB`) n'a **aucun élève
  éligible** : l'onglet `ELEVES` ne contient que des élèves de Première
  (groupes `P…`) et de 1STMG, aucun de Terminale. Il faut ajouter les élèves
  de Terminale au classeur pour que ce créneau soit utilisable.
- Trois créneaux partagent l'horaire *Lundi 12h30‑13h25* (AP2, AP3, AP7) et
  deux autres *Mardi* / *Vendredi* / *Jeudi 12h30‑13h25* : c'est là que se
  déclenchent les règles de conflit. **AP1** (*Lundi 12h05‑13h00*) recouvre
  ces trois créneaux du lundi de 30 minutes : le moteur les traite comme
  incompatibles.
- AP7 rattache ses élèves par **classe** (`1STMG`) et non par groupe de
  barrette ; les 22 élèves de 1STMG n'ont d'ailleurs aucun groupe renseigné.
- La capacité (15) est stockée par créneau en base : elle peut être ajustée
  au cas par cas en SQL si la direction le demande, sans toucher au code.
- **Minimisation des données** : les colonnes `Né(e) le` et `Sexe` ont été
  vidées du classeur ; l'outil ne s'en sert pas et n'affiche nulle part
  autre chose que nom, prénom, classe et groupes. Les colonnes de l'onglet
  ELEVES sont repérées par leur **intitulé** et non par leur rang : vous
  pouvez supprimer ou déplacer ces colonnes sans casser l'import.
