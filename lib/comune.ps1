# Funzioni comuni a install.ps1 e mediabox.ps1 (Windows).
# Questo file viene incluso con ". lib\comune.ps1": non va eseguito da solo.

$Utf8SenzaBom = New-Object System.Text.UTF8Encoding $false

function Scrivi-Titolo([string]$Testo) { Write-Host ""; Write-Host "== $Testo ==" -ForegroundColor Cyan }
function Scrivi-Info([string]$Testo)   { Write-Host "  · $Testo" }
function Scrivi-Ok([string]$Testo)     { Write-Host "  ✔ $Testo" -ForegroundColor Green }
function Scrivi-Avviso([string]$Testo) { Write-Host "  ! $Testo" -ForegroundColor Yellow }

# Mostra un errore con "cosa fare" ed esce
function Esci-ConErrore([string]$Cosa, [string]$CosaFare = "") {
    Write-Host "  ✘ $Cosa" -ForegroundColor Red
    if ($CosaFare) { Write-Host "    Cosa fare: $CosaFare" }
    Write-Host ""
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

function Attendi-Docker([int]$Secondi = 300) {
    Aggiungi-DockerAlPath
    $exe = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) { return }
    if (Test-Path $exe) { Start-Process $exe | Out-Null }
    Scrivi-Info "Avvio Docker Desktop (la prima volta può chiederti di accettare le condizioni d'uso)…"
    $scadenza = (Get-Date).AddSeconds($Secondi)
    while ((Get-Date) -lt $scadenza) {
        Start-Sleep -Seconds 5
        & docker info *> $null
        if ($LASTEXITCODE -eq 0) { return }
    }
    Esci-ConErrore "Docker Desktop non risponde." `
        "Apri Docker Desktop dal menu Start, accetta le condizioni se richiesto, aspetta che diventi verde e rilancia."
}

function Invoca-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Argomenti)
    $generato = Join-Path $script:MediaboxDir "generato"
    & docker compose --project-directory $generato -f (Join-Path $generato "docker-compose.yml") @Argomenti
}

function Costruisci-Configuratore {
    $base = Leggi-Env "IMG_PYTHON" $script:EnvFile
    if (-not $base) { $base = "docker.io/library/python:3.13-slim" }
    Scrivi-Info "Preparo il configuratore…"
    & docker build -q --build-arg "BASE_IMAGE=$base" -t mediabox/configuratore:locale (Join-Path $script:MediaboxDir "configuratore") | Out-Null
    if ($LASTEXITCODE -ne 0) { Esci-ConErrore "Non riesco a preparare il configuratore." "Controlla la connessione a Internet e riprova." }
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
        Invoca-Compose restart $servizio *> $null
    }
    Remove-Item $file -Force
}

# Tutto il ciclo: genera → prepara → avvia → configura via API. Restituisce $true se tutto è andato bene.
function Applica-Configurazione {
    Genera-File
    Scrivi-Titolo "Scarico e avvio i servizi (la prima volta può richiedere diversi minuti)"
    Invoca-Compose build -q cerca | Out-Host
    if ($LASTEXITCODE -ne 0) { Esci-ConErrore "Non riesco a preparare Cerca." "Controlla la connessione a Internet e riprova." }
    Invoca-Compose pull -q --ignore-buildable | Out-Host
    Invoca-Compose run --rm -T configuratore prepara | Out-Host
    Invoca-Compose up -d --remove-orphans | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Esci-ConErrore "Alcuni servizi non sono partiti." "Guarda i messaggi qui sopra; spesso basta riprovare con: mediabox configura"
    }
    Riavvia-Segnati
    Scrivi-Ok "Servizi avviati"
    Invoca-Compose run --rm -T configuratore configura | Out-Host
    return ($LASTEXITCODE -eq 0)
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
