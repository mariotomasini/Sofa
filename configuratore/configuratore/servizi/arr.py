"""Aiuti comuni per Radarr, Sonarr e Prowlarr (stessa famiglia di API)."""

from __future__ import annotations

import httpx

from ..messaggi import ErroreMediabox
from ..rete import attendi, controlla


class Arr:
    def __init__(self, nome: str, indirizzo: str, chiave: str, versione: str = "v3") -> None:
        self.nome = nome
        self.indirizzo = indirizzo
        self.api = f"{indirizzo}/api/{versione}"
        self.intestazioni = {"X-Api-Key": chiave}

    def attendi(self) -> None:
        attendi(
            self.nome,
            f"{self.api}/system/status",
            accetta=lambda r: r.status_code in (200, 401),
        )
        r = httpx.get(f"{self.api}/system/status", headers=self.intestazioni, timeout=10)
        if r.status_code == 401:
            raise ErroreMediabox(
                f"{self.nome} non accetta la chiave API del file .env.",
                f"Esegui \"mediabox restart\" e poi \"mediabox configura\": {self.nome} legge la chiave all'avvio.",
            )

    # --- chiamate base -------------------------------------------------------
    def _r(self, metodo: str, percorso: str, cosa: str, **kw) -> httpx.Response:
        kw.setdefault("timeout", 60)
        r = httpx.request(metodo, f"{self.api}/{percorso}", headers=self.intestazioni, **kw)
        return controlla(r, f"{cosa} in {self.nome}")

    def get(self, percorso: str, cosa: str = "leggere le impostazioni", **kw):
        return self._r("GET", percorso, cosa, **kw).json()

    def post(self, percorso: str, dati, cosa: str = "salvare le impostazioni", **kw):
        return self._r("POST", percorso, cosa, json=dati, **kw).json()

    def put(self, percorso: str, dati, cosa: str = "salvare le impostazioni", **kw):
        return self._r("PUT", percorso, cosa, json=dati, **kw).json()

    # --- utilità ---------------------------------------------------------------
    def aggiorna_config(self, sezione: str, modifiche: dict) -> None:
        """Legge /config/<sezione>, applica le modifiche e salva solo se serve."""
        attuale = self.get(f"config/{sezione}")
        if all(attuale.get(k) == v for k, v in modifiche.items()):
            return
        attuale.update(modifiche)
        self.put(f"config/{sezione}", attuale, f"salvare la configurazione \"{sezione}\"")

    def assicura_da_schema(
        self,
        risorsa: str,
        implementazione: str,
        nome: str,
        campi: dict,
        extra: dict | None = None,
    ) -> dict:
        """Crea (o aggiorna) un elemento partendo dallo schema del servizio.

        Vale per download client, applicazioni di Prowlarr, notifiche…
        Le chiavi di `extra` vengono applicate solo se esistono nello schema.
        """
        extra = extra or {}
        esistente = next((e for e in self.get(risorsa) if e.get("name") == nome), None)
        if esistente is None:
            schemi = self.get(f"{risorsa}/schema")
            base = next((s for s in schemi if s.get("implementation") == implementazione), None)
            if base is None:
                raise ErroreMediabox(
                    f"{self.nome} non conosce \"{implementazione}\".",
                    "Probabilmente la versione del servizio è cambiata: aggiorna mediabox.",
                )
            base = dict(base)
            base.pop("presets", None)
        else:
            base = esistente
        base["name"] = nome
        base["enable"] = True
        for chiave, valore in extra.items():
            if chiave in base:
                base[chiave] = valore
        for campo in base.get("fields", []):
            if campo["name"] in campi:
                campo["value"] = campi[campo["name"]]
        if esistente is None:
            return self.post(risorsa, base, f"aggiungere {nome}", params={"forceSave": "true"})
        return self.put(f"{risorsa}/{esistente['id']}", base, f"aggiornare {nome}", params={"forceSave": "true"})

    def id_lingua(self, nome_inglese: str) -> int | None:
        for lingua in self.get("language"):
            if lingua.get("name") == nome_inglese:
                return lingua["id"]
        return None

    def assicura_root_folder(self, percorso: str) -> None:
        if any(r.get("path", "").rstrip("/") == percorso for r in self.get("rootfolder")):
            return
        self.post("rootfolder", {"path": percorso}, f"aggiungere la cartella {percorso}")


def campi_qbittorrent(imp, categoria_campo: str, categoria: str) -> dict:
    return {
        "host": "qbittorrent",
        "port": 8080,
        "useSsl": False,
        "urlBase": "",
        "username": imp.utente,
        "password": imp.password,
        categoria_campo: categoria,
        "initialState": 0,
    }
