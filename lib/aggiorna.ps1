# Aggiornamento di mediabox da GitHub e spostamento dei media (Windows).
# Incluso da install.ps1 e mediabox.ps1 dopo lib\comune.ps1.

function Leggi-VersioneInstallata {
    $file = Join-Path $script:MediaboxDir "VERSION"
    if (Test-Path $file) { return (Get-Content $file -Raw).Trim() }
    return "0.0.0"
}

function Converti-Versione([string]$Testo) {
    $pulito = ($Testo -replace "^v", "") -replace "[^0-9.].*$", ""
    try { return [version]$pulito } catch { return [version]"0.0" }
}

function Trova-UltimaRelease {
    $repo = Leggi-Env "MEDIABOX_REPO" $script:EnvFile
    if (-not $repo) { $repo = "mariotomasini/Sofa" }
    $api = $env:MEDIABOX_GITHUB_API
    if (-not $api) { $api = "https://api.github.com" }
    try {
        $dati = Invoke-RestMethod -Uri "$api/repos/$repo/releases/latest" -Headers @{ Accept = "application/vnd.github+json" } -TimeoutSec 30
        return @{ Tag = $dati.tag_name; Archivio = $dati.zipball_url }
    } catch { return $null }
}

function Test-PiuNuova([string]$Nuova, [string]$Attuale) {
    return (Converti-Versione $Nuova) -gt (Converti-Versione $Attuale)
}

function Invia-Ntfy([string]$Messaggio) {
    $topic = Leggi-Env "NTFY_TOPIC" $script:EnvFile
    if (-not $topic) { return }
    $server = Leggi-Env "NTFY_SERVER" $script:EnvFile
    if (-not $server) { $server = "https://ntfy.sh" }
    try { Invoke-RestMethod -Method Post -Uri "$server/$topic" -Body $Messaggio -Headers @{ Title = "mediabox" } -TimeoutSec 20 | Out-Null }
    catch { $null = $_ }  # una notifica mancata non deve fermare l'aggiornamento
}

# Le versioni delle immagini seguono quelle nuove, salvo quelle cambiate a mano nel .env
function Aggiorna-VersioniImmagini([string]$VecchioEsempio) {
    foreach ($riga in (Get-Content (Join-Path $script:MediaboxDir ".env.example") | Where-Object { $_ -match "^IMG_[A-Z_]+=" })) {
        $chiave, $nuovo = $riga -split "=", 2
        $attuale = Leggi-Env $chiave $script:EnvFile
        if (-not $attuale -or $attuale -eq (Leggi-Env $chiave $VecchioEsempio)) { Scrivi-Env $chiave $nuovo $script:EnvFile }
    }
}

# Scarica e installa il nuovo codice. Restituisce $true se c'era una versione nuova.
function Aggiorna-Codice {
    $release = Trova-UltimaRelease
    if (-not $release) { Scrivi-Info "Nessuna versione pubblicata su GitHub (o GitHub non risponde)."; return $false }
    $installata = Leggi-VersioneInstallata
    if (-not (Test-PiuNuova $release.Tag $installata)) { Scrivi-Ok "mediabox è già all'ultima versione ($installata)"; return $false }
    Scrivi-Titolo "Nuova versione di mediabox: $($release.Tag) (installata: $installata)"
    $temporanea = Join-Path $env:TEMP "mediabox-aggiornamento"
    Remove-Item $temporanea -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $temporanea | Out-Null
    $zip = Join-Path $temporanea "mediabox.zip"
    Invoke-WebRequest -Uri $release.Archivio -OutFile $zip -UseBasicParsing -TimeoutSec 300
    Expand-Archive -Path $zip -DestinationPath (Join-Path $temporanea "nuova") -Force
    $sorgente = Get-ChildItem (Join-Path $temporanea "nuova") -Directory | Select-Object -First 1
    if (-not $sorgente -or -not (Test-Path (Join-Path $sorgente.FullName "mediabox.ps1"))) {
        Esci-ConErrore "L'archivio scaricato non sembra mediabox." "Controlla MEDIABOX_REPO nel file .env."
    }
    $vecchioEsempio = Join-Path $temporanea "vecchio.env.example"
    Copy-Item (Join-Path $script:MediaboxDir ".env.example") $vecchioEsempio
    & robocopy $sorgente.FullName $script:MediaboxDir /E /NFL /NDL /NJH /NJS /NP /XD .git generato backup /XF .env | Out-Null
    Aggiorna-VersioniImmagini $vecchioEsempio
    Remove-Item $temporanea -Recurse -Force -ErrorAction SilentlyContinue
    Scrivi-Ok "Codice aggiornato a $($release.Tag)"
    return $true
}

# Attività pianificata: ogni notte controlla se è uscita una nuova versione
function Installa-AggiornamentiAutomatici {
    # L'attività c'è sempre: se gli aggiornamenti sono spenti nelle impostazioni, non fa nulla
    $nome = "mediabox - aggiornamento automatico"
    $script = Join-Path $script:MediaboxDir "mediabox.ps1"
    $azione = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" aggiorna-automatico"
    $quando = New-ScheduledTaskTrigger -Daily -At "04:20" -RandomDelay (New-TimeSpan -Minutes 40)
    $impostazioni = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries
    $chi = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $nome -Action $azione -Trigger $quando -Settings $impostazioni -Principal $chi -Force | Out-Null
    if ((Leggi-Env "AGGIORNAMENTI_AUTOMATICI" $script:EnvFile) -eq "no") {
        Scrivi-Info "Aggiornamenti automatici spenti (si accendono dalle impostazioni)"
    } else {
        Scrivi-Ok "Aggiornamenti automatici attivi (ogni notte verso le 4:30)"
    }
}

# Le impostazioni salvate dal browser si applicano da sole: un controllo nascosto,
# avviato all'accesso a Windows, guarda il file .da-applicare
function Installa-ControlloImpostazioni {
    $nome = "mediabox - impostazioni dal browser"
    $script = Join-Path $script:MediaboxDir "mediabox.ps1"
    $azione = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" controlla-impostazioni"
    $quando = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $impostazioni = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    $chi = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $nome -Action $azione -Trigger $quando -Settings $impostazioni -Principal $chi -Force | Out-Null
    Start-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue
    Scrivi-Ok "Impostazioni dal browser pronte: $(Indirizzo-Principale)/impostazioni/"
}

# Sposta film, musica e download quando cambia MEDIA_DIR (servizi spenti)
function Sposta-Media([string]$Vecchia, [string]$Nuova) {
    if (-not $Vecchia -or -not $Nuova -or $Vecchia -eq $Nuova) { return }
    $da = $Vecchia -replace "/", "\"
    $a = $Nuova -replace "/", "\"
    Scrivi-Titolo "Sposto film, musica e download"
    Scrivi-Info "Da $da a $a (con tanti film può richiedere molto tempo: non spegnere il PC)"
    Invoca-Compose stop *> $null
    New-Item -ItemType Directory -Force -Path $a | Out-Null
    if (Test-Path $da) {
        & robocopy $da $a /E /MOVE /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Esci-ConErrore "Lo spostamento in $a non è riuscito (spazio finito?)." "I file rimasti sono ancora in $da. Libera spazio e riprova."
        }
    }
    Scrivi-Ok "File spostati in $a"
}
