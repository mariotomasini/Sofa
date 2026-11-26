import asyncio
import random

from app.foto_profili import FOTO, FotoProfili, scegli_foto


def test_foto_tutte_diverse_e_non_gia_usate():
    usate = list(FOTO[:5])
    scelte = scegli_foto(10, usate, random.Random(1))
    assert len(set(scelte)) == 10 and not set(scelte) & set(usate)
    # più utenti che foto: si ricomincia, senza fermarsi
    assert len(scegli_foto(len(FOTO) + 3, [], random.Random(2))) == len(FOTO)


def test_assegna_solo_a_chi_non_ha_immagine():
    class Archivio:
        stato = {}

        def stato_leggi(self, k):
            return self.stato.get(k)

        def stato_scrivi(self, k, v):
            self.stato[k] = v

    class Risposta:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        content = b"jpg"

    class Client:
        async def get(self, url, **kw):
            assert url.startswith("https://picsum.photos/id/")
            return Risposta()

    class Jellyfin:
        impostate = []

        async def profili(self):
            return [{"id": "a", "nome": "Cesare", "immagine": ""}, {"id": "b", "nome": "Mario", "immagine": "tag"}]

        async def imposta_immagine(self, id_utente, contenuto, tipo):
            self.impostate.append((id_utente, contenuto, tipo))

    j = Jellyfin()
    archivio = Archivio()
    assert asyncio.run(FotoProfili(archivio, Client(), j).assegna()) == 1
    assert j.impostate == [("a", b"jpg", "image/jpeg")]
    assert "foto_profili:usate" in archivio.stato
