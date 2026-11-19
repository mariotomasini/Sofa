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

function Mostra-Aiuto {
    @"
Uso: mediabox <comando>

  up                 accende tutti i servizi
  down               spegne tutti i servizi
  restart [servizio] riavvia tutto (o un solo servizio, es. "mediabox restart jellyfin")
  status             mostra quali servizi sono accesi
  logs [servizio]    mostra i messaggi dei servizi (Ctrl+C per uscire)
  impostazioni       cambia cosa usi: serie, anime, musica, sottotitoli, notifiche
  configura          rigenera la configurazione dal file .env e la riapplica
  update             aggiorna i servizi all'ultima versione (fa prima un backup)
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
        $tsIp = (& $ts ip -4 2>$null | Select-Object -First 1)
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
    Invoca-Compose stop *> $null
    Invoca-Compose run --rm -T configuratore backup | Out-Host
    $esito = $LASTEXITCODE
    Invoca-Compose start *> $null
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
    Invoca-Compose stop *> $null
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
    & $ts ip -4 *> $null
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

switch ($Comando.ToLower()) {
    { $_ -in "aiuto", "help", "-h", "--help" } { Mostra-Aiuto }
    "up" { Controlla-Installazione; Invoca-Compose up -d --remove-orphans; Scrivi-Ok "Servizi accesi" }
    "down" { Controlla-Installazione; Invoca-Compose down; Scrivi-Ok "Servizi spenti" }
    "restart" { Controlla-Installazione; Invoca-Compose restart @Altri; Scrivi-Ok "Riavviato" }
    "status" { Controlla-Installazione; Mostra-Stato }
    "logs" { Controlla-Installazione; Invoca-Compose logs -f --tail 200 @Altri }
    "configura" { Controlla-Installazione; Aggiorna-Ip; [void](Applica-Configurazione) }
    "impostazioni" {
        Controlla-Installazione; Costruisci-Configuratore
        $esito = Mostra-MenuImpostazioni
        if ($esito -eq 0) { Aggiorna-Ip; [void](Applica-Configurazione) }
        elseif ($esito -ne 3) { exit $esito }
    }
    "update" {
        Controlla-Installazione
        [void](Esegui-Backup)
        Scrivi-Titolo "Aggiornamento"
        Invoca-Compose pull -q --ignore-buildable
        Invoca-Compose build -q --pull cerca
        if (Applica-Configurazione) { Scrivi-Ok "mediabox aggiornato" }
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
