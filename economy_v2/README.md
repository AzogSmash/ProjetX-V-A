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

## Saisons, titres, événements et équipes

Les saisons durent 30 jours UTC et ne remettent à zéro que leurs scores. Le score
provient des productions, transports, ventes mondiales, livraisons et contrats réels ;
aucun transfert de CR ne donne de points. La finalisation lazy fige les scores et remet
uniquement des titres et une réputation limitée.

Les événements sont persistés par cycle UTC de six heures. Leurs multiplicateurs sont
stockés en points de base et contraints entre 0,80 et 1,25. Ils sont appliqués une seule
fois aux formules existantes lors de la production ou de la création d'une opération.
Le calendrier déterministe conserve un horizon glissant d'un an et se prolonge de façon
lazy, transactionnelle et idempotente lorsqu'il reste moins de 30 jours disponibles.

Les rôles d'équipe sont `owner`, `manager` et `employee`. Les permissions V1 sont
explicites et limitées : aucune ne permet de gérer un wallet personnel, transférer
librement des actifs ou changer le propriétaire. Les commandes V1 gèrent l'équipe et
ses consultations ; les actions métier déléguées restent volontairement désactivées.

## Rapport d'équilibrage

`?economyreport` (`?ereport`) est un rapport administrateur strictement en lecture
seule ; l'option `text` produit une version copiable. Les alertes V1 sont centralisées
dans `reporting_repository.py` : production IA > 40 %, métier < 10 %, croissance nette
des CR > 25 % sur 7 jours, spread > 50 %, top 10 % > 70 % de la richesse, contrats
expirés > 35 %, moins de 3 livraisons hebdomadaires et mines pleines > 60 %.
