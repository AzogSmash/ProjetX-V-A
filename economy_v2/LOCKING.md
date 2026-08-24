# Ordre global des verrous industriels

1. verrou global du sous-système (`market`, `AI`, `world market`, expiration) ;
2. `actor_id` impliqués, triés par ordre croissant, verrouillés avec `-actor_id` ;
3. entreprises et profils métier ;
4. objets métier : ordre, contrat, transport, mission, job ;
5. inventaires puis comptes joueurs/IA ;
6. journaux append-only.

Les RPC historiques limitées aux joueurs conservent leur verrou Discord. Une RPC
actor-aware ne verrouille jamais plusieurs acteurs dans un ordre non déterministe.
