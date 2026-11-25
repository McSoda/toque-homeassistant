# Toque pour Home Assistant

Intégration personnalisée (compatible [HACS](https://hacs.xyz)) pour **Toque**, l'application qui remplace celle du robot
**Lidl Monsieur Cuisine Connect (SKMC 1200)**. Toque est le projet de
[Wuki60](https://github.com/Wuki60/MrCuisineVibeConnect), qui **n'a pas d'API**. Cette intégration lit l'API locale
(HTTP + SSE, **lecture seule**) ajoutée par le fork
[McSoda/MrCuisineVibeConnect](https://github.com/McSoda/MrCuisineVibeConnect) : elle ne pilote ni le moteur, ni la
chauffe, ni la session de cuisson.

> **À lire d'abord : cette intégration ne fonctionne qu'avec le fork
> [McSoda/MrCuisineVibeConnect](https://github.com/McSoda/MrCuisineVibeConnect).**
> L'API locale pour Home Assistant (`/api/v1`, annonce mDNS `_toque._tcp`) y a été ajoutée ; elle n'existe pas dans le
> dépôt de Wuki60. Avec la version de Toque de Wuki60, l'intégration ne trouvera rien à lire. L'ajout de cette API a été
> proposé à Wuki60 ([pull request #66](https://github.com/Wuki60/MrCuisineVibeConnect/pull/66)), fermée sans fusion.
> L'API est décrite dans
> [`docs/HOME_ASSISTANT_API.md`](https://github.com/McSoda/MrCuisineVibeConnect/blob/main/docs/HOME_ASSISTANT_API.md) du
> fork.

*English: custom Home Assistant integration for the Toque app (by [Wuki60](https://github.com/Wuki60/MrCuisineVibeConnect)) running on the Monsieur Cuisine Connect robot. Local push, read-only. **It only works with the fork [McSoda/MrCuisineVibeConnect](https://github.com/McSoda/MrCuisineVibeConnect)**, which adds the local API (install its [latest release](https://github.com/McSoda/MrCuisineVibeConnect/releases/latest), Monsieur Cuisine Connect only, then enable the API in Toque: Settings › Advanced › Home Assistant). The upstream project does not include this API. The documentation below is in French; entity names and UI are translated in English too.*

## Prérequis

- Un **Monsieur Cuisine Connect** avec Toque **du fork** installé : prendre la
  [**dernière release**](https://github.com/McSoda/MrCuisineVibeConnect/releases/latest) (à ce jour `v0.10.0.1`, basée sur la
  0.10.0 de Wuki60, APK `MCC-285-Toque-0.10.0.1.apk`). Les releases du fork ne concernent que le Connect.
- L'API activée sur le robot : **Réglages › Avancé**, bloc **Home Assistant** (elle est désactivée par défaut). L'écran
  affiche alors le **port** (8765 par défaut) et le **jeton**.
- Home Assistant **2026.10** ou plus récent (version sur laquelle les tests ont été exécutés).
- Le robot et Home Assistant sur le même réseau local.

## Installation

### Avec HACS

1. HACS › menu ⋮ › **Dépôts personnalisés** › ajouter l'adresse de ce dépôt, catégorie **Intégration**.
2. Installer **Toque**, puis redémarrer Home Assistant.

### À la main

Copier le dossier `custom_components/toque` dans le dossier `custom_components` de votre configuration, puis redémarrer.

## Configuration

**Réglages › Appareils et services › Ajouter une intégration › Toque.**

- **Découverte automatique** : le robot annonce `_toque._tcp` (mDNS). Il apparaît parmi les appareils découverts ;
  il reste à saisir le **jeton**.
- **Saisie manuelle** : hôte (adresse IP du robot), port, jeton. L'adresse est validée par `GET /info` (public), le jeton
  par `GET /state`.
- Si le jeton est régénéré sur le robot, Home Assistant demande de le ressaisir (réauthentification).
- Si l'adresse IP du robot change, la découverte mDNS met l'adresse à jour toute seule (l'`id` du robot est
  l'identifiant unique : renommer le robot ne crée pas un second appareil).

> **Sécurité.** L'API est en HTTP clair sur le réseau local. Le jeton protège d'un accès fortuit, pas d'une écoute du
> réseau. Ne l'exposez pas à Internet.

## Mise à jour des données et disponibilité

- Données en **temps réel** par le flux SSE (`/events`) ; reconnexion automatique avec `Last-Event-ID` (délai 1 s, doublé
  jusqu'à 30 s). Un flux qui n'envoie aucun octet (pings de 15 s compris) pendant **30 s** est considéré mort et rouvert, **sans** `Last-Event-ID` : les identifiants du robot repartent de 1 à chaque redémarrage de
  Toque, un identifiant antérieur au redémarrage n'a donc plus de sens.
- `/state` et `/session` sont interrogés toutes les **60 s** en repli. Si le flux reste ouvert mais **muet** (plus de `state`
  au-delà du signal de vie du robot + 1 s : 6 s pendant un travail, 31 s sinon), l'intégration interroge `/state` sans
  attendre, puis à chaque réévaluation (toutes les 2 s) tant que le silence dure : le robot reste disponible même si son
  flux se bloque. Les événements dérivés (`action_requise`) sont aussi émis à partir de ces interrogations.
- **Robot indisponible** (toutes les entités) si aucun `state` n'est reçu en 2× le signal de vie du robot : 10 s pendant un
  travail, 60 s sinon.
- **Mesures de la base moteur indisponibles** (température, consigne, régime, niveau de vitesse, poids, temps restant,
  couvercle, bol, moteur, chauffe) si `link` vaut `OFFLINE` ou si `snapshotAgeMs` dépasse 5 s (ou vaut `-1`).
  L'état général, le défaut, l'écran et tout ce qui touche à la recette restent disponibles.

## Entités

Un appareil par robot (`sw_version` = version de Toque, `model` = modèle annoncé par le robot, SKMC 1200 pour un
Connect, `hw_version` = firmware de la carte, masquée s'il vaut 0).

| Entité | Source |
|---|---|
| `sensor` État (`standby`, `running`, `recipe`, `fault`) | `status` |
| `sensor` Température, Consigne de température, Régime (tr/min), Niveau de vitesse, Poids, Temps restant | `machine`, `job` |
| `binary_sensor` Couvercle ouvert, Bol présent, Moteur, Chauffe, Défaut (attributs `code`, `message`), Écran allumé | `machine`, `fault`, `screenOn` |
| `sensor` Recette en cours (attribut `ingredients`), Étape (`n/N`) | `session` |
| `sensor` Étape en cours, Dernière étape manuelle, Prochaine étape manuelle | `session` — état tronqué à 255 caractères, **texte complet dans l'attribut `text`** |
| `event` Cuisine : `step`, `action_requise`, `done`, `job_done`, `fault` | `/events` |
| Diagnostic (inactifs par défaut) : Nombre de recettes, Durée de fonctionnement, Adresse IP, Version d'Android, Firmware de la carte | `/info` |

- **Température** : `unknown` quand le robot renvoie 0. Sa sonde ne mesure rien en dessous de 37 °C environ et renvoie alors 0 :
  ce n'est pas une température. Elle suit la mesure dès que le contenu dépasse ce seuil.
- **Consigne de température** : la température que **Toque demande** pour le travail en cours (par exemple 95 °C pour une
  étape à 95 °C), 0 sans chauffe ni travail. Ce n'est pas l'écho de la base : avant ce comportement, le robot renvoyait une consigne
  ajustée pendant la montée (103 puis 97 °C pour 95 °C demandés, observé sur un robot de développement).

Les attributs `ingredients` et `text` sont exclus de l'enregistreur (recorder).

« Veille » côté utilisateur = `État = standby` **et** `Écran allumé = off`.

### Services

| Service | Rôle |
|---|---|
| `toque.list_recipes` (réponse) | Résumés de recettes ; filtres `query`, `category`, `favorite`, `limit`, `offset`. |
| `toque.get_recipe` (réponse) | Détail d'une recette (`recipe_id`). |
| `toque.add_recipe_to_todo` | Ajoute les ingrédients d'une recette à une entité `todo` (liste de courses). |

`config_entry_id` est facultatif s'il n'y a qu'un robot.

## Points où la spécification est ambiguë ou incomplète

La spécification de référence est `docs/HOME_ASSISTANT_API.md` du fork. Ce qui suit n'est pas inventé : ce sont
des choix faits faute de précision, à confirmer.

- **Vitesse (tr/min).** La spec liste « Vitesse (tr/min) » mais précise que `rpm` et `speed` restent à 0 moteur en marche
  sur le SKMC 1200. L'intégration expose donc `rpm` (Régime) **et** `targetSpeed` (Niveau de vitesse) ; ce dernier est le
  fiable pour l'instant.
- **Température.** Constaté sur un SKMC 1200 : `tempC` vaut 0 sous environ 37 °C puis suit la mesure pendant la chauffe (de
  37 à 95 °C observés, par paliers de 5 °C au-delà de 65 °C). L'intégration traduit ce 0 en `unknown`.
- **Robot Smart.** Toque prend en charge le Monsieur Cuisine Smart (SKMS 1200), mais le fork ne publie pas d'APK pour ce
  robot, et celui du dépôt de Wuki60 n'a pas l'API. Dans le code du fork, `/info` annonce le modèle du robot choisi et
  l'intégration l'affiche. Non testé : je n'ai pas de robot Smart, on ne sait pas quelles mesures (`rpm`, `tempC`) il
  renvoie.
- **`action_requise`.** Le tableau des entités le cite, mais le flux SSE n'a pas d'événement de ce nom. Il est **déduit** de
  `session` : émis quand `awaiting` passe à `user` (ou que l'étape change alors que `awaiting` vaut `user`).
- **Fraîcheur.** `snapshotAgeMs` est évalué à la réception du `state`, il n'est pas augmenté du temps écoulé depuis ; le
  robot republie lui-même un `state` avec un âge qui grandit. Quelles entités dépendent de cette règle (liste ci-dessus) est
  une interprétation de l'intégration.
- **Disponibilité sans flux SSE.** La spec ne dit rien : tant que le flux est coupé, le robot reste disponible 2× 60 s après le
  dernier `state` (l'interrogation de repli le renouvelle).
- **Ingrédients d'une recette détaillée.** La spec dit « format `RecipeCodec.toMap` (ingrédients groupés) » sans le
  détailler ; `toque.add_recipe_to_todo` suppose la même forme que dans `/session` (`[{group, items:[{qty, unit, name, note}]}]`)
  et accepte aussi une liste à plat.
- **Images.** `imageUrl` et `/recipes/{id}/image` (§8) ne sont **pas encore utilisés** par l'intégration.
- **Événements rejoués.** Après une reconnexion, les événements rejoués (50 au plus) déclenchent à nouveau l'entité
  `event` ; un `job_done` ou `step` ancien peut donc être revu.
- **Version d'API.** Seule `api: 1` est acceptée ; une autre est refusée à la configuration.

## Développement

Les tests utilisent `pytest-homeassistant-custom-component` et un faux robot local (`tests/fake_server.py`) ; ils n'ont
besoin d'aucun robot.

```
pip install -r requirements_test.txt
pytest
```

Ce plugin vise Linux (la CI l'exécute sur Ubuntu). Sous Windows il faut de petites cales locales (modules `fcntl` et
`resource` factices dans l'environnement virtuel) ; `tests/conftest.py` neutralise déjà le blocage des sockets sous Windows.
Le jeton utilisé par les tests est factice.

La CI (GitHub Actions) lance hassfest, la validation HACS et les tests.

## Images de marque

Icône et logo (une toque de chef devant une maison) sont dans `custom_components/toque/brand/`, chargés directement par
Home Assistant (2026.3 et plus). Pour les régénérer : `python tools/make_brand.py` (Pillow requis).

## Licence

MIT, comme Toque : voir [LICENSE](LICENSE).
