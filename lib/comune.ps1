# Funzioni comuni a install.ps1 e mediabox.ps1 (Windows).
# Questo file viene incluso con ". lib\comune.ps1": non va eseguito da solo.

$Utf8SenzaBom = New-Object System.Text.UTF8Encoding $false

function Scrivi-Titolo([string]$Testo) { Write-Host ""; Write-Host "== $Testo ==" -ForegroundColor Cyan }
function Scrivi-Info([string]$Testo)   { Write-Host "  · $Testo" }
# √ e × invece di ✔ e ✘: il carattere della console di Windows non ha questi ultimi (si vedrebbero quadratini)
function Scrivi-Ok([string]$Testo)     { Write-Host "  √ $Testo" -ForegroundColor Green }
function Scrivi-Avviso([string]$Testo) { Write-Host "  ! $Testo" -ForegroundColor Yellow }

# Mostra un errore con "cosa fare" ed esce
function Esci-ConErrore([string]$Cosa, [string]$CosaFare = "") {
    Write-Host "  × $Cosa" -ForegroundColor Red
    if ($CosaFare) { Write-Host "    Cosa fare: $CosaFare" }
    Write-Host ""
    if ($script:InRegistro) { Scrivi-StatoInstallazione "errore" }
    if ($script:RegistroInstaller) { Write-Host "    Il registro completo è in: $($script:RegistroInstaller)" }
    # Durante l'installazione (finestra aperta con doppio clic) aspettiamo prima di chiudere
    if ($script:PausaSuErrore) { Read-Host "  Premi Invio per chiudere" | Out-Null }
    exit 1
}

function Chiedi-SiNo([string]$Domanda, [bool]$Predefinito = $true) {
    $suggerimento = if ($Predefinito) { "[S/n]" } else { "[s/N]" }
    while ($true) {
        $risposta = (Read-Host "  ? $Domanda $suggerimento").Trim().ToLower()
        if (-not $risposta) { return $Predefinito }
        if (@("s", "si", "sì", "y", "yes") -contains $risposta) { return $true }
        if (@("n", "no") -contains $risposta) { return $false }
        Write-Host "    Rispondi s (sì) oppure n (no)."
    }
}

# Esegue un comando esterno (docker, wsl…) scartandone i messaggi di errore.
# Con $ErrorActionPreference = "Stop", Windows PowerShell 5.1 trasforma ogni riga
# scritta su stderr e redirezionata in un errore che ferma tutto lo script:
# qui dentro le preferenze tornano "Continue" solo per quel comando. $LASTEXITCODE resta valido.
function Senza-Stderr([scriptblock]$Blocco) {
    $prima = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Blocco } finally { $ErrorActionPreference = $prima }
}

function Leggi-Env([string]$Chiave, [string]$File) {
    if (-not (Test-Path $File)) { return "" }
    $riga = Get-Content -LiteralPath $File -Encoding UTF8 | Where-Object { $_ -match "^$([regex]::Escape($Chiave))=" } | Select-Object -Last 1
    if ($null -eq $riga) { return "" }
    return $riga.Substring($Chiave.Length + 1)
}

function Scrivi-Env([string]$Chiave, [string]$Valore, [string]$File) {
    $righe = @()
    if (Test-Path $File) {
        $righe = @(Get-Content -LiteralPath $File -Encoding UTF8 | Where-Object { $_ -notmatch "^$([regex]::Escape($Chiave))=" })
    }
    $righe += "$Chiave=$Valore"
    [System.IO.File]::WriteAllLines($File, [string[]]$righe, $Utf8SenzaBom)
}

function Nuova-ChiaveCasuale {
    $byte = New-Object byte[] 16
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($byte)
    return (($byte | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Trova-IpLocale {
    $conf = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } | Select-Object -First 1
    if ($conf) { return $conf.IPv4Address[0].IPAddress }
    return ""
}

# --- Docker -------------------------------------------------------------------

function Aggiungi-DockerAlPath {
    $cartella = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
    if ((Test-Path $cartella) -and ($env:Path -notlike "*$cartella*")) { $env:Path = "$env:Path;$cartella" }
}

function Attendi-Docker([int]$Secondi = 600) {
    Aggiungi-DockerAlPath
    $exe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    Senza-Stderr { & docker info *> $null }
    if ($LASTEXITCODE -eq 0) { return }
    if (Test-Path $exe) { Start-Process $exe | Out-Null }
    Scrivi-Info "Avvio Docker Desktop (la prima volta può chiederti di accettare le condizioni d'uso)…"
    Scrivi-Info "Se si apre la sua finestra: Accept sulle condizioni, poi Skip. Aspetto fino a 10 minuti."
    $scadenza = (Get-Date).AddSeconds($Secondi)
    while ((Get-Date) -lt $scadenza) {
        Start-Sleep -Seconds 5
        Senza-Stderr { & docker info *> $null }
        if ($LASTEXITCODE -eq 0) { return }
    }
    Esci-ConErrore "Docker Desktop non risponde." `
        "Apri Docker Desktop dal menu Start, accetta le condizioni se richiesto, aspetta che diventi verde e rilancia."
}

# Niente param(): in una funzione "avanzata" PowerShell scambia "-d" (di "up -d") per il suo
# -Debug e non lo passa a Docker, che allora avvia i servizi in primo piano e non torna più.
function Invoca-Compose {
    $generato = Join-Path $script:MediaboxDir "generato"
    & docker compose --project-directory $generato -f (Join-Path $generato "docker-compose.yml") @args
}

function Costruisci-Configuratore {
    $base = Leggi-Env "IMG_PYTHON" $script:EnvFile
    if (-not $base) { $base = "docker.io/library/python:3.13-slim" }
    Scrivi-Info "Preparo il configuratore…"
    & docker build -q --build-arg "BASE_IMAGE=$base" -t mediabox/configuratore:locale (Join-Path $script:MediaboxDir "configuratore") | Out-Null
    if ($LASTEXITCODE -ne 0) { Esci-ConErrore "Non riesco a preparare il configuratore." "Controlla la connessione a Internet e riprova." }
}

# Menu "Cosa guardi?" (0 = scelte salvate, 3 = nessuna modifica)
function Mostra-MenuImpostazioni([switch]$PrimaVolta) {
    $argomenti = "run --rm -it -v `"$($script:MediaboxDir):/mediabox`" mediabox/configuratore:locale impostazioni"
    if ($PrimaVolta) { $argomenti += " --prima-volta" }
    # Start-Process lascia il menu collegato alla finestra (le domande compaiono subito)
    $processo = Start-Process docker -ArgumentList $argomenti -NoNewWindow -Wait -PassThru
    return $processo.ExitCode
}

function Genera-File {
    Costruisci-Configuratore
    & docker run --rm -v "$($script:MediaboxDir):/mediabox" mediabox/configuratore:locale genera | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Esci-ConErrore "Non riesco a generare i file di configurazione." "Leggi il messaggio qui sopra, correggi il file .env e riprova."
    }
}

function Riavvia-Segnati {
    $file = Join-Path $script:MediaboxDir "generato\.da-riavviare"
    if (-not (Test-Path $file)) { return }
    foreach ($servizio in (Get-Content $file | Where-Object { $_.Trim() })) {
        Scrivi-Info "Riavvio $servizio per applicare le modifiche…"
        Senza-Stderr { Invoca-Compose restart $servizio *> $null }
    }
    Remove-Item $file -Force
}

# Scarica le immagini una alla volta: la pagina nel browser mostra "3 di 12"
function Scarica-Immagini {
    $immagini = @(Senza-Stderr { Invoca-Compose config --images 2>$null } | Where-Object { $_ -and $_ -notlike "mediabox/*" } | Sort-Object -Unique)
    $i = 0
    foreach ($immagine in $immagini) {
        $i++
        Scrivi-Info "Scarico $(($immagine -split '/')[-1]) ($i di $($immagini.Count))"
        Senza-Stderr { & docker pull -q $immagine *> $null }
        if ($LASTEXITCODE -ne 0) { Scrivi-Avviso "Non riesco a scaricare $(($immagine -split '/')[-1]): riprovo all'avvio." }
    }
}

# Tutto il ciclo: genera → prepara → avvia → configura via API. Restituisce $true se tutto è andato bene.
function Applica-Configurazione {
    Genera-File
    Scrivi-Titolo "Scarico i programmi (la prima volta può richiedere diversi minuti)"
    Scarica-Immagini
    Scrivi-Info "Preparo Cerca"
    Invoca-Compose build -q cerca | Out-Host
    if ($LASTEXITCODE -ne 0) { Esci-ConErrore "Non riesco a preparare Cerca." "Controlla la connessione a Internet e riprova." }
    Scrivi-Titolo "Avvio i servizi"
    Invoca-Compose run --rm --remove-orphans -T configuratore prepara | Out-Host
    # la pagina della configurazione passa a Caddy (/impostazioni): libera la porta
    Ferma-ProceduraWeb
    Invoca-Compose up -d --remove-orphans | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Esci-ConErrore "Alcuni servizi non sono partiti." "Guarda i messaggi qui sopra; spesso basta riprovare con: mediabox configura"
    }
    Riavvia-Segnati
    Scrivi-Ok "Servizi avviati"
    Invoca-Compose run --rm -T configuratore configura | Out-Host
    return ($LASTEXITCODE -eq 0)
}

# --- Configurazione dal browser -----------------------------------------------------
$script:NomeProcedura = "mediabox-procedura"

function Scrivi-StatoInstallazione([string]$Valore) {
    [System.IO.File]::WriteAllText((Join-Path $script:MediaboxDir "installazione.stato"), "$Valore`n", $Utf8SenzaBom)
}

# Esegue un blocco scrivendo tutto anche nel registro che la pagina legge. Restituisce $true se è andato bene.
function Con-Registro([scriptblock]$Blocco) {
    $registro = Join-Path $script:MediaboxDir "installazione.log"
    [System.IO.File]::WriteAllText($registro, "", $Utf8SenzaBom)
    Scrivi-StatoInstallazione "in_corso"
    $script:InRegistro = $true
    $esito = $false
    Start-Transcript -Path $registro -Append | Out-Null
    try {
        $esito = [bool](& $Blocco | Select-Object -Last 1)
    } finally {
        Stop-Transcript | Out-Null
        $script:InRegistro = $false
        Scrivi-StatoInstallazione $(if ($esito) { "fatto" } else { "errore" })
    }
    return $esito
}

# I dischi da proporre per i media, con lo spazio libero
function Scrivi-Dischi {
    $dischi = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Sort-Object FreeSpace -Descending)
    $migliore = $dischi | Select-Object -First 1
    $elenco = foreach ($d in $dischi) {
        $percorso = if ($d.DeviceID -eq "C:") { "C:/Mediabox/dati" } else { "$($d.DeviceID)/Mediabox" }
        $nome = if ($d.VolumeName) { "Disco $($d.DeviceID) ($($d.VolumeName))" } else { "Disco $($d.DeviceID)" }
        [ordered]@{
            percorso = $percorso; nome = $nome
            libero_gb = [math]::Round($d.FreeSpace / 1GB); totale_gb = [math]::Round($d.Size / 1GB)
            consigliato = ($d.DeviceID -eq $migliore.DeviceID)
        }
    }
    $json = ConvertTo-Json -InputObject @($elenco) -Depth 3
    [System.IO.File]::WriteAllText((Join-Path $script:MediaboxDir "dischi.json"), $json, $Utf8SenzaBom)
}

function Avvia-ProceduraWeb {
    $porta = Leggi-Env "HTTP_PORT" $script:EnvFile
    if (-not $porta) { $porta = "80" }
    Ferma-ProceduraWeb
    # se c'è un mediabox vecchio acceso, la porta è sua: lo spengo
    if (Test-Path (Join-Path $script:MediaboxDir "generato\docker-compose.yml")) { Senza-Stderr { Invoca-Compose stop caddy *> $null } }
    & docker run -d --name $script:NomeProcedura -p "$($porta):8080" -v "$($script:MediaboxDir):/mediabox" `
        mediabox/configuratore:locale web | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Esci-ConErrore "Non riesco ad aprire la pagina di configurazione sulla porta $porta." `
            "Controlla che nessun altro programma usi quella porta e rilancia l'installer."
    }
}

function Ferma-ProceduraWeb {
    Senza-Stderr { & docker rm -f $script:NomeProcedura *> $null }
}

# Aspetta che la configurazione nel browser sia finita
function Attendi-ProceduraWeb {
    $flag = Join-Path $script:MediaboxDir ".configurazione-pronta"
    Scrivi-Info "Aspetto che tu finisca la configurazione nel browser… (per interrompere chiudi questa finestra)"
    while (-not (Test-Path $flag)) {
        $attivo = & docker ps -q --filter "name=^$($script:NomeProcedura)$"
        if (-not $attivo) {
            Scrivi-Avviso "La pagina di configurazione si è chiusa: la riapro."
            Avvia-ProceduraWeb
        }
        Start-Sleep -Seconds 2
    }
    Remove-Item $flag -Force
    Scrivi-Ok "Configurazione ricevuta"
}

function Indirizzo-Principale {
    $porta = Leggi-Env "HTTP_PORT" $script:EnvFile
    $ip = Leggi-Env "HOST_IP" $script:EnvFile
    if (-not $ip) { $ip = "mediabox.local" }
    if (-not $porta -or $porta -eq "80") { return "http://$ip" }
    return "http://$($ip):$porta"
}

# --- HDHomeRun ----------------------------------------------------------------------
# Stesso protocollo di configuratore/hdhomerun.py: richiesta UDP in broadcast sulla porta 65001.
function Trova-HDHomeRun([int]$Secondi = 3) {
    $contenuto = [byte[]](0x01, 0x04, 0xFF, 0xFF, 0xFF, 0xFF, 0x02, 0x04, 0xFF, 0xFF, 0xFF, 0xFF)
    $corpo = [byte[]](0x00, 0x02, 0x00, $contenuto.Length) + $contenuto
    $crc = Calcola-Crc32 $corpo
    $pacchetto = $corpo + [BitConverter]::GetBytes([uint32]$crc)
    $udp = New-Object System.Net.Sockets.UdpClient
    try {
        $udp.EnableBroadcast = $true
        $udp.Client.ReceiveTimeout = 500
        [void]$udp.Send($pacchetto, $pacchetto.Length, (New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Broadcast), 65001))
        $scadenza = (Get-Date).AddSeconds($Secondi)
        while ((Get-Date) -lt $scadenza) {
            try {
                $da = New-Object System.Net.IPEndPoint ([System.Net.IPAddress]::Any), 0
                $dati = $udp.Receive([ref]$da)
                if ($dati.Length -ge 8 -and $dati[0] -eq 0 -and $dati[1] -eq 3) { return $da.Address.ToString() }
            } catch { $null = $_ }  # nessuna risposta in questo mezzo secondo: si riprova
        }
    } catch { $null = $_ } finally { $udp.Close() }  # rete non disponibile: nessun HDHomeRun
    return ""
}

function Calcola-Crc32([byte[]]$Dati) {
    # Numeri decimali e [long]: in PowerShell 5.1 0xFFFFFFFF vale -1
    $tutti = [long]4294967295
    $polinomio = [long]3988292384
    $crc = $tutti
    foreach ($b in $Dati) {
        $crc = $crc -bxor [long]$b
        for ($i = 0; $i -lt 8; $i++) {
            if ($crc -band 1) { $crc = ($crc -shr 1) -bxor $polinomio } else { $crc = $crc -shr 1 }
        }
    }
    return [uint32]($crc -bxor $tutti)
}
