# Aggiornamento di mediabox da GitHub e spostamento dei media (Windows).
# Incluso da install.ps1 e mediabox.ps1 dopo lib\comune.ps1.

function Leggi-VersioneInstallata {
    $file = Join-Path $script:MediaboxDir "VERSION"
    if (Test-Path $file) { return (Get-Content $file -Raw).Trim() }
    return "0.0.0"
}

function Invia-Ntfy([string]$Messaggio) {
    $topic = Leggi-Env "NTFY_TOPIC" $script:EnvFile
    if (-not $topic) { return }
    $server = Leggi-Env "NTFY_SERVER" $script:EnvFile
    if (-not $server) { $server = "https://ntfy.sh" }
    try { Invoke-RestMethod -Method Post -Uri "$server/$topic" -Body $Messaggio -Headers @{ Title = "mediabox" } -TimeoutSec 20 | Out-Null }
    catch { $null = $_ }  # una notifica mancata non deve fermare l'aggiornamento
}

# Lo stato che la pagina delle impostazioni mostra: fase e messaggio
# fasi: controllo | scarico | installo | fatto | aggiornato | errore | in_attesa
function Scrivi-StatoAggiornamento([string]$Fase, [string]$Messaggio = "") {
    $dati = [ordered]@{ fase = $Fase; messaggio = $Messaggio; quando = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss") }
    $testo = $dati | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText((Join-Path $script:MediaboxDir "aggiornamento.stato"), $testo, (New-Object System.Text.UTF8Encoding $false))
}

# Il controllo e il download li fa il configuratore (la stessa logica su Linux e Windows).
# Codici: 0 = c'è una versione nuova / installata, 3 = niente di nuovo, altro = errore
function Invoca-Aggiornamento([string]$Azione) {
    Senza-Stderr { & docker run --rm -v "$($script:MediaboxDir):/mediabox" -e MEDIABOX_GITHUB_API mediabox/configuratore:locale aggiornamento $Azione | Out-Host }
    return $LASTEXITCODE
}

# Vero se nel piano dell'aggiornamento c'è da rifare questa parte (cerca, configuratore, configura)
function Nel-Piano([string]$Parte) {
    $file = Join-Path $script:MediaboxDir "generato\aggiornamento.json"
    if (-not (Test-Path $file)) { return $false }
    try { return [bool]((Get-Content $file -Raw | ConvertFrom-Json).$Parte) } catch { return $false }
}

# Scarica solo i programmi che mancano (quelli con una versione nuova)
function Scarica-Mancanti {
    $immagini = @(Senza-Stderr { Invoca-Compose config --images 2>$null } | Where-Object { $_ -and $_ -notlike "mediabox/*" } | Sort-Object -Unique)
    foreach ($immagine in $immagini) {
        Senza-Stderr { & docker image inspect $immagine *> $null }
        if ($LASTEXITCODE -eq 0) { continue }
        Scrivi-Info "Scarico la versione nuova di $(($immagine -split '/')[-1])"
        Senza-Stderr { & docker pull -q $immagine *> $null }
        if ($LASTEXITCODE -ne 0) { Scrivi-Avviso "Non riesco a scaricare $(($immagine -split '/')[-1]): riprovo all'avvio." }
    }
}

# Dopo un aggiornamento: via le versioni vecchie dei programmi di mediabox e gli avanzi delle
# costruzioni di Cerca e del configuratore. Su un PC con un disco piccolo sono diversi GB.
# Si toglie solo un'immagine dello stesso programma con una versione diversa da quella in uso.
function Pulisci-VersioniVecchie {
    $inUso = @(Senza-Stderr { Invoca-Compose config --images 2>$null } | Where-Object { $_ } | Sort-Object -Unique)
    if (-not $inUso) { return }
    $nomi = @($inUso | ForEach-Object { $_ -replace ":[^:/]*$", "" } | Sort-Object -Unique)
    $tutte = @(Senza-Stderr { & docker images --format "{{.Repository}}:{{.Tag}}" 2>$null })
    foreach ($immagine in $tutte) {
        if (-not $immagine -or $immagine -like "*<none>*") { continue }
        if ($inUso -contains $immagine) { continue }
        if ($nomi -notcontains ($immagine -replace ":[^:/]*$", "")) { continue }
        # se un contenitore la usa ancora, Docker si rifiuta: va bene così
        Senza-Stderr { & docker rmi $immagine *> $null }
        if ($LASTEXITCODE -eq 0) { Scrivi-Info "Tolta la versione vecchia: $(($immagine -split '/')[-1])" }
    }
    Senza-Stderr { & docker image prune -f *> $null }
    Senza-Stderr { & docker builder prune -f --filter until=168h *> $null }
}

# Prima parte: controlla, fa il backup e installa i file nuovi.
# Restituisce $true se ha installato qualcosa (poi va eseguito "aggiorna-servizi" con il nuovo script).
function Aggiorna-Mediabox {
    Scrivi-Titolo "Controllo gli aggiornamenti"
    Scrivi-StatoAggiornamento "controllo" "Controllo se c'è una versione nuova…"
    Costruisci-Configuratore
    $esito = Invoca-Aggiornamento "controlla"
    if ($esito -eq 3) {
        Scrivi-StatoAggiornamento "aggiornato" "mediabox è già all'ultima versione."
        Scrivi-Ok "mediabox è già all'ultima versione ($(Leggi-VersioneInstallata))"
        return $false
    }
    if ($esito -ne 0) {
        Scrivi-StatoAggiornamento "errore" "Non riesco a controllare gli aggiornamenti: manca Internet?"
        Esci-ConErrore "Non riesco a controllare gli aggiornamenti." "Controlla la connessione a Internet e riprova."
    }
    [void](Esegui-Backup)
    $esito = Invoca-Aggiornamento "installa"
    if ($esito -eq 3) { return $false }
    if ($esito -ne 0) {
        Esci-ConErrore "L'aggiornamento non è riuscito." "Riprova più tardi: mediabox continua a funzionare con la versione di prima."
    }
    return $true
}

# Seconda parte (con il nuovo script): rifà solo quello che è cambiato
function Aggiorna-Servizi {
    Scrivi-StatoAggiornamento "installo" "Aggiorno i programmi cambiati…"
    Scrivi-Titolo "Aggiorno solo quello che è cambiato"
    Genera-File
    Scarica-Mancanti
    if (Nel-Piano "cerca") {
        Scrivi-Info "Preparo la nuova versione di Cerca"
        Invoca-Compose build -q cerca | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Scrivi-StatoAggiornamento "errore" "Non riesco a preparare la nuova versione di Cerca."
            Esci-ConErrore "Non riesco a preparare Cerca." "Controlla la connessione a Internet e riprova: mediabox update"
        }
    }
    Invoca-Compose up -d --remove-orphans | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Scrivi-StatoAggiornamento "errore" "Alcuni servizi non sono ripartiti."
        Esci-ConErrore "Alcuni servizi non sono ripartiti." "Riprova con: mediabox configura"
    }
    Riavvia-Segnati
    if (Nel-Piano "configura") {
        Scrivi-Titolo "Riconfiguro i servizi"
        Scrivi-StatoAggiornamento "installo" "Riconfiguro i servizi…"
        Invoca-Compose run --rm -T configuratore configura | Out-Host
        if ($LASTEXITCODE -ne 0) {
            Scrivi-StatoAggiornamento "errore" "La configurazione dei servizi non è finita: guarda il registro."
            return $false
        }
    }
    Pulisci-VersioniVecchie
    Remove-Item (Join-Path $script:MediaboxDir "generato\aggiornamento.json") -Force -ErrorAction SilentlyContinue
    Scrivi-StatoAggiornamento "fatto" "mediabox aggiornato alla versione $(Leggi-VersioneInstallata)."
    Scrivi-Ok "mediabox aggiornato alla versione $(Leggi-VersioneInstallata)"
    return $true
}

# Il registro che la pagina delle impostazioni mostra durante l'aggiornamento
function Scrivi-IntestazioneRegistro {
    Add-Content -Path (Join-Path $script:MediaboxDir "aggiornamenti.log") -Value "`r`n== $(Get-Date -Format 'dd/MM/yyyy HH:mm') ==" -Encoding UTF8
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
        Scrivi-Ok "Aggiornamenti automatici attivi (controllo ogni minuto, più uno di riserva ogni notte)"
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
    Senza-Stderr { Invoca-Compose stop *> $null }
    New-Item -ItemType Directory -Force -Path $a | Out-Null
    if (Test-Path $da) {
        & robocopy $da $a /E /MOVE /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Esci-ConErrore "Lo spostamento in $a non è riuscito (spazio finito?)." "I file rimasti sono ancora in $da. Libera spazio e riprova."
        }
    }
    Scrivi-Ok "File spostati in $a"
}
