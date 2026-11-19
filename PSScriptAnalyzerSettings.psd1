# Regole di lint per gli script PowerShell di mediabox (usate da "make check").
@{
    Severity     = @('Error', 'Warning')
    ExcludeRules = @(
        'PSAvoidUsingWriteHost',            # gli script parlano con l'utente: Write-Host è voluto
        'PSUseApprovedVerbs',               # nomi delle funzioni in italiano
        'PSUseSingularNouns',
        'PSUseShouldProcessForStateChangingFunctions',
        'PSAvoidUsingPlainTextForPassword', # la password finisce comunque nel file .env
        'PSUseBOMForUnicodeEncodedFile'
    )
}
