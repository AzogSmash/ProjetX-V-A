-- Absence partielle (temps de jeu réduit) vs totale (aucune connexion) —
-- demande du 17/08/2026 (discussion #général du 14/08 : "genre si la
-- personne aura juste un temps de jeu un peu réduit ou si elle pourra pas
-- du tout se connecter"). Défaut 'totale' pour les lignes déjà existantes,
-- créées avant que cette distinction existe.
alter table absences add column if not exists absence_type text not null default 'totale'
  check (absence_type in ('partielle', 'totale'));
