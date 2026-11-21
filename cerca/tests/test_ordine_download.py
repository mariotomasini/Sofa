import asyncio

from app.ordine_download import OrdineDownload, numero, ordine_voluto, posizioni


def t(h, nome, priorita, aggiunto=0):
    return {"hash": h * 40, "name": nome, "priority": priorita, "added_on": aggiunto}


def test_numero():
    assert numero("Dark.S01E03.1080p.WEB.ITA.ENG") == ("dark", 1, 3)
    assert numero("Dark 2x05 720p") == ("dark", 2, 5)
    assert numero("The.Bear.2022.S02.COMPLETE.1080p") == ("the bear", 2, 0)
    assert numero("Dune.Parte.Due.2024.iTALiAN.1080p") is None


def test_puntate_in_fila_e_film_al_loro_posto():
    torrent = [
        t("a", "Dark.S01E03.1080p", 1, 10),
        t("f", "Dune.2024.1080p", 2, 5),
        t("b", "Dark.S01E01.1080p", 3, 11),
        t("c", "Dark.S01E02.1080p", 4, 12),
        t("z", "Finito.S01E01", 0),  # già finito: fuori dalla coda
    ]
    assert ordine_voluto(torrent, []) == ["b" * 40, "f" * 40, "c" * 40, "a" * 40]


def test_serie_nell_ordine_di_arrivo():
    torrent = [t("x", "Lost.S01E02", 1, 50), t("y", "Dark.S01E02", 2, 10),
               t("w", "Lost.S01E01", 3, 60), t("v", "Dark.S01E01", 4, 20)]
    assert ordine_voluto(torrent, []) == ["v" * 40, "y" * 40, "w" * 40, "x" * 40]


def test_scelti_per_primi():
    torrent = [t("b", "Dark.S01E01", 1), t("c", "Dark.S01E02", 2), t("d", "Dark.S01E05", 3)]
    assert ordine_voluto(torrent, ["d" * 40, "q" * 40]) == ["d" * 40, "b" * 40, "c" * 40]


class QbitFinto:
    def __init__(self, torrent):
        self.torrent = torrent
        self.spostati = []

    async def tutti(self):
        return self.torrent

    async def in_fondo(self, h):
        self.spostati.append(h)
        coda = sorted((x for x in self.torrent if x["priority"] > 0), key=lambda x: x["priority"])
        coda = [x for x in coda if x["hash"] != h] + [x for x in coda if x["hash"] == h]
        for i, x in enumerate(coda):
            x["priority"] = i + 1


class ArchivioFinto:
    def __init__(self):
        self.dati = {}

    def stato_leggi(self, k):
        return self.dati.get(k)

    def stato_scrivi(self, k, v):
        self.dati[k] = v


def test_riordina_solo_se_serve_e_scarica_prima():
    qbit = QbitFinto([t("a", "Dark.S01E02", 1), t("b", "Dark.S01E01", 2), t("c", "Dark.S01E03", 3)])
    ordine = OrdineDownload(ArchivioFinto(), qbit)
    assert asyncio.run(ordine.ordina()) is True
    assert list(posizioni(qbit.torrent)) == ["b" * 40, "a" * 40, "c" * 40]
    qbit.spostati.clear()
    assert asyncio.run(ordine.ordina()) is False and qbit.spostati == []
    asyncio.run(ordine.scarica_prima("C" * 40))
    assert list(posizioni(qbit.torrent)) == ["c" * 40, "b" * 40, "a" * 40]
    # finito il torrent scelto, la scelta si dimentica
    qbit.torrent[2]["priority"] = 0
    asyncio.run(ordine.ordina())
    assert ordine.primi() == []
