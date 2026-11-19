"""Ricerca di un HDHomeRun nella rete di casa (protocollo di discovery UDP).

Va eseguito con la rete dell'host (docker run --network host): stampa l'IP
del primo HDHomeRun trovato, oppure niente.
"""

from __future__ import annotations

import socket
import struct
import time
import zlib

PORTA = 65001
TIPO_RICHIESTA = 0x0002
TIPO_RISPOSTA = 0x0003
TAG_TIPO_DISPOSITIVO = 0x01
TAG_ID_DISPOSITIVO = 0x02
JOLLY = 0xFFFFFFFF


def pacchetto_richiesta() -> bytes:
    contenuto = struct.pack(">BBI", TAG_TIPO_DISPOSITIVO, 4, JOLLY) + struct.pack(">BBI", TAG_ID_DISPOSITIVO, 4, JOLLY)
    corpo = struct.pack(">HH", TIPO_RICHIESTA, len(contenuto)) + contenuto
    return corpo + struct.pack("<I", zlib.crc32(corpo) & 0xFFFFFFFF)


def e_risposta(dati: bytes) -> bool:
    if len(dati) < 8:
        return False
    tipo, lunghezza = struct.unpack(">HH", dati[:4])
    if tipo != TIPO_RISPOSTA or len(dati) != 4 + lunghezza + 4:
        return False
    crc = struct.unpack("<I", dati[-4:])[0]
    return crc == zlib.crc32(dati[:-4]) & 0xFFFFFFFF


def cerca(attesa: float = 3.0) -> list[str]:
    trovati: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.5)
        try:
            s.sendto(pacchetto_richiesta(), ("255.255.255.255", PORTA))
        except OSError:
            return trovati
        scadenza = time.monotonic() + attesa
        while time.monotonic() < scadenza:
            try:
                dati, (ip, _) = s.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                break
            if e_risposta(dati) and ip not in trovati:
                trovati.append(ip)
    return trovati


def main() -> int:
    trovati = cerca()
    if trovati:
        print(trovati[0])
    return 0
