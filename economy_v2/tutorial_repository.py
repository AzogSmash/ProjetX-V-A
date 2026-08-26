from __future__ import annotations

import time
from dataclasses import dataclass

from economy_v2.database import immediate_transaction


@dataclass(frozen=True)
class TutorialStep:
    slug: str
    title: str
    objective: str
    command: str
    tip: str
    check: str | None = None


COMMON_STEPS=(
    TutorialStep("discover","Découvrir l’économie","Comprendre la chaîne Mineur → Marchand → Forgeron → Banquier.","?tutorial next","Le marché, les transports et les contrats relient les entreprises."),
    TutorialStep("company","Créer ton entreprise","Choisis ton métier et crée ta première entreprise.","?company create <métier> <nom>","Métiers : miner, merchant, blacksmith ou banker.","company"),
    TutorialStep("profile","Lire ta fiche","Consulte ton métier, ton entreprise et ta progression.","?fiche","Reviens ensuite avec ?tutorial next."),
)
ROLE_STEPS={
 "common":(
    TutorialStep("choose_job","Choisir un métier","Crée une entreprise pour débloquer un parcours adapté.","?company create <métier> <nom>","Le métier est validé depuis SQLite.","company"),
    TutorialStep("market","Découvrir le marché","Observe le carnet de minerai.","?market","Aucune transaction n’est automatique."),
    TutorialStep("contracts","Découvrir les contrats","Consulte les besoins des entreprises.","?contracts","Les contrats utilisent un escrow."),
    TutorialStep("guide","Utiliser le guide dynamique","Demande les meilleures actions disponibles.","?next","Les recommandations sont en lecture seule."),
 ),
 "miner":(
    TutorialStep("mine","Consulter ta mine","Observe la production passive, le stock et sa capacité.","?mine","Une mine pleine arrête de produire."),
    TutorialStep("collect","Première récolte","Place au moins un minerai dans ton inventaire.","?mine collect","La progression est vérifiée depuis ton inventaire ou tes ventes.","ore_owned"),
    TutorialStep("sell","Vendre du minerai","Crée ton premier ordre de vente de minerai.","?market sell iron_ore <quantité> <prix>","Consulte ?market avant de choisir ton prix.","market_sell"),
    TutorialStep("miner_next","Poursuivre ta progression","Découvre contrats, améliorations et prochaines actions.","?next","Le stockage s’améliore avec ?mine upgrade storage."),
 ),
 "merchant":(
    TutorialStep("buy","Acheter du minerai","Crée ton premier ordre d’achat.","?market buy iron_ore <quantité> <prix>","Les CR sont placés en escrow.","market_buy"),
    TutorialStep("trucks","Comprendre les camions","Consulte capacités, emplacements et transports.","?merchant","Les améliorations utilisent ?merchant upgrade <type>."),
    TutorialStep("transport","Premier transport","Lance un transport de minerai vers un Forgeron.","?merchant transport <@forgeron|id> iron_ore <quantité>","Le destinataire est toujours validé côté SQLite.","transport"),
    TutorialStep("merchant_next","Développer ton réseau","Consulte partenaires, contrats et prochaines actions.","?next","Les partenaires ne donnent aucun accès aux actifs."),
 ),
 "blacksmith":(
    TutorialStep("ore","Obtenir du minerai","Consulte le marché ou un partenaire Marchand.","?market","Le fournisseur IA de secours existe via ?forge ai-supply iron_ore <quantité>."),
    TutorialStep("process","Première forge","Lance un job de transformation.","?forge process iron_ore <quantité>","La durée dépend du niveau de vitesse.","forge_job"),
    TutorialStep("ingots","Collecter les lingots","Récupère une production terminée.","?forge collect","Suis les jobs avec ?forge jobs.","ingot_owned"),
    TutorialStep("forge_next","Trouver un débouché","Prépare une expédition ou consulte les prochaines actions.","?next","Expédition : ?forge shipment create <marchand> <banquier> <quantité>."),
 ),
 "banker":(
    TutorialStep("world_market","Comprendre le prix mondial","Consulte le prix et l’historique sans prédiction.","?bank market","L’historique est disponible avec ?bank history."),
    TutorialStep("ingots","Obtenir des lingots","Reçois des lingots via la chaîne industrielle.","?bank inventory","Un fournisseur IA de secours existe via ?bank ai-order iron_ingot <quantité>.","ingot_owned"),
    TutorialStep("world_sale","Première vente mondiale","Vends des lingots au prix mondial actuel.","?bank sell iron_ingot <quantité>","Une variation future n’est jamais garantie.","world_sale"),
    TutorialStep("bank_next","Piloter la suite","Consulte contrats, historique et prochaines actions.","?next","Compare toujours les données réelles disponibles."),
 ),
}
FINAL_STEP=TutorialStep("complete","Terminer le tutoriel","Découvre les systèmes communs puis poursuis librement.","?tutorial next","?partners • ?contracts • ?delivery • ?achievements • ?objectives • ?season • ?events • ?titles • ?equipe • ?bilan")


def tutorial_steps(path):return COMMON_STEPS+ROLE_STEPS.get(path,ROLE_STEPS["common"])+(FINAL_STEP,)


class IndustrialTutorialMixin:
    @staticmethod
    def _tutorial_path(c,uid):
        row=c.execute("SELECT primary_job FROM industrial_users WHERE discord_user_id=?",(uid,)).fetchone();job=row[0] if row else None
        return job if job in ROLE_STEPS else "common"

    @staticmethod
    def _tutorial_done(c,uid,check):
        if check is None:return False
        queries={
            "company":"SELECT EXISTS(SELECT 1 FROM industrial_companies WHERE owner_discord_user_id=?)",
            "ore_owned":"SELECT EXISTS(SELECT 1 FROM industrial_inventory WHERE owner_discord_user_id=? AND resource_type='iron_ore' AND quantity>0 UNION ALL SELECT 1 FROM industrial_market_orders WHERE owner_discord_user_id=? AND side='sell')",
            "market_sell":"SELECT EXISTS(SELECT 1 FROM industrial_market_orders WHERE owner_discord_user_id=? AND side='sell')",
            "market_buy":"SELECT EXISTS(SELECT 1 FROM industrial_market_orders WHERE owner_discord_user_id=? AND side='buy')",
            "transport":"SELECT EXISTS(SELECT 1 FROM industrial_transports t JOIN industrial_actors a ON a.id=t.operator_actor_id WHERE a.discord_user_id=?)",
            "forge_job":"SELECT EXISTS(SELECT 1 FROM industrial_forge_jobs WHERE owner_discord_user_id=?)",
            "ingot_owned":"SELECT EXISTS(SELECT 1 FROM industrial_inventory WHERE owner_discord_user_id=? AND resource_type='iron_ingot' AND quantity>0 UNION ALL SELECT 1 FROM industrial_forge_collections WHERE owner_discord_user_id=?)",
            "world_sale":"SELECT EXISTS(SELECT 1 FROM industrial_world_sales WHERE banker_discord_user_id=?)",
        }
        sql=queries[check];count=sql.count("?");return bool(c.execute(sql,(uid,)*count).fetchone()[0])

    def _tutorial_view(self,c,uid,row=None):
        path=self._tutorial_path(c,uid)
        if row is None:row=c.execute("SELECT * FROM industrial_tutorial_progress WHERE discord_user_id=?",(uid,)).fetchone()
        if not row:return {"started":False,"path":path,"status":"not_started","current_step":0,"total_steps":8,"step":tutorial_steps(path)[0],"completed_checks":[]}
        stored=dict(row);index=int(stored["current_step"]);stored.update({"started":True,"path":path,"total_steps":8,"step":tutorial_steps(path)[index] if stored["status"]!="completed" else None,"completed_checks":[step.slug for step in tutorial_steps(path) if step.check and self._tutorial_done(c,uid,step.check)]});return stored

    def get_tutorial(self,uid):
        with self._read() as c:return self._tutorial_view(c,uid)

    def update_tutorial(self,uid,action,request_id):
        if action not in {"start","next","restart","stop"}:raise ValueError("invalid tutorial action")
        now=int(time.time())
        with immediate_transaction(self.database_path) as c:
            previous=c.execute("SELECT discord_user_id,action FROM industrial_tutorial_actions WHERE request_id=?",(request_id,)).fetchone()
            if previous:
                if (int(previous[0]),previous[1])!=(uid,action):raise ValueError("tutorial request id parameter mismatch")
                return self._tutorial_view(c,uid)|{"duplicate":True}
            c.execute("INSERT INTO industrial_tutorial_actions(request_id,discord_user_id,action) VALUES(?,?,?)",(request_id,uid,action));row=c.execute("SELECT * FROM industrial_tutorial_progress WHERE discord_user_id=?",(uid,)).fetchone();path=self._tutorial_path(c,uid)
            if action in {"start","restart"}:
                if row and action=="start" and row["status"]=="active":return self._tutorial_view(c,uid,row)
                c.execute("INSERT INTO industrial_tutorial_progress(discord_user_id,path,current_step,status,started_at,updated_at,completed_at) VALUES(?,?,0,'active',?,?,NULL) ON CONFLICT(discord_user_id) DO UPDATE SET path=excluded.path,current_step=0,status='active',started_at=excluded.started_at,updated_at=excluded.updated_at,completed_at=NULL",(uid,path,now,now));return self._tutorial_view(c,uid)
            if not row:return {"started":False,"status":"not_started","path":path,"current_step":0,"total_steps":8,"step":tutorial_steps(path)[0],"completed_checks":[]}
            if action=="stop":
                if row["status"]=="active":c.execute("UPDATE industrial_tutorial_progress SET status='stopped',updated_at=? WHERE discord_user_id=?",(now,uid))
                return self._tutorial_view(c,uid)
            if row["status"]!="active":return self._tutorial_view(c,uid)
            index=int(row["current_step"]);steps=tutorial_steps(path);step=steps[index]
            if step.check and not self._tutorial_done(c,uid,step.check):return self._tutorial_view(c,uid)|{"blocked":True}
            index+=1
            while index<7 and steps[index].check and self._tutorial_done(c,uid,steps[index].check):index+=1
            if index>=8:c.execute("UPDATE industrial_tutorial_progress SET path=?,status='completed',current_step=7,completed_at=?,updated_at=? WHERE discord_user_id=?",(path,now,now,uid))
            else:c.execute("UPDATE industrial_tutorial_progress SET path=?,current_step=?,updated_at=? WHERE discord_user_id=?",(path,index,now,uid))
            return self._tutorial_view(c,uid)

    def get_tutorial_hint(self,uid):
        with self._read() as c:
            row=c.execute("SELECT status,current_step,path FROM industrial_tutorial_progress WHERE discord_user_id=?",(uid,)).fetchone()
            if not row:return None
            path=self._tutorial_path(c,uid);return {"status":row["status"],"current_step":int(row["current_step"]),"total_steps":8,"path":path}
