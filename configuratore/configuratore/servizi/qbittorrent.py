"""Configurazione di qBittorrent: cartelle, categorie e upload ridotto al minimo."""

from __future__ import annotations

import json

import httpx

from ..impostazioni import Impostazioni
from ..messaggi import ErroreMediabox, ok
from ..rete import attendi, controlla

INDIRIZZO = "http://qbittorrent:8080"

CATEGORIE = ["radarr", "cerca", "manuale", "sonarr"]


def preferenze(imp: Impostazioni) -> dict:
    """Preferenze fisse: l'upload è ridotto al minimo e niente porte aperte."""
    return {
        "locale": "it",
        "save_path": "/data/downloads",
        "temp_path_enabled": False,
        "auto_tmm_enabled": True,
        "torrent_changed_tmm_enabled": True,
        "save_path_changed_tmm_enabled": True,
        "category_changed_tmm_enabled": True,
        # Upload: 1 KiB/s (il valore è in byte al secondo)
        "up_limit": 1024,
        "alt_up_limit": 1024,
        # Rapporto 0: appena finito il download il torrent si ferma
        "max_ratio_enabled": True,
        "max_ratio": 0,
        "max_ratio_act": 0,  # 0 = ferma il torrent
        # Nessun port forwarding automatico
        "upnp": False,
        "listen_port": int(imp.get("TORRENT_PORT", "6881")),
        "random_port": False,
        # Interfaccia web dietro Caddy
        "web_ui_username": imp.utente,
        "web_ui_password": imp.password,
        "bypass_local_auth": False,
        "bypass_auth_subnet_whitelist_enabled": True,
        "bypass_auth_subnet_whitelist": imp.get("SUBNET"),
        "web_ui_reverse_proxy_enabled": True,
        "web_ui_reverse_proxies_list": imp.get("SUBNET"),
        "web_ui_upnp": False,
    }


def configura(imp: Impostazioni, esito) -> None:
    attendi("qBittorrent", f"{INDIRIZZO}/api/v2/app/version", accetta=lambda r: r.status_code in (200, 403))
    with httpx.Client(base_url=f"{INDIRIZZO}/api/v2", timeout=30) as client:
        r = client.get("/app/version")
        if r.status_code == 403:
            # rete interna non ancora autorizzata: proviamo con la password
            login = client.post("/auth/login", data={"username": imp.utente, "password": imp.password})
            if login.text.strip() != "Ok.":
                raise ErroreMediabox(
                    "qBittorrent non accetta la password di amministrazione.",
                    "Esegui \"mediabox down\" e poi \"mediabox configura\": la password viene reimpostata.",
                )
        controlla(
            client.post("/app/setPreferences", data={"json": json.dumps(preferenze(imp))}),
            "salvare le preferenze di qBittorrent",
        )
        ok("Upload limitato a 1 KiB/s, rapporto 0 con torrent fermato, UPnP spento")

        esistenti = client.get("/torrents/categories").json()
        for nome in CATEGORIE:
            if nome == "sonarr" and not imp.sonarr:
                continue
            percorso = f"/data/downloads/{nome}"
            dati = {"category": nome, "savePath": percorso}
            if nome in esistenti:
                if esistenti[nome].get("savePath") != percorso:
                    controlla(client.post("/torrents/editCategory", data=dati), f"aggiornare la categoria {nome}")
            else:
                controlla(client.post("/torrents/createCategory", data=dati), f"creare la categoria {nome}")
        ok("Categorie: " + ", ".join(c for c in CATEGORIE if c != "sonarr" or imp.sonarr))
