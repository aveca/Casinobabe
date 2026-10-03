# CasinoBae MVP

## Livré
- Site responsive et page de vente
- Démo interactive du flux lobby/Rand
- Addon WoW : lobby, whispers, annonces, /rand observé, manche et gagnant
- ACTION_REQUIRED pour les opérations nécessitant une intervention humaine
- Dashboard web et contrat d’événements

## Limites actuelles
- Le navigateur ne peut pas recevoir arbitrairement des événements d’un addon WoW sans bridge externe autorisé.
- Les invitations et autres actions protégées ne sont pas simulées.
- Le MVP ne contient pas de paiement réel ni de règlement monétaire.

## Test rapide WoW
1. Installer addon/CasinoBae/.
2. /cb lobby
3. Faire rejoindre au moins deux joueurs confirmés.
4. /cb start
5. Chaque joueur utilise /rand.
6. L’addon observe les messages système et clôt la manche quand tous les joueurs ont un résultat.