#Requires -Version 5.1
<#
  mediabox — comando per gestire il media server (Windows).
  Scrivi "mediabox aiuto" per l'elenco dei comandi.
#>
param(
    [Parameter(Position = 0)][string]$Comando = "aiuto",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Altri = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$script:MediaboxDir = $PSScriptRoot
$script:EnvFile = Join-Path $PSScriptRoot ".env"
. (Join-Path $PSScriptRoot "lib\comune.ps1")
. (Join-Path $PSScriptRoot "lib\aggiorna.ps1")

function Mostra-Aiuto {
    @"
Uso: mediabox <comando>

  up                 accende tutti i servizi
  down               spegne tutti i servizi
  restart [servizio] riavvia tutto (o un solo servizio, es. "mediabox restart jellyfin")
  status             mostra quali servizi sono accesi
  logs [servizio]    mostra i messaggi dei servizi (Ctrl+C per uscire)
  impostazioni       cambia cosa usi dal terminale (più comodo dal browser: /impostazioni)
  azzera-jellyfin    rifà Jellyfin da zero con l'utente di mediabox (i film restano)
  configura          rigenera la configurazione dal file .env e la riapplica
  update             aggiorna mediabox e i servizi all'ultima versione (fa prima un backup)
  versione           mostra la versione installata e l'ultima disponibile
  backup             salva configurazioni e impostazioni nella cartella backup
  restore <file>     ripristina un backup
  remote             installa Tailscale per usare mediabox da fuori casa
  indirizzi          mostra gli indirizzi da aprire nel browser
"@ | Write-Host
}

function Controlla-Installazione {
    if (-not (Test-Path $script:EnvFile)) {
        Esci-ConErrore "mediabox non è ancora installato." "Fai doppio clic su installa.cmd."
    }
    Attendi-Docker
    if (-not (Test-Path (Join-Path $script:MediaboxDir "generato\docker-compose.yml"))) { Genera-File }
}

function Aggiorna-Ip {
    $ip = Trova-IpLocale
    if ($ip) { Scrivi-Env "HOST_IP" $ip $script:EnvFile }
    $ts = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (Test-Path $ts) {
        $tsIp = (Senza-Stderr { & $ts ip -4 2>$null } | Select-Object -First 1)
        if ($tsIp) { Scrivi-Env "TAILSCALE_IP" $tsIp $script:EnvFile }
    }
}

function Mostra-Stato {
    Scrivi-Titolo "Stato dei servizi"
    $righe = Invoca-Compose ps --all --format "{{.Service}}`t{{.State}}`t{{.Health}}" | Sort-Object
    foreach ($riga in $righe) {
        $parti = $riga -split "`t"
        $testo = switch ($parti[1]) {
            "running" { "acceso" }
            "restarting" { "si sta riavviando (controlla: mediabox logs $($parti[0]))" }
            { $_ -in "exited", "dead" } { "spento" }
            default { $parti[1] }
        }
        if ($parti.Count -gt 2 -and $parti[2] -eq "unhealthy") { $testo += " (non risponde bene)" }
        $colore = if ($testo -like "acceso*") { "Green" } else { "Yellow" }
        Write-Host ("  {0,-14} {1}" -f $parti[0], $testo) -ForegroundColor $colore
    }
}

function Esegui-Backup {
    Scrivi-Titolo "Backup"
    Scrivi-Info "Spengo i servizi per un minuto, così il backup è completo…"
    Senza-Stderr { Invoca-Compose stop *> $null }
    Invoca-Compose run --rm -T configuratore backup | Out-Host
    $esito = $LASTEXITCODE
    Senza-Stderr { Invoca-Compose start *> $null }
    Scrivi-Ok "Servizi riaccesi"
    return ($esito -eq 0)
}

function Esegui-Ripristino([string]$File) {
    if (-not $File) { Esci-ConErrore "Indica il file di backup." "Esempio: mediabox restore backup\mediabox-backup-2025-01-31_2100.tar.gz" }
    if (-not (Test-Path $File)) { Esci-ConErrore "Non trovo il file $File." }
    $assoluto = (Resolve-Path $File).Path
    $nome = Split-Path $assoluto -Leaf
    $cartellaBackup = Join-Path $script:MediaboxDir "backup"
    New-Item -ItemType Directory -Force -Path $cartellaBackup | Out-Null
    if ((Split-Path $assoluto -Parent) -ne $cartellaBackup) { Copy-Item $assoluto (Join-Path $cartellaBackup $nome) -Force }
    if (-not (Chiedi-SiNo "Il ripristino sostituisce le configurazioni attuali (non i film). Continuo?" $false)) { return }
    Senza-Stderr { Invoca-Compose stop *> $null }
    Invoca-Compose run --rm -T configuratore ripristina "backup/$nome" | Out-Host
    Aggiorna-Ip
    [void](Applica-Configurazione)
}

function Attiva-Remoto {
    Scrivi-Titolo "Accesso da fuori casa con Tailscale"
    $ts = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
    if (-not (Test-Path $ts)) {
        Scrivi-Info "Installo Tailscale…"
        & winget install -e --id Tailscale.Tailscale --accept-package-agreements --accept-source-agreements --silent
        if (-not (Test-Path $ts)) { Esci-ConErrore "Non sono riuscito a installare Tailscale." "Scaricalo da tailscale.com e riprova." }
    }
    Senza-Stderr { & $ts ip -4 *> $null }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Si apre il browser: accedi (o crea un account gratuito) e torna qui."
        & $ts up
    }
    $ip = (& $ts ip -4 | Select-Object -First 1)
    Scrivi-Env "TAILSCALE_IP" $ip $script:EnvFile
    Scrivi-Ok "Tailscale attivo. Indirizzo di mediabox da fuori casa: http://$ip"
    Write-Host "  Sul telefono installa l'app Tailscale, accedi con lo stesso account e apri"
    Write-Host "  http://$ip nel browser (o http://$ip/jellyfin nell'app Jellyfin)."
    Write-Host "  Non serve aprire nessuna porta sul router."
}

# Le impostazioni salvate dal browser (/impostazioni)
function Applica-ImpostazioniWeb {
    $flag = Join-Path $script:MediaboxDir ".da-applicare"
    # "Aggiorna ora" dalla pagina delle impostazioni
    $aggiorna = Join-Path $script:MediaboxDir ".da-aggiornare"
    if (Test-Path $aggiorna) {
        Remove-Item $aggiorna, $flag -Force -ErrorAction SilentlyContinue
        Scrivi-IntestazioneRegistro
        $registro = Join-Path $script:MediaboxDir "aggiornamenti.log"
        Senza-Stderr { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:MediaboxDir "mediabox.ps1") update *>> $registro }
        return
    }
    if (-not (Test-Path $flag)) { return }
    $cartellaPrima = (Get-Content -LiteralPath $flag -Raw -ErrorAction SilentlyContinue)
    if ($cartellaPrima) { $cartellaPrima = $cartellaPrima.Trim() }
    Remove-Item $flag -Force
    $ok = Con-Registro {
        Sposta-Media $cartellaPrima (Leggi-Env "MEDIA_DIR" $script:EnvFile)
        New-Item -ItemType Directory -Force -Path ((Leggi-Env "MEDIA_DIR" $script:EnvFile) -replace "/", "\") | Out-Null
        Aggiorna-Ip
        Applica-Configurazione
    }
    if (-not $ok) { exit 1 }
}

switch ($Comando.ToLower()) {
    { $_ -in "aiuto", "help", "-h", "--help" } { Mostra-Aiuto }
    "up" { Controlla-Installazione; Invoca-Compose up -d --remove-orphans; Scrivi-Ok "Servizi accesi" }
    "down" { Controlla-Installazione; Invoca-Compose down; Scrivi-Ok "Servizi spenti" }
    "restart" { Controlla-Installazione; Invoca-Compose restart @Altri; Scrivi-Ok "Riavviato" }
    "status" { Controlla-Installazione; Mostra-Stato }
    "logs" { Controlla-Installazione; Invoca-Compose logs -f --tail 200 @Altri }
    "configura" { Controlla-Installazione; Aggiorna-Ip; [void](Applica-Configurazione) }
    "azzera-jellyfin" {
        Controlla-Installazione
        Scrivi-Titolo "Rifaccio Jellyfin da zero"
        Scrivi-Info "Tolgo utenti e impostazioni di Jellyfin (i film nelle cartelle restano dove sono)…"
        Senza-Stderr { Invoca-Compose rm -sf jellyfin *> $null }
        Senza-Stderr { & docker volume rm mediabox_jellyfin_config *> $null }
        Scrivi-Ok "Jellyfin azzerato: ora lo riconfiguro con l'utente di mediabox"
        Aggiorna-Ip
        if (-not (Con-Registro { Applica-Configurazione })) { exit 1 }
    }
    "applica-impostazioni" { Controlla-Installazione; Applica-ImpostazioniWeb }
    "controlla-impostazioni" {
        # avviato all'accesso a Windows (nascosto): applica le modifiche fatte nel browser
        while ($true) {
            if ((Test-Path (Join-Path $script:MediaboxDir ".da-applicare")) -or (Test-Path (Join-Path $script:MediaboxDir ".da-aggiornare"))) {
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:MediaboxDir "mediabox.ps1") applica-impostazioni
            }
            Start-Sleep -Seconds 5
        }
    }
    "impostazioni" {
        Controlla-Installazione; Costruisci-Configuratore
        Scrivi-Info "Le stesse scelte le trovi anche nel browser: $(Indirizzo-Principale)/impostazioni/"
        $cartellaPrima = Leggi-Env "MEDIA_DIR" $script:EnvFile
        $esito = Mostra-MenuImpostazioni
        if ($esito -eq 0) {
            Sposta-Media $cartellaPrima (Leggi-Env "MEDIA_DIR" $script:EnvFile)
            Aggiorna-Ip; [void](Applica-Configurazione)
        }
        elseif ($esito -ne 3) { exit $esito }
    }
    "update" {
        Controlla-Installazione
        if (Aggiorna-Mediabox) {
            # il nuovo codice va eseguito dal nuovo script
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:MediaboxDir "mediabox.ps1") aggiorna-servizi
            exit $LASTEXITCODE
        }
    }
    "aggiorna-servizi" {
        Controlla-Installazione
        if (-not (Aggiorna-Servizi)) { exit 1 }
    }
    "aggiorna-automatico" {
        # avviato ogni notte dall'attività pianificata
        Controlla-Installazione
        if ((Leggi-Env "AGGIORNAMENTI_AUTOMATICI" $script:EnvFile) -eq "no") { exit 0 }
        Scrivi-IntestazioneRegistro
        $registro = Join-Path $script:MediaboxDir "aggiornamenti.log"
        Senza-Stderr { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $script:MediaboxDir "mediabox.ps1") update *>> $registro }
        $fase = ""
        try { $fase = (Get-Content (Join-Path $script:MediaboxDir "aggiornamento.stato") -Raw | ConvertFrom-Json).fase } catch { $fase = "" }
        if ($fase -eq "fatto") { Invia-Ntfy "mediabox aggiornato alla versione $(Leggi-VersioneInstallata)." }
        elseif ($fase -eq "errore") { Invia-Ntfy "L'aggiornamento di mediabox non è riuscito: apri le impostazioni (/impostazioni) per vedere perché." }
    }
    "versione" {
        Controlla-Installazione
        Write-Host "  Versione installata: $(Leggi-VersioneInstallata)"
        Costruisci-Configuratore
        [void](Invoca-Aggiornamento "controlla")
    }
    "backup" { Controlla-Installazione; [void](Esegui-Backup) }
    "restore" { Controlla-Installazione; Esegui-Ripristino ($Altri | Select-Object -First 1) }
    "remote" { Controlla-Installazione; Attiva-Remoto; Genera-File | Out-Null }
    "indirizzi" {
        Controlla-Installazione; Aggiorna-Ip; Costruisci-Configuratore
        & docker run --rm -v "$($script:MediaboxDir):/mediabox" mediabox/configuratore:locale indirizzi
    }
    default { Scrivi-Avviso "Comando sconosciuto: $Comando"; Mostra-Aiuto; exit 1 }
}
