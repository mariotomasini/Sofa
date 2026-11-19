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
    [string]$Cartella = "C:\Mediabox"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

. (Join-Path $PSScriptRoot "lib\comune.ps1")

# --- Permessi di amministratore --------------------------------------------------------
$identita = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identita.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $argomenti = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Cartella", "`"$Cartella`"")
    if ($WithSonarr) { $argomenti += "-WithSonarr" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argomenti
    exit 0
}

$script:PausaSuErrore = $true
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
    Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "mediabox" -Value $comando
    Scrivi-Avviso "Serve un riavvio del PC. Dopo il riavvio e l'accesso, l'installazione continua da sola."
    if (Chiedi-SiNo "Riavvio adesso?" $true) { Restart-Computer -Force }
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

# --- 4. File .env e domande -----------------------------------------------------------------------
Scrivi-Titolo "Configurazione"
$nuovaInstallazione = -not (Test-Path $script:EnvFile)
if ($nuovaInstallazione) { Copy-Item (Join-Path $Cartella ".env.example") $script:EnvFile }

function Test-ChiaveTmdb([string]$Chiave) {
    try {
        if ($Chiave.Length -gt 40) {
            Invoke-RestMethod -Uri "https://api.themoviedb.org/3/configuration" -Headers @{ Authorization = "Bearer $Chiave" } | Out-Null
        } else {
            Invoke-RestMethod -Uri "https://api.themoviedb.org/3/configuration?api_key=$Chiave" | Out-Null
        }
        return "ok"
    } catch {
        if ($_.Exception.Response) { return "rifiutata" }
        return "sconosciuto"
    }
}

if (-not (Leggi-Env "TMDB_API_KEY" $script:EnvFile)) {
    Write-Host ""
    Write-Host "  Serve una chiave gratuita di TMDB (il catalogo dei film)."
    Write-Host "  La trovi su themoviedb.org → il tuo account → Impostazioni → API."
    while ($true) {
        $chiave = (Read-Host "  ? Incolla la chiave TMDB") -replace "\s", ""
        if (-not $chiave) { Write-Host "    La chiave non può essere vuota."; continue }
        $esito = Test-ChiaveTmdb $chiave
        if ($esito -eq "ok") { Scrivi-Ok "Chiave TMDB valida"; break }
        if ($esito -eq "sconosciuto") { Scrivi-Avviso "Non riesco a verificarla (manca Internet?). La salvo comunque."; break }
        Write-Host "    TMDB non accetta questa chiave. Controlla di averla copiata tutta e riprova."
    }
    Scrivi-Env "TMDB_API_KEY" $chiave $script:EnvFile
}

if (-not (Leggi-Env "ADMIN_PASSWORD" $script:EnvFile)) {
    Write-Host ""
    Write-Host "  Scegli una password di amministrazione: vale per Jellyfin e per i pannelli."
    Write-Host '  Almeno 8 caratteri, senza virgolette doppie (") e senza barra rovesciata (\).'
    while ($true) {
        $p1 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host "  ? Password" -AsSecureString)))
        $p2 = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR((Read-Host "  ? Scrivila di nuovo" -AsSecureString)))
        if ($p1 -ne $p2) { Write-Host "    Le due password non coincidono, riprova."; continue }
        if ($p1.Length -lt 8) { Write-Host "    Troppo corta: almeno 8 caratteri."; continue }
        if ($p1.Contains('"') -or $p1.Contains('\')) { Write-Host "    Togli le virgolette doppie e la barra rovesciata."; continue }
        if ([Text.Encoding]::UTF8.GetByteCount($p1) -gt 72) { Write-Host "    Troppo lunga: al massimo 72 caratteri."; continue }
        break
    }
    Scrivi-Env "ADMIN_PASSWORD" $p1 $script:EnvFile
    Remove-Variable p1, p2
}

if ($nuovaInstallazione) {
    Write-Host ""
    Write-Host "  (Facoltativo) Notifiche sul telefono con l'app gratuita ntfy: scegli un nome"
    Write-Host "  difficile da indovinare, es. mediabox-rossi-4821. Lascia vuoto per saltare."
    $ntfy = (Read-Host "  ? Argomento ntfy") -replace "[^A-Za-z0-9_-]", ""
    Scrivi-Env "NTFY_TOPIC" $ntfy $script:EnvFile
}

foreach ($chiave in @("RADARR_API_KEY", "PROWLARR_API_KEY", "SONARR_API_KEY")) {
    if (-not (Leggi-Env $chiave $script:EnvFile)) { Scrivi-Env $chiave (Nuova-ChiaveCasuale) $script:EnvFile }
}

# Cartella dei film: il disco con più spazio libero
if ($nuovaInstallazione) {
    $dischi = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" | Sort-Object FreeSpace -Descending
    $cartellaDati = "C:/Mediabox/dati"
    $migliore = $dischi | Select-Object -First 1
    if ($migliore -and $migliore.DeviceID -ne "C:") {
        $gb = [math]::Round($migliore.FreeSpace / 1GB)
        if (Chiedi-SiNo "Metto film e download su $($migliore.DeviceID)\Mediabox ($gb GB liberi)?" $true) {
            $cartellaDati = "$($migliore.DeviceID)/Mediabox"
        }
    }
    Scrivi-Env "MEDIA_DIR" $cartellaDati $script:EnvFile
}
$mediaDir = Leggi-Env "MEDIA_DIR" $script:EnvFile
New-Item -ItemType Directory -Force -Path ($mediaDir -replace "/", "\") | Out-Null
Scrivi-Ok "Film, serie e download in: $($mediaDir -replace '/', '\')"

# --- 5. Hardware e rete -----------------------------------------------------------------------
Scrivi-Titolo "Rilevo l'hardware e la rete"
Scrivi-Env "PIATTAFORMA" "windows" $script:EnvFile
Scrivi-Env "PUID" "1000" $script:EnvFile
Scrivi-Env "PGID" "1000" $script:EnvFile
Scrivi-Env "VAAPI" "no" $script:EnvFile
Scrivi-Env "DVB" "no" $script:EnvFile
if ($WithSonarr) { Scrivi-Env "WITH_SONARR" "si" $script:EnvFile }
$ip = Trova-IpLocale
Scrivi-Env "HOST_IP" $ip $script:EnvFile
Scrivi-Info "Su Windows la transcodifica usa il processore e i tuner USB non sono supportati (limiti di Docker Desktop)."

$hdhr = Trova-HDHomeRun
if ($hdhr) { Scrivi-Env "HDHOMERUN_IP" $hdhr $script:EnvFile; Scrivi-Ok "HDHomeRun trovato in rete: $hdhr" }
else { Scrivi-Info "Nessun HDHomeRun in rete" }

# Porta 80 occupata da un altro programma (es. IIS)?
$porta = Leggi-Env "HTTP_PORT" $script:EnvFile
if (-not $porta) { $porta = "80" }
$occupata = Get-NetTCPConnection -LocalPort ([int]$porta) -State Listen -ErrorAction SilentlyContinue |
    Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName -notmatch "docker|wslrelay|vpnkit" }
if ($occupata) {
    Scrivi-Avviso "La porta $porta è già usata da un altro programma."
    if (Chiedi-SiNo "Uso la porta 8080? (l'indirizzo avrà :8080 alla fine)" $true) { Scrivi-Env "HTTP_PORT" "8080" $script:EnvFile; $porta = "8080" }
}

# Nome mediabox.local: Windows annuncia da solo solo il proprio nome
if ($env:COMPUTERNAME -ne "MEDIABOX") {
    Write-Host ""
    Write-Host "  Per usare l'indirizzo http://mediabox.local il PC deve chiamarsi MEDIABOX."
    if (Chiedi-SiNo "Rinomino il PC in MEDIABOX? (serve un riavvio alla fine)" $true) {
        Rename-Computer -NewName "MEDIABOX" -Force -WarningAction SilentlyContinue
        $riavvioNecessario = $true
        Scrivi-Env "NOME_HOST" "mediabox.local" $script:EnvFile
    } else {
        Scrivi-Env "NOME_HOST" "$($env:COMPUTERNAME.ToLower()).local" $script:EnvFile
    }
} else {
    Scrivi-Env "NOME_HOST" "mediabox.local" $script:EnvFile
}

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
$pubbliche = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq "Public" }
if ($pubbliche) {
    Scrivi-Avviso "La rete '$($pubbliche[0].Name)' è impostata come 'Pubblica': gli altri dispositivi non vedranno mediabox."
    if (Chiedi-SiNo "È la rete di casa? La imposto come 'Privata'?" $true) {
        $pubbliche | Set-NetConnectionProfile -NetworkCategory Private
    }
}
Scrivi-Ok "Firewall pronto (solo rete di casa)"

# Il PC non deve andare in sospensione, altrimenti mediabox si spegne
if (Chiedi-SiNo "Disattivo la sospensione automatica del PC (quando è collegato alla corrente)?" $true) {
    & powercfg /change standby-timeout-ac 0
    & powercfg /change hibernate-timeout-ac 0
    Scrivi-Ok "Sospensione disattivata"
}

# Comando "mediabox" disponibile in qualsiasi finestra
$percorsoMacchina = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($percorsoMacchina -notlike "*$Cartella*") {
    [Environment]::SetEnvironmentVariable("Path", "$percorsoMacchina;$Cartella", "Machine")
}
Scrivi-Ok "Comando 'mediabox' installato (funziona nelle nuove finestre di PowerShell o Prompt)"

# --- 6. Avvio e configurazione automatica ---------------------------------------------------
$tuttoBene = Applica-Configurazione

Scrivi-Titolo "Ultima cosa: accensione automatica"
Write-Host "  Docker Desktop parte quando entri in Windows. Perché mediabox si accenda da solo"
Write-Host "  dopo un'interruzione di corrente, attiva l'accesso automatico a Windows:"
Write-Host "  premi Win+R, scrivi netplwiz, togli la spunta 'Per utilizzare questo computer è"
Write-Host "  necessario che l'utente immetta il nome utente e la password' (dettagli nel README)."

if ($riavvioNecessario) {
    Scrivi-Avviso "Il PC è stato rinominato in MEDIABOX: serve un riavvio perché mediabox.local funzioni."
    if (Chiedi-SiNo "Riavvio adesso?" $false) { Restart-Computer -Force }
}
if (-not $tuttoBene) {
    Scrivi-Avviso "Qualcosa non è andato: leggi 'Cosa non è andato' qui sopra. Spesso basta scrivere: mediabox configura"
}
Write-Host ""
Read-Host "  Premi Invio per chiudere" | Out-Null
