# Économie industrielle SQLite

SQLite est l'unique source de vérité de l'économie `?`. Les migrations sont additives,
versionnées dans `migrations/`, et appliquées dans une transaction par `database.py`.

## Progression V1

La valeur d'entreprise est une statistique non encaissable : solde liquide + inventaire
valorisé à prix comptables fixes (minerai 8 CR, lingot 80 CR) + coût cumulé réellement
payable des niveaux d'infrastructure. L'historique des transferts n'est pas ajouté afin
d'éviter le double comptage et l'exploitation de transferts circulaires.

La réputation additionne une progression sous-linéaire déterministe de production,
commerce, logistique, livraisons et contrats, plus les événements persistants de succès
et d'objectifs. Elle n'est ni transférable ni convertible en CR.

Les statistiques personnelles sont exposées par `?bilan` (`?indstats`). La commande
industrielle `?stats` n'existe pas afin de préserver la commande historique `!stats`.

## Notifications et sauvegardes

Les notifications stockent les préférences et des événements idempotents. Leur remise
est lazy : aucun scheduler par transport/job n'est créé. Les sauvegardes utilisent
l'API Backup SQLite, sont compatibles WAL et tournent via un scheduler singleton activé
par `INDUSTRIAL_BACKUP_ENABLED`. Configuration : `INDUSTRIAL_BACKUP_DIR` (défaut
`/data/backups`), `INDUSTRIAL_BACKUP_INTERVAL` (21600 s), et
`INDUSTRIAL_BACKUP_RETENTION` (12, minimum 2). Un snapshot sur `/data` ne protège pas
contre la suppression du volume Railway ; une destination externe pourra être ajoutée
ultérieurement sans modifier le format SQLite.
