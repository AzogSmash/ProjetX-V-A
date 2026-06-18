from flask import Flask
from threading import Thread
import logging

app = Flask("")

# Désactive les logs Flask pour réduire le spam
log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)


@app.route("/")
def home():
    return "🤖 HappyBot is online and running!"


@app.route("/health")
def health():
    return {"status": "healthy", "bot": "active"}, 200


def run():
    try:
        app.run(host="0.0.0.0", port=8080, debug=False, use_reloader=False)
    except Exception as e:
        print(f"Erreur serveur web : {e}")


def keep_alive():
    t = Thread(target=run)
    t.daemon = True  # Le thread se ferme avec le programme principal
    t.start()
    print("🌐 Serveur web Keep-Alive démarré sur le port 8080")
