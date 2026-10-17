# Procédure rejouable : session pêche WoW en 1 bouton

Ce document décrit la séquence **exactement telle qu'elle a été exécutée à la
main puis rejouée** par `scripts/fishing-oneclick.ps1`, avec les causes racines
rencontrées et les contraintes non contournables.

## La procédure

| # | Étape | Automatisable | Comment |
|---|-------|---------------|---------|
| 1 | Sync addon source → live | oui | copie fichier par fichier, jamais de suppression |
| 2 | Battle.net : **UNE** instance | oui | `Start-Process Battle.net.exe` seulement si 0 instance |
| 3 | Authentification bnet | **non** | voir « Authentification » ci-dessous |
| 4 | Lancement WoW | oui | `Battle.net.exe --launch=wow_classic_anniversary`, **une seule fois** |
| 5 | Attente fenêtre + 1er plan | oui | `FindWindow("World of Warcraft")` + `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` |
| 6 | Démarrage bot en arrière-plan | oui | `Start-Process python scripts\fishing_bot.py --cli` (fenêtre minimisée, logs redirigés) |
| 7 | Attente porte « perso en jeu » | oui | le bot attend le HUD lui-même (30 × 10 s) |

Commande :

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\fishing-oneclick.ps1 -NoWait
```

Sans `-NoWait`, le script attend en plus que la porte « perso en jeu » soit
franchie et affiche la progression.

## Authentification Battle.net — ce qui est faisable, et ce qui ne l'est pas

Le programme **ne saisit jamais un mot de passe**. Ni dans le script, ni par un
outil annexe, ni depuis un fichier. Un secret qui transite par une ligne de
commande ou un historique PowerShell est un secret compromis (lisible par tout
processus du poste, écrit en clair sur disque).

Ce qui est automatisable, et c'est suffisant :

- **« Keep me logged in »** (déjà coché) : la session bnet persiste plusieurs
  semaines. Après une première connexion, les lancements suivants n'ont
  **plus aucune étape de login**.
- **Passkey / Windows Hello** : le programme clique « Use passkey », l'OS
  authentifie via PIN/empreinte. Le secret ne quitte jamais l'OS. Vérifié le
  2026-10-17 : le titre de la fenêtre passe de `Battle.net Login` à
  `Battle.net`, ce qui est précisément le signal que le script attend.
- **« Email a code »** : même principe, code lu par l'utilisateur.

L'utilisateur valide donc **une fois** (PIN ou code). Ensuite le bouton fait
tout seul.

> Rappel : un mot de passe collé dans un chat est un mot de passe à changer.

## Causes racines trouvées (à ne pas réintroduire)

### 1. Crash « Blizzard Error » = deux clients Battle.net

`Battle.net.exe --launch=...` **démarre une nouvelle instance** au lieu de
router vers l'instance existante si celle-ci n'a pas fini son démarrage. Résultat
observé : deux fenêtres `Battle.net Login`, crash `L'application a rencontré une
erreur inattendue`, et aucun WoW lancé.

Règle codée dans le script : compter les fenêtres bnet, n'en démarrer **qu'une**
si 0, attendre sa fenêtre, envoyer `--launch` **une seule fois**, et refuser de
continuer si deux fenêtres authentifiées coexistent.

### 2. Détection de session = le titre de la fenêtre

| Titre | Signification |
|-------|---------------|
| `Battle.net Login` | session **déconnectée** → le script s'arrête et le dit |
| `Battle.net` | session **authentifiée** → on peut lancer WoW |

Une capture d'écran ne suffit pas à distinguer les deux (le formulaire de login
est aussi « connectedness »), d'où le contrôle par titre.

### 3. `fishing_bot.py` ne compilait pas

Le bloc `wf_gate_*` était indenté de 4 espaces dans une méthode de classe à 8 →
`IndentationError`, donc **aucun bot n'a pu démarrer** depuis ce worktree.
Détecté par `python -m py_compile`, qui fait partie des contrôles obligatoires.

### 4. Deux clients WoW = une seule fenêtre ciblable

Les 2 clients partagent le titre `World of Warcraft`. `find_wow_window()`
renvoyait la première : deux bots se seraient visés la même fenêtre, et les
helpers `send_key()` / `send_escape()` (qui re-résolvent la fenêtre à chaque
appui) auraient pu taper dans le mauvais client.

Corrigé par `set_wow_instance()` + `--instance N` (cible fixée une fois par
process), avec un journal et un verrou PID par instance, et une trace
`WIN_TARGET` au démarrage.

## Contrainte physique : un seul client actif à la fois

Windows n'accorde le focus clavier qu'à **une** fenêtre. `send_key()` refuse
d'envoyer une touche si le client ciblé n'est pas au premier plan
(`fishing_bot.py`), sinon la touche partirait dans une autre application.

Conséquence : **deux bots de pêche simultanés sont impossibles** — le second
resterait en `FOCUS_LOST` en boucle. Le script refuse `-BotInstances "1,2"` sauf
`-AllowFocusRace`. La bonne solution est un **arbitre de focus** (rotation d'un
client actif à la fois), pas un deuxième bot.

## Vérification

```powershell
Get-Content state\oneclick-last-run.json      # résumé machine-lisible
Get-Content reports\fishing-bot-log.jsonl -Tail 5
Get-Content state\fishing-bot.pid             # PID du bot
```

Journaux du bot : `reports\fishing-bot-log.jsonl` (fenêtre ciblée `WIN_TARGET` en
première ligne). Console : `logs\fishing-bot-cli.out.log`.