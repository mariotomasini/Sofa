# mediabox

**mediabox** trasforma un PC in un media server per tutta la casa. Lo installi con un solo comando e poi apri un solo indirizzo nel browser: **http://mediabox.local**.

Con mediabox puoi:

- **guardare** film, serie e TV in diretta sulla TV, sul telefono o sul PC, con locandine e guida TV (Jellyfin);
- **cercare qualsiasi film** per titolo e vedere subito:
  - dove guardarlo legalmente in Italia (gratis, in abbonamento, a noleggio);
  - se esiste su Internet Archive, per scaricarlo direttamente;
  - cosa trovano i tuoi indexer di Prowlarr;
- **scaricare** un film e ritrovarlo in Jellyfin già rinominato e con la locandina, senza fare nient'altro;
- **aggiungere un torrent** trovato da te (magnet, link o file `.torrent`), con l'upload ridotto al minimo;
- **ricevere un avviso** quando un film della tua lista diventa gratis su un servizio con pubblicità;
- usare tutto anche **da fuori casa**, senza aprire porte sul router (Tailscale).

---

## Indice

1. [Cosa serve](#cosa-serve)
2. [Installazione su Linux](#installazione-su-linux)
3. [Installazione su Windows](#installazione-su-windows)
4. [Primi passi](#primi-passi)
5. [Come si usa Cerca](#come-si-usa-cerca)
6. [TV in diretta](#tv-in-diretta)
7. [Usare mediabox fuori casa](#usare-mediabox-fuori-casa)
8. [Il comando mediabox](#il-comando-mediabox)
9. [Backup](#backup)
10. [Dove sono i file](#dove-sono-i-file)
11. [Risoluzione dei problemi](#risoluzione-dei-problemi)
12. [Per chi vuole mettere le mani nel codice](#per-chi-vuole-mettere-le-mani-nel-codice)

---

## Cosa serve

- Un PC sempre acceso, collegato alla rete di casa (meglio con il cavo).
  - **Linux** (consigliato): Debian 12 o Ubuntu Server 24.04, 64 bit. Un processore Intel con grafica integrata permette la transcodifica hardware, cioè meno fatica per il PC.
  - **Windows**: Windows 10 (versione 22H2) o Windows 11, 64 bit, con la virtualizzazione attiva nel BIOS. Ci sono alcuni limiti, elencati in [Differenze su Windows](#differenze-su-windows).
- Una **chiave gratuita di TMDB**, il catalogo dei film:
  1. crea un account su [themoviedb.org](https://www.themoviedb.org/signup);
  2. vai su *Impostazioni → API* e chiedi una chiave per uso personale;
  3. copia la **API Key** (corta) oppure il **Read Access Token** (lungo): vanno bene tutti e due.
- Facoltativi:
  - l'app **ntfy** sul telefono, per le notifiche;
  - un **tuner TV**: HDHomeRun di rete oppure chiavetta USB DVB-T2.

Durante l'installazione ti verranno chieste solo tre cose:

1. la chiave TMDB;
2. una password di amministrazione;
3. facoltativo, il nome per le notifiche ntfy.

---

## Installazione su Linux

Accedi al PC con il tuo utente normale (non root) e scrivi:

```bash
git clone https://github.com/mariotomasini/Sofa.git mediabox
cd mediabox
./install.sh
```

Se vuoi anche **Sonarr**, per organizzare le serie TV scaricate, usa `./install.sh --with-sonarr`.

L'installer:

1. controlla il sistema e installa Docker se manca;
2. riconosce la scheda grafica Intel, i tuner USB e un eventuale HDHomeRun in rete;
3. ti fa le tre domande;
4. scarica e avvia i servizi, poi li configura da solo;
5. alla fine stampa un **riepilogo** con gli indirizzi e le poche cose da fare a mano.

La prima volta ci vogliono **10-20 minuti**, a seconda della connessione.

Puoi rilanciare `./install.sh` quando vuoi: non cancella nulla e sistema quello che manca. mediabox viene installato in `/opt/mediabox`; film e download vanno in `/srv/mediabox/dati`, oppure in un disco più grande, se l'installer ne trova uno e tu confermi.

---

## Installazione su Windows

1. Scarica mediabox da GitHub (*Code → Download ZIP*) ed estrai la cartella.
2. Fai **doppio clic su `installa.cmd`** e accetta la richiesta di permessi.
3. Se WSL2 non è ancora attivo, l'installer lo attiva e chiede di **riavviare**. Dopo il riavvio e l'accesso, l'installazione riparte da sola.
4. Docker Desktop viene installato da solo. Al primo avvio potrebbe chiederti di accettare le condizioni d'uso: accettale (per uso personale è gratuito).
5. Rispondi alle domande:
   - le tre domande di base;
   - se rinominare il PC in **MEDIABOX**: dì di sì, serve per l'indirizzo `mediabox.local`;
   - su quale disco mettere i film;
   - se disattivare la sospensione.

mediabox viene installato in `C:\Mediabox`. I film vanno nel disco con più spazio, ad esempio `D:\Mediabox`, e li vedi anche da Esplora file.

### Accensione automatica su Windows

Docker Desktop parte quando entri in Windows. Perché mediabox riparta da solo anche dopo un'interruzione di corrente, attiva l'**accesso automatico**:

1. premi `Win + R`, scrivi `netplwiz` e premi Invio;
2. togli la spunta *"Per utilizzare questo computer è necessario che l'utente immetta il nome utente e la password"* e conferma con la tua password.

Su Windows 11 l'opzione non compare se è attivo l'accesso senza password: vai in *Impostazioni → Account → Opzioni di accesso*, disattiva *"Per una maggiore sicurezza, consenti l'accesso a Windows Hello..."* e riprova.

Nel BIOS, se c'è, attiva anche *"Restore on AC power loss: Power On"*, così il PC si riaccende da solo.

### Differenze su Windows

| | Linux | Windows |
|---|---|---|
| Transcodifica con la grafica Intel | ✅ | ❌ usa il processore (limite di Docker Desktop) |
| Tuner USB + TVHeadend | ✅ | ❌ non supportato: usa un HDHomeRun di rete |
| `mediabox.local` | ✅ automatico | ✅ se il PC si chiama MEDIABOX |
| Si accende da solo | ✅ | ⚠️ serve l'accesso automatico |

Per guardare film sulla TV di casa la transcodifica di solito non serve, perché la TV riproduce il file così com'è. Serve soprattutto da fuori casa o con più TV contemporaneamente.

---

## Primi passi

Apri **http://mediabox.local** dal browser di un PC o di un telefono collegato alla rete di casa. Se non si apre, usa l'indirizzo IP mostrato nel riepilogo, ad esempio `http://192.168.1.50`.

![Pagina iniziale](docs/pagina-iniziale.png)

| Pagina | Indirizzo | Password? |
|---|---|---|
| Pagina iniziale | `/` | no |
| Cerca un film | `/cerca` | no |
| Jellyfin | `/jellyfin` | utente e password di Jellyfin |
| qBittorrent | `/qbit` | sì (amministrazione) |
| Radarr | `/radarr` | sì |
| Prowlarr | `/prowlarr` | sì |
| Sonarr *(se installato)* | `/sonarr` | sì |
| TVHeadend *(se c'è un tuner USB)* | `/tv` | sì |

L'utente è sempre **admin** e la password è quella che hai scelto durante l'installazione.

### 1. Aggiungi i tuoi indexer (una volta sola)

Apri **Prowlarr** dalla pagina iniziale, vai su *Indexer → Aggiungi indexer* e aggiungi quelli che usi. Prowlarr li passa da solo a Radarr e a Cerca. Senza indexer, Cerca mostra comunque la disponibilità legale e Internet Archive.

### 2. Collega la TV

Installa l'app **Jellyfin** sulla TV (Android TV, Fire TV, LG, Samsung...). Come server scrivi:

```
http://mediabox.local/jellyfin
```

Se la TV non lo trova, usa l'IP: `http://192.168.1.50/jellyfin`. Molte TV non riconoscono i nomi `.local`.

---

## Come si usa Cerca

![Scheda di un film](docs/cerca-film.png)

1. Scrivi il titolo e premi **Cerca**.
2. Scegli il film giusto dalle locandine.
3. Nella scheda del film compaiono tre sezioni, che si caricano insieme:
   - **Dove vederlo legalmente in Italia**: servizi gratis con pubblicità, abbonamenti, noleggio e acquisto. I dati vengono da JustWatch.
   - **Internet Archive**: le versioni trovate, con la **migliore** in cima. Viene scelta la risoluzione più alta e il file originale invece delle copie compresse. **Scarica** salva il file direttamente; **Via torrent** usa il torrent ufficiale di Archive. Molti film su Archive sono di pubblico dominio: controlla sempre la licenza nella pagina dell'item.
   - **Indexer (Prowlarr)**: i risultati dei tuoi indexer, con dimensione, seed e peer. **Scarica** manda il torrent a qBittorrent.
4. Segui l'avanzamento nella pagina **Download**. A download finito mediabox passa il film a Radarr, che lo rinomina e lo mette nella libreria **Film** di Jellyfin con la locandina. Lo stato diventa **"Pronto in Jellyfin"**.

### Avvisami se diventa gratis

Nella scheda di un film premi **"Avvisami se diventa disponibile gratis"**. Ogni giorno mediabox controlla se il film è comparso tra i servizi gratuiti con pubblicità, come Pluto TV o Rakuten TV:

- appena succede, lo trovi in evidenza nella pagina di Cerca;
- se hai configurato ntfy, ricevi anche una notifica sul telefono.

Per le notifiche installa l'app **ntfy** e iscriviti all'argomento che hai scelto durante l'installazione. Se l'hai saltato, scrivilo in `NTFY_TOPIC` nel file `.env` e poi esegui `mediabox configura`.

### Aggiungere un torrent trovato da te

Pagina **Aggiungi torrent**:

1. incolla un link magnet o l'indirizzo di un file `.torrent`, oppure carica il file;
2. facoltativo, cerca il film a cui si riferisce: a fine download verrà rinominato e messo in Jellyfin;
3. se non lo indichi, mediabox prova a capirlo dal nome. Se non ci riesce, nella pagina Download compare **"Scegli il film"**.

Puoi anche aggiungere i torrent direttamente da **qBittorrent** con la categoria `manuale`: mediabox li trova da solo e li gestisce allo stesso modo.

Per proteggerti da torrent falsi, qBittorrent **non scarica mai programmi** (`.exe`, `.bat`, `.scr`...).

### Upload ridotto al minimo

qBittorrent è impostato in modo fisso così:

- upload limitato a **1 KiB/s**;
- rapporto massimo **0**: appena finito il download, il torrent si **ferma**;
- **UPnP e NAT-PMP spenti**: nessuna porta viene aperta sul router.

---

## TV in diretta

L'installer cerca da solo un tuner:

- **HDHomeRun** in rete → viene collegato a Jellyfin automaticamente;
- **chiavetta USB DVB-T2** (solo Linux) → viene avviato **TVHeadend** e collegato a Jellyfin, guida TV compresa;
- **nessun tuner** → la TV in diretta viene saltata, e il riepilogo lo dice.

Se colleghi un tuner dopo l'installazione, rilancia `./install.sh` (o `installa.cmd`).

Per **registrare** una serie: in Jellyfin apri la *Guida*, scegli un programma e premi *Registra serie*. Le registrazioni finiscono nella libreria **Registrazioni**.

### Tuner USB: cosa resta da fare a mano

L'installer crea la rete DVB-T con l'elenco dei multiplex italiani, collega il tuner e fa partire la ricerca dei canali. Dopo **5-10 minuti**:

1. apri `http://mediabox.local/tv`;
2. vai su *Configurazione → DVB Inputs → Muxes* e controlla che diversi multiplex siano *OK*;
3. vai su *Configurazione → DVB Inputs → Servizi*, premi **"Mappa tutti i servizi"** e conferma;
4. in Jellyfin vai su *Pannello di controllo → TV in diretta* e premi **"Aggiorna dati guida"**.

Se trova pochi canali, controlla l'antenna e poi vai su *Configurazione → DVB Inputs → Reti*, seleziona "DVB-T Italia" e premi **"Forza scansione"**. Poi ripeti dal punto 3.

### HDHomeRun: la guida TV

Jellyfin non legge la guida TV dall'antenna. Per averla, metti in `EPG_XMLTV_URL` nel file `.env` l'indirizzo di una guida in formato XMLTV per l'Italia: ne esistono di gratuite, cerca "XMLTV Italia". Poi esegui `mediabox configura`.

---

## Usare mediabox fuori casa

Sul PC di mediabox scrivi:

```bash
mediabox remote
```

Il comando installa **Tailscale**, una rete privata gratuita, e ti chiede di accedere con un account. Poi:

1. installa l'app **Tailscale** sul telefono o sul portatile e accedi con lo stesso account;
2. apri l'indirizzo `http://100.x.x.x` che il comando ti ha mostrato. Nell'app Jellyfin usa `http://100.x.x.x/jellyfin`.

Non serve aprire nessuna porta sul router. mediabox risponde solo alla rete di casa e a Tailscale: rifiuta qualsiasi altro accesso.

---

## Il comando mediabox

Su Linux scrivilo in un terminale; su Windows in PowerShell o nel Prompt dei comandi.

| Comando | Cosa fa |
|---|---|
| `mediabox status` | mostra quali servizi sono accesi |
| `mediabox up` / `mediabox down` | accende / spegne tutto |
| `mediabox restart [servizio]` | riavvia tutto o un servizio (es. `mediabox restart jellyfin`) |
| `mediabox logs [servizio]` | mostra i messaggi di un servizio (Ctrl+C per uscire) |
| `mediabox configura` | riapplica la configurazione dopo una modifica al file `.env` |
| `mediabox update` | aggiorna i servizi (fa prima un backup) |
| `mediabox backup` | crea un backup |
| `mediabox restore <file>` | ripristina un backup |
| `mediabox remote` | attiva l'accesso da fuori casa (Tailscale) |
| `mediabox indirizzi` | mostra gli indirizzi da aprire nel browser |

---

## Backup

```bash
mediabox backup
```

Il comando crea un file in `/opt/mediabox/backup/` (Windows: `C:\Mediabox\backup\`) con:

- le impostazioni di mediabox (`.env`, che contiene anche la password);
- la configurazione di tutti i servizi;
- la cronologia di Cerca.

**I film non sono inclusi**, perché sono troppo grandi: per quelli usa un disco esterno.

Copia il backup su una chiavetta. Per ripristinarlo, anche su un PC nuovo dopo aver installato mediabox:

```bash
mediabox restore /percorso/mediabox-backup-AAAA-MM-GG_HHMM.tar.gz
```

---

## Dove sono i file

| Cosa | Linux | Windows |
|---|---|---|
| Film | `/srv/mediabox/dati/media/film` | `D:\Mediabox\media\film` |
| Serie | `.../media/serie` | `...\media\serie` |
| Registrazioni TV | `.../media/registrazioni` | `...\media\registrazioni` |
| Download in corso | `.../downloads` | `...\downloads` |
| Impostazioni (`.env`) | `/opt/mediabox/.env` | `C:\Mediabox\.env` |

I film vengono rinominati come `Titolo (Anno) [tmdbid-123]/Titolo (Anno) Qualità.mkv`, così Jellyfin li riconosce sempre.

Puoi anche copiare tu dei film nella cartella `film`: per farli riconoscere a colpo sicuro, mettili in una cartella chiamata `Titolo (Anno)`.

---

## Risoluzione dei problemi

Primo passo, sempre: `mediabox status`. Se un servizio è spento, usa `mediabox restart <servizio>`. Se continua a spegnersi, guarda `mediabox logs <servizio>`.

### `mediabox.local` non si apre

- **Usa l'indirizzo IP**: lo trovi con `mediabox indirizzi`. Funziona sempre.
- **Android e molte smart TV non riconoscono i nomi `.local`**: lì usa l'IP.
- Su **Linux**: controlla con `systemctl status mediabox-avahi` che il servizio sia attivo, e che il dispositivo sia nella stessa rete (non nella rete "ospiti" del router).
- Su **Windows**: il PC deve chiamarsi MEDIABOX e la rete deve essere impostata come **Privata**: *Impostazioni → Rete e Internet → la tua rete → Tipo di profilo di rete: Privata*.
- Se l'IP del PC cambia spesso, nel router assegna un **IP fisso** al PC di mediabox (si chiama "DHCP statico" o "prenotazione indirizzo").

### Un film scaricato non compare in Jellyfin

1. Apri **Cerca → Download** e guarda lo stato del film:
   - **"Scegli il film"**: mediabox non ha capito di che film si tratta. Premi il pulsante e sceglilo.
   - **"Problema"**: leggi il messaggio sotto. Spesso basta premere **Riprova**.
2. Apri **Radarr → Attività → Coda**: se c'è un avviso giallo, passaci sopra con il mouse per vedere il motivo, ad esempio "file di campione" o "archivio .rar".
3. In Jellyfin vai su *Pannello di controllo → Librerie* e premi **"Scansiona tutte le librerie"**.
4. Se la locandina è sbagliata: in Jellyfin apri il film, poi *⋯ → Identifica* e scegli quello giusto.

### Un torrent non parte o resta fermo

- Se ha pochi o nessun **seed**, scegline un altro con più seed.
- Il limite di upload a 1 KiB/s è voluto. Alcuni tracker privati però penalizzano chi non condivide: con quei tracker mediabox non è adatto.

### La transcodifica non funziona (video a scatti, errore di riproduzione)

- Solo su **Linux** con grafica Intel. Controlla che `/dev/dri/renderD128` esista: `ls -l /dev/dri`. Se non c'è, il driver della grafica non è caricato: aggiorna il sistema (`sudo apt upgrade`) e riavvia.
- Se avevi installato mediabox prima di collegare/attivare la grafica, rilancia `./install.sh`.
- In Jellyfin: *Pannello di controllo → Riproduzione → Transcodifica*. Deve esserci "VAAPI" con dispositivo `/dev/dri/renderD128`.
- I processori Intel più vecchi (prima del 2015 circa) non decodificano HEVC/H.265: togli la spunta da "HEVC" in quella pagina.
- Per vedere l'errore preciso: *Pannello di controllo → Log*, apri l'ultimo file che inizia con `FFmpeg.Transcode`.

### Il tuner TV non viene trovato

- **USB (Linux)**: con il tuner collegato scrivi `ls /dev/dvb`. Se la cartella non esiste:
  - prova un'altra porta USB;
  - installa i firmware con `sudo apt install firmware-linux-nonfree`, poi riavvia.

  Quando `/dev/dvb` compare, rilancia `./install.sh`.
- **USB (Windows)**: non è supportato, usa un HDHomeRun.
- **HDHomeRun**:
  - deve essere nella stessa rete del PC: controlla che l'app HDHomeRun sul telefono lo veda;
  - se l'installer non lo trova da solo, scrivi il suo IP in `HDHOMERUN_IP` nel file `.env` e poi esegui `mediabox configura`.
- **Nessun canale**: vedi [Tuner USB: cosa resta da fare a mano](#tuner-usb-cosa-resta-da-fare-a-mano). Controlla anche l'antenna: deve essere quella del tetto, non un'antennina da interno lontana dalla finestra.

### Ho dimenticato la password / voglio cambiarla

Modifica `ADMIN_PASSWORD` nel file `.env` (`/opt/mediabox/.env` oppure `C:\Mediabox\.env`), poi esegui `mediabox down` e `mediabox configura`.

La password di **Jellyfin** si cambia da Jellyfin stesso (*Impostazioni → Profilo*). Se la cambi, mettila uguale anche nel file `.env`: Cerca la usa per avvisare Jellyfin dei film nuovi.

### "La porta 80 è già usata"

Un altro programma usa già la porta del sito, ad esempio IIS su Windows o un altro web server. L'installer propone di usare la 8080: l'indirizzo diventa `http://mediabox.local:8080`.

### Cerca dice "TMDB rifiuta la chiave API"

Controlla `TMDB_API_KEY` nel file `.env`: deve essere tutta, senza spazi. Poi esegui `mediabox configura`.

---

## Per chi vuole mettere le mani nel codice

### Come è fatto

```
install.sh, mediabox          installer e comando per Linux (bash)
install.ps1, mediabox.ps1     installer e comando per Windows (PowerShell)
installa.cmd, mediabox.cmd    avvio con doppio clic / da Prompt
lib/                          funzioni comuni bash e PowerShell, servizio avahi
configuratore/                container Python che fa il lavoro vero:
  configuratore/templates/      modelli di docker-compose, Caddyfile, Homepage
  configuratore/servizi/        configurazione via API di ogni servizio
cerca/                        il servizio Cerca (FastAPI) + test
.env.example                  tutte le impostazioni, con spiegazioni
```

- Gli script bash e PowerShell fanno solo le cose che dipendono dal sistema: prerequisiti, hardware, domande.
- Tutto il resto lo fa il **configuratore**, uguale su Linux e Windows: genera i file in `generato/`, prepara i volumi, configura i servizi via API e stampa il riepilogo.
- Ogni impostazione si cambia **solo dal file `.env`**. I file in `generato/` vengono riscritti a ogni `mediabox configura`.
- I dati dei servizi stanno in volumi Docker (`mediabox_*`), separati dai file generati.

### Controlli

```bash
make dipendenze   # una volta: pytest, ruff, shellcheck
make check        # sintassi degli script, lint (shellcheck, ruff, PSScriptAnalyzer) e test
```

I test di `cerca/tests/test_scelta_archive.py` usano metadati reali di Internet Archive, salvati in `cerca/tests/fixtures/`.

### Componenti usati

[Jellyfin](https://jellyfin.org), [qBittorrent](https://www.qbittorrent.org), [Radarr](https://radarr.video), [Prowlarr](https://prowlarr.com), [Sonarr](https://sonarr.tv), [TVHeadend](https://tvheadend.org), [Caddy](https://caddyserver.com), [Homepage](https://gethomepage.dev), [Intro Skipper](https://github.com/intro-skipper/intro-skipper). I dati dei film vengono da [TMDB](https://www.themoviedb.org) e quelli sulla disponibilità da [JustWatch](https://www.justwatch.com). Questo prodotto usa l'API di TMDB ma non è approvato né certificato da TMDB.

### Uso responsabile

mediabox è pensato per film di pubblico dominio, contenuti che possiedi e servizi legali. Scaricare opere protette da diritto d'autore senza autorizzazione è illegale in Italia: la responsabilità di cosa scarichi è tua.
