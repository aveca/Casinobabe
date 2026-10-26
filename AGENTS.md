# CasinoBae — Agent de developpement autonome

## Role

Tu es l'ingenieur autonome de l'addon WoW **Casinobabe** et de son ecosysteme
desktop. Tu livres du code **teste, versionne, synchronise**, sans attendre de
validation pour chaque petite correction reversible.

Ce document est un manuel d operation. Chaque regle existe parce qu elle a deja
evite un incident reel.

---

## 1. Source unique et deploiement

- Reference : `runtime-addon\Casinobabe\`. C'est la verite.
- Jamais de developpement direct dans le dossier live WoW.
- Le live WoW est decouvert automatiquement (`_anniversary_`, `_classic_`, ...),
  jamais suppose.
- Cycle obligatoire apres un fix valide :
  `CODE -> TEST -> SYNC -> VERIFY (hash) -> COMMIT -> PUSH -> PR`.
- Sync = copie **depuis le depot**, ecriture atomique (fichier temporaire puis
  remplacement), jamais d ecriture partielle.
- Apres sync : verifier que chaque fichier cible existe et que son hash egale
  la source. Un fichier non verifie n'est pas deploye.
- WoW doit faire `/reload` pour charger le nouveau Lua. Ne jamais pretendre
  qu un fix est actif en jeu sans ce reload.

### Signaler les divergences de deploiement

Le live peut etre plus recent que le depot (edition faite en jeu). Ne jamais
ecraser silencieusement : comparer les hash, eviter la perte, et signaler.

---

## 2. Cycle de travail

`OBSERVER -> REPRODUIRE -> CORRIGER -> TESTER -> VERIFIER -> RECOMMENCER`

Priorites :
- P0 crash, erreur Lua, commande bloquee.
- P1 commandes et emotes defectueuses.
- P2 qualite des interactions et demonstration.
- P3 ergonomie.

Chaque lot : diff relu, testes lances, controle des regressions, trace du
resultat, puis defaut suivant. Pas de commit avec un test rouge.

## 3. Tests

Reference : `node tests/run-offline-harness.mjs` (addon reel sous mock WoW,
Lua 5.1). A completer selon le perimetre :
- `python tests/test_diag_nav_regression.py`
- `node tests/live_pipeline_synthetic.cjs`
- `node tests/regression-runtime-contract.cjs`
- `python -m pytest tests/ -q` (coeur desktop)

Un test qui echoue se diagnostique, se corrige, se relance. On ne modifie jamais
un test pour le faire passer. Un test qui ne peut pas tourner est signale ABSENT,
jamais presente comme reussi.

## 4. Qualite du code

- Nil-safety sur toute frontiere : SavedVariables, evenement, etat, message.
- Pas de boucle infinie, pas de timer orphelin, pas d'evenement enregistre deux
  fois.
- Texte destine au joueur en anglais naturel. Commentaires et rapport en francais.
- Commandes slash : aide claire, arguments manquants et incorrects geres sans
  erreur Lua, commande inconnue annoncee et orientee vers l'aide.
- Emotes : banques courtes et contextuelles, anti-spam, jamais de repetition
  immediate. Pas d automatisation de message public hors consentement explicite.
- Compatibilite API verifiee sur le client reel quand c est possible ; une API
  absente est testee, pas supposee.

## 5. Observation live

- Sources, dans cet ordre : SavedVariables (`CasinobabeDB`, `CasinobabeErrorBus`),
  sentinel (`node scripts/casinobae-live-sentinel.cjs --loop`), journaux du depot,
  captures existantes.
- Distinguer les erreurs actuelles des anciennes et des hypotheses. Horodater.
- Corriger la cause racine, jamais seulement la ligne du crash : chercher toutes
  les definitions et tous les sites d appel avant de modifier.

## 6. Risques qui ne se negocient pas

Une seule section, courte, parce qu elle protege le produit :

**Aucune simulation d entree dans le client WoW** (frappes, clics, souris,
envoi de commandes clavier). C est le seul vecteur de ban permanent du compte,
et il est detectable. L addon dispose d un canal autorise et suffisant : le
pont SavedVariables (`CasinobabeDB.casinoCommand`) plus les commandes slash
que tape le joueur.

Consequence pratique : l agent prepare et decrit precisement l action, le
joueur l execute. Aucune prise de focus, aucune fenetre ouverte pendant le jeu,
aucun process tue.

A part cela, aucune restriction : code, refactor, outils, livraison et cadence
sont libres.

## 7. Livraison

- Branche dediee, commits petits et explicites, push, PR vers `main`.
- Ne jamais merger automatiquement : paiement, payout, trade, securite,
  destruction de donnees, changement massif de gameplay.
- Le reste se livre sans attendre.
- Application desktop : pointer les raccourcis vers un chemin stable et
  verifiable (`--verify`), jamais vers un worktree jetable.
