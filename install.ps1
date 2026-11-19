#Requires -Version 5.1
<#
  mediabox — installer per Windows 10 (22H2) e Windows 11.
  Avvialo con un doppio clic su "installa.cmd". Si può rilanciare quando si vuole.
  Usa Docker Desktop (con WSL2): su Windows non sono disponibili la
  transcodifica hardware e i tuner USB (vedi README).
#>
[CmdletBinding()]
param(
    [switch]$WithSonarr,
    [switch]$WithAnime,
    [switch]$WithLidarr,
    [string]$Cartella = "C:\Mediabox"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

. (Join-Path $PSScriptRoot "lib\comune.ps1")
. (Join-Path $PSScriptRoot "lib\aggiorna.ps1")

# --- Permessi di amministratore --------------------------------------------------------
$identita = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identita.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argomenti = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Cartella", "`"$Cartella`"")
    if ($WithSonarr) { $argomenti += "-WithSonarr" }
    if ($WithAnime) { $argomenti += "-WithAnime" }
    if ($WithLidarr) { $argomenti += "-WithLidarr" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argomenti
    exit 0
}

$script:PausaSuErrore = $true
# Tutto quello che compare nella finestra finisce anche in un registro, utile se qualcosa va storto
$script:RegistroInstaller = Join-Path $env:TEMP "mediabox-installa.log"
try { Start-Transcript -Path $script:RegistroInstaller -Append | Out-Null } catch { $script:RegistroInstaller = "" }
# Un errore imprevisto non deve chiudere la finestra senza spiegazioni
trap {
    Write-Host ""
    Write-Host "  × Errore imprevisto: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "    Riga $($_.InvocationInfo.ScriptLineNumber): $($_.InvocationInfo.Line.Trim())"
    if ($script:RegistroInstaller) { Write-Host "    Il registro completo è in: $($script:RegistroInstaller)" }
    Write-Host "    Rilancia installa.cmd: riprende da dove era arrivato."
    Read-Host "  Premi Invio per chiudere" | Out-Null
    exit 1
}
$script:MediaboxDir = $Cartella
$script:EnvFile = Join-Path $Cartella ".env"
$riavvioNecessario = $false

# --- 1. Controlli ------------------------------------------------------------------------
Scrivi-Titolo "Controllo il sistema"
if (-not [Environment]::Is64BitOperatingSystem) { Esci-ConErrore "Serve Windows a 64 bit." }
$build = [Environment]::OSVersion.Version.Build
if ($build -lt 19045) {
    Esci-ConErrore "Questa versione di Windows è troppo vecchia (build $build)." `
        "Aggiorna a Windows 10 22H2 o Windows 11 da Impostazioni → Windows Update, poi rilancia."
}
Scrivi-Ok "Windows supportato (build $build)"

# --- 2. Copia dei file -----------------------------------------------------------------------
if ((Resolve-Path $PSScriptRoot).Path.TrimEnd("\") -ne $Cartella.TrimEnd("\")) {
    Scrivi-Info "Copio mediabox in $Cartella…"
    New-Item -ItemType Directory -Force -Path $Cartella | Out-Null
    & robocopy $PSScriptRoot $Cartella /E /NFL /NDL /NJH /NJS /NP /XD .git generato backup __pycache__ .pytest_cache .ruff_cache /XF .env | Out-Null
    if ($LASTEXITCODE -ge 8) { Esci-ConErrore "Non riesco a copiare i file in $Cartella." }
    Scrivi-Ok "File copiati in $Cartella"
}

# --- 3. WSL2 e Docker Desktop ----------------------------------------------------------------
Scrivi-Titolo "Installo quello che serve"
& wsl.exe --status *> $null
if ($LASTEXITCODE -ne 0) {
    Scrivi-Info "Attivo WSL2 (il sottosistema Linux di Windows)…"
    & wsl.exe --install --no-distribution
    if ($LASTEXITCODE -ne 0) {
        Esci-ConErrore "Non riesco ad attivare WSL2." `
            "Controlla nel BIOS che la virtualizzazione (Intel VT-x) sia attiva, poi rilancia l'installer."
    }
    # Dopo il riavvio l'installazione riparte da sola
    $comando = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Cartella 'install.ps1')`" -Cartella `"$Cartella`""
    if ($WithSonarr) { $comando += " -WithSonarr" }
    if ($WithAnime) { $comando += " -WithAnime" }
    if ($WithLidarr) { $comando += " -WithLidarr" }
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "mediabox" -Value $comando
    Scrivi-Avviso "Serve un riavvio del PC: lo faccio tra 2 minuti. Salva quello che hai aperto."
    Scrivi-Info "Dopo il riavvio accedi a Windows e accetta la richiesta di permessi: l'installazione continua da sola."
    Scrivi-Info "Per rimandare il riavvio, in un'altra finestra scrivi: shutdown /a"
    & shutdown.exe /r /t 120 /c "mediabox: riavvio per attivare WSL2. L'installazione continua dopo l'accesso."
    # la finestra resta aperta fino al riavvio, così l'avviso si legge
    for ($secondi = 120; $secondi -gt 0; $secondi -= 10) {
        Write-Host "  Riavvio tra $secondi secondi…"
        Start-Sleep -Seconds 10
    }
    exit 0
}
Scrivi-Ok "WSL2 attivo"

Aggiungi-DockerAlPath
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Esci-ConErrore "Manca winget (Programma di installazione app)." `
            "Installalo dal Microsoft Store cercando 'Programma di installazione app', poi rilancia."
    }
    Scrivi-Info "Installo Docker Desktop (alcuni minuti)…"
    & winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { Esci-ConErrore "Non riesco a installare Docker Desktop." "Controlla la connessione a Internet e riprova." }
    Aggiungi-DockerAlPath
}
Attendi-Docker
Scrivi-Ok "Docker Desktop pronto"

# Docker Desktop parte da solo all'accesso a Windows
foreach ($nome in @("settings-store.json", "settings.json")) {
    $file = Join-Path $env:APPDATA "Docker\$nome"
    if (Test-Path $file) {
        try {
            $json = Get-Content $file -Raw | ConvertFrom-Json
            $campo = if ($nome -eq "settings-store.json") { "AutoStart" } else { "autoStart" }
            $json | Add-Member -NotePropertyName $campo -NotePropertyValue $true -Force
            [System.IO.File]::WriteAllText($file, ($json | ConvertTo-Json -Depth 20), $Utf8SenzaBom)
        } catch { Scrivi-Avviso "Non riesco a impostare l'avvio automatico di Docker Desktop: attivalo dalle sue impostazioni." }
    }
}

# --- 4. File .env ---------------------------------------------------------------------------
Scrivi-Titolo "Preparo la configurazione"
if (-not (Test-Path $script:EnvFile)) { Copy-Item (Join-Path $Cartella ".env.example") $script:EnvFile }
foreach ($chiave in @("RADARR_API_KEY", "PROWLARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY", "IMPOSTAZIONI_SEGRETO")) {
    if (-not (Leggi-Env $chiave $script:EnvFile)) { Scrivi-Env $chiave (Nuova-ChiaveCasuale) $script:EnvFile }
}
if ($WithSonarr) { Scrivi-Env "WITH_SONARR" "si" $script:EnvFile }
if ($WithAnime) { Scrivi-Env "WITH_ANIME" "si" $script:EnvFile }
if ($WithLidarr) { Scrivi-Env "WITH_LIDARR" "si" $script:EnvFile }
Scrivi-Env "PIATTAFORMA" "windows" $script:EnvFile
Scrivi-Env "PUID" "1000" $script:EnvFile
Scrivi-Env "PGID" "1000" $script:EnvFile
Scrivi-Env "VAAPI" "no" $script:EnvFile
Scrivi-Env "DVB" "no" $script:EnvFile
$ip = Trova-IpLocale
Scrivi-Env "HOST_IP" $ip $script:EnvFile
Scrivi-Ok "File di configurazione pronto"

# --- 5. Hardware e rete -----------------------------------------------------------------------
Scrivi-Titolo "Rilevo l'hardware e la rete"
Scrivi-Info "Su Windows la transcodifica usa il processore e i tuner USB non sono supportati (limiti di Docker Desktop)."
Costruisci-Configuratore
$hdhr = Trova-HDHomeRun
if ($hdhr) { Scrivi-Env "HDHOMERUN_IP" $hdhr $script:EnvFile; Scrivi-Ok "HDHomeRun trovato in rete: $hdhr" }
else { Scrivi-Info "Nessun HDHomeRun in rete" }

# Porta 80 occupata da un altro programma (es. IIS)? Allora 8080, senza chiedere
$porta = Leggi-Env "HTTP_PORT" $script:EnvFile
if (-not $porta) { $porta = "80" }
$occupata = Get-NetTCPConnection -LocalPort ([int]$porta) -State Listen -ErrorAction SilentlyContinue |
    Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName -notmatch "docker|wslrelay|vpnkit|com.docker" }
if ($occupata) {
    Scrivi-Avviso "La porta $porta è già usata da un altro programma: uso la 8080."
    Scrivi-Env "HTTP_PORT" "8080" $script:EnvFile
    $porta = "8080"
}
# Windows annuncia in rete solo il proprio nome: "nomedelpc.local" (mediabox.local se si chiama MEDIABOX)
Scrivi-Env "NOME_HOST" "$($env:COMPUTERNAME.ToLower()).local" $script:EnvFile

# Firewall: solo reti private (casa), mai reti pubbliche
$regole = @(
    @{ Nome = "mediabox (sito)"; Porta = $porta; Protocollo = "TCP" },
    @{ Nome = "mediabox (torrent TCP)"; Porta = (Leggi-Env "TORRENT_PORT" $script:EnvFile); Protocollo = "TCP" },
    @{ Nome = "mediabox (torrent UDP)"; Porta = (Leggi-Env "TORRENT_PORT" $script:EnvFile); Protocollo = "UDP" }
)
foreach ($r in $regole) {
    Get-NetFirewallRule -DisplayName $r.Nome -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $r.Nome -Direction Inbound -Action Allow -Protocol $r.Protocollo `
        -LocalPort $r.Porta -Profile Private, Domain | Out-Null
}
Scrivi-Ok "Firewall pronto (solo rete di casa)"

# Comando "mediabox" disponibile in qualsiasi finestra
$percorsoMacchina = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($percorsoMacchina -notlike "*$Cartella*") {
    [Environment]::SetEnvironmentVariable("Path", "$percorsoMacchina;$Cartella", "Machine")
}
Scrivi-Ok "Comando 'mediabox' installato (funziona nelle nuove finestre di PowerShell o Prompt)"

# --- 6. Configurazione dal browser --------------------------------------------------------------
# (la password la scrive solo la procedura: se manca, la procedura non è stata fatta)
if (-not (Leggi-Env "ADMIN_PASSWORD" $script:EnvFile)) {
    $pubbliche = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq "Public" })
    if ($pubbliche) {
        # altrimenti il telefono non raggiunge la pagina di configurazione
        Scrivi-Info "La rete '$($pubbliche[0].Name)' era 'Pubblica': la imposto come rete di casa ('Privata') per la configurazione."
        $pubbliche | Set-NetConnectionProfile -NetworkCategory Private
    }
    Scrivi-Dischi
    Avvia-ProceduraWeb
    $suffisso = if ($porta -eq "80") { "" } else { ":$porta" }
    Scrivi-Titolo "Continua dal browser"
    Write-Host ""
    Write-Host "  Apri questo indirizzo da questo PC o dal telefono collegato alla stessa rete:"
    Write-Host ""
    Write-Host "      http://$($ip)$suffisso" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Lì scegli la password, cosa guardare e dove salvare i film."
    Write-Host "  Puoi seguire l'installazione dal browser: questa finestra puoi anche ignorarla."
    Write-Host ""
    Start-Process "http://localhost$suffisso/impostazioni/" -ErrorAction SilentlyContinue
    Attendi-ProceduraWeb
}

# Le scelte di Windows fatte nella procedura
$riavvioNecessario = $false
if ((Leggi-Env "WIN_RINOMINA_PC" $script:EnvFile) -ne "no" -and $env:COMPUTERNAME -ne "MEDIABOX") {
    Rename-Computer -NewName "MEDIABOX" -Force -WarningAction SilentlyContinue
    Scrivi-Env "NOME_HOST" "mediabox.local" $script:EnvFile
    $riavvioNecessario = $true
    Scrivi-Ok "Il PC si chiamerà MEDIABOX dopo il riavvio"
}
if ((Leggi-Env "WIN_RETE_PRIVATA" $script:EnvFile) -eq "no") {
    Scrivi-Info "Rete lasciata com'era"
} else {
    Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq "Public" } |
        Set-NetConnectionProfile -NetworkCategory Private
}
if ((Leggi-Env "WIN_NO_SOSPENSIONE" $script:EnvFile) -ne "no") {
    & powercfg /change standby-timeout-ac 0
    & powercfg /change hibernate-timeout-ac 0
    Scrivi-Ok "Sospensione disattivata (con la corrente)"
}

$mediaDir = Leggi-Env "MEDIA_DIR" $script:EnvFile
New-Item -ItemType Directory -Force -Path ($mediaDir -replace "/", "\") | Out-Null
Scrivi-Ok "Film, serie e download in: $($mediaDir -replace '/', '\')"
Installa-AggiornamentiAutomatici
Installa-ControlloImpostazioni

# --- 7. Avvio e configurazione automatica ---------------------------------------------------
$tuttoBene = Con-Registro { Applica-Configurazione }

Scrivi-Titolo "Ultima cosa: accensione automatica"
Write-Host "  Docker Desktop parte quando entri in Windows. Perché mediabox si accenda da solo"
Write-Host "  dopo un'interruzione di corrente, attiva l'accesso automatico a Windows:"
Write-Host "  premi Win+R, scrivi netplwiz, togli la spunta 'Per utilizzare questo computer è"
Write-Host "  necessario che l'utente immetta il nome utente e la password' (dettagli nel README)."

if ($riavvioNecessario) {
    Scrivi-Avviso "Il PC è stato rinominato in MEDIABOX: riavvialo quando vuoi perché http://mediabox.local funzioni."
}
if (-not $tuttoBene) {
    Scrivi-Avviso "Qualcosa non è andato: leggi 'Cosa non è andato' qui sopra (o nel browser). Spesso basta scrivere: mediabox configura"
}
Write-Host ""
Write-Host "  Fatto."
Read-Host "  Premi Invio per chiudere questa finestra" | Out-Null
