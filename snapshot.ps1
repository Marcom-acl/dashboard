Set-Location "$PSScriptRoot"
$date = Get-Date -Format 'yyyy-MM-dd HH:mm'
git add -A
$status = git status --porcelain
if ($status) {
    git commit -m "snapshot $date"
    Write-Host "Snapshot cree : snapshot $date" -ForegroundColor Green
} else {
    Write-Host "Rien a sauvegarder - workspace propre." -ForegroundColor Yellow
}
Read-Host "Appuie sur Entree pour fermer"
