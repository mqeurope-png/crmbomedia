<#
.SYNOPSIS
  Exporta en LOTE los PDF de facturas de FACTUSOL, por serie+número,
  conduciendo la generación de PDF que BoHub ya tiene (el mismo motor E4 que
  usa la descarga individual y el adjunto del envío F-1). NO reimplementa la
  generación de PDF.

.DESCRIPTION
  Para cada factura (serie+código) descarga su PDF del endpoint de solo lectura
  de BoHub y lo guarda en una carpeta. Funciona por serie+número AUNQUE la
  factura no esté enlazada a ningún pedido de BoHub (facturas de flux hechas a
  mano): el PDF se genera de los datos de FACTUSOL, no del pedido. La identidad
  fiscal (empresa emisora) la pone la SERIE (1 Bomedia, 2 MQ Europe, 5
  Streamtec), como ya hace el motor E4.

  Solo lectura: NO envía, NO marca, NO escribe en FACTUSOL. Si una factura no
  existe, la salta y avisa.

  Entrada (elige una):
    * CSV con columnas `serie,codigo` (número desnudo, p. ej. `1,260742`), o
    * un rango: -Serie 2 -Desde 526071 -Hasta 526096.

.EXAMPLE
  # Las 7 de flux/Marta desde un CSV:
  .\exportar_facturas_pdf.ps1 -BaseUrl https://tu-dominio -Csv .\pedidos_enviar.csv

.EXAMPLE
  # Un rango de la serie 2, en inglés, a otra carpeta:
  .\exportar_facturas_pdf.ps1 -BaseUrl https://tu-dominio -Serie 2 -Desde 526071 -Hasta 526096 -Idioma en -OutDir .\pdfs_mq
#>

[CmdletBinding()]
param(
    [string]$BaseUrl = "",
    # CSV con dos columnas: serie,codigo (p. ej. "1,260742").
    [string]$Csv = "",
    # Rango alternativo al CSV: una serie y un intervalo de códigos.
    [int]$Serie = 0,
    [int]$Desde = 0,
    [int]$Hasta = 0,
    # Idioma del PDF (etiquetas; nunca cambia los datos). es|en|de|fr|nl.
    [ValidateSet("es", "en", "de", "fr", "nl")]
    [string]$Idioma = "es",
    [string]$OutDir = ".\facturas_pdf",
    [int]$PausaSegundos = 1,
    [string]$LogFile = ".\exportar_facturas_$(Get-Date -Format yyyyMMdd_HHmmss).log"
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

function Get-ErrorBody($err) {
    try {
        if ($err.ErrorDetails -and $err.ErrorDetails.Message) { return $err.ErrorDetails.Message }
        $resp = $err.Exception.Response
        if ($resp) {
            $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
            return $reader.ReadToEnd()
        }
    } catch {}
    return $err.Exception.Message
}
function Get-HttpStatus($err) {
    try { return [int]$err.Exception.Response.StatusCode } catch { return 0 }
}
# Nombre de archivo seguro: quita separadores y caracteres inválidos del SO.
function Get-SafeName($name, $fallback) {
    if (-not $name) { return $fallback }
    $name = $name -replace '[\\/:*?"<>|]', "_"
    $name = $name.Trim()
    if (-not $name) { return $fallback }
    return $name
}

Start-Transcript -Path $LogFile -Append | Out-Null
Write-Host "== Exportar PDF de facturas en lote (motor E4 de BoHub, solo lectura) ==" -ForegroundColor Cyan

if (-not $BaseUrl) { $BaseUrl = Read-Host "URL base de BoHub (p.ej. https://tu-dominio)" }
$BaseUrl = $BaseUrl.TrimEnd("/")
if (-not $BaseUrl.StartsWith("http")) { Stop-Transcript | Out-Null; throw "BaseUrl no válida: $BaseUrl" }

# --- 1) Lista de facturas (serie,codigo): CSV o rango -----------------------
$facturas = @()
if ($Csv) {
    if (-not (Test-Path $Csv)) { Stop-Transcript | Out-Null; throw "No encuentro el CSV: $Csv (columnas: serie,codigo)" }
    $filas = Import-Csv -Path $Csv
    if (-not $filas) { Stop-Transcript | Out-Null; throw "El CSV está vacío." }
    foreach ($f in $filas) {
        if (($null -eq $f.serie) -or ($null -eq $f.codigo)) {
            Stop-Transcript | Out-Null; throw "El CSV debe tener las columnas 'serie' y 'codigo'."
        }
        $facturas += [pscustomobject]@{ serie = [int]"$($f.serie)".Trim(); codigo = [int]"$($f.codigo)".Trim() }
    }
}
elseif ($Serie -gt 0 -and $Desde -gt 0 -and $Hasta -ge $Desde) {
    for ($c = $Desde; $c -le $Hasta; $c++) {
        $facturas += [pscustomobject]@{ serie = $Serie; codigo = $c }
    }
}
else {
    Stop-Transcript | Out-Null
    throw "Indica un CSV (-Csv serie,codigo) o un rango (-Serie N -Desde A -Hasta B)."
}
Write-Host ("Facturas a exportar: {0} · idioma {1}" -f $facturas.Count, $Idioma) -ForegroundColor Gray

# --- 2) Login (mismo esquema que emitir_/enviar_facturas_lote.ps1) ----------
$email = Read-Host "Email BoHub"
$secPass = Read-Host "Contraseña" -AsSecureString
$plain = [System.Net.NetworkCredential]::new("", $secPass).Password
Write-Host "Autenticando en $BaseUrl ..." -ForegroundColor Gray
try {
    $login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/auth/login" `
        -ContentType "application/json" -Body (@{ email = $email; password = $plain } | ConvertTo-Json)
} catch {
    Write-Host "ERROR de login: $(Get-ErrorBody $_)" -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
} finally { $plain = $null }
if ($login.requires_2fa) {
    Write-Host "Tu usuario tiene 2FA; este script no completa el 2FA. Usa un usuario sin 2FA (rol admin/pedidos/user)." -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
}
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "Login OK (token ~8 h)." -ForegroundColor Green

# --- 3) Carpeta de salida ---------------------------------------------------
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$OutDir = (Resolve-Path $OutDir).Path
Write-Host "Guardando en: $OutDir" -ForegroundColor Gray

# --- 4) Descarga de una en una (solo lectura) -------------------------------
$descargadas = @(); $fallidas = @()
foreach ($fac in $facturas) {
    $num = "{0}-{1}" -f $fac.serie, $fac.codigo
    $uri = "$BaseUrl/api/erp/factusol/documents/facturas/$($fac.serie)/$($fac.codigo)/pdf?lang=$Idioma"
    try {
        $resp = Invoke-WebRequest -Uri $uri -Headers $headers -Method Get
    } catch {
        $code = Get-HttpStatus $_
        if ($code -eq 404) {
            Write-Host ("  {0} → NO EXISTE en FACTUSOL (se salta)" -f $num) -ForegroundColor Yellow
            $motivo = "no existe"
        } else {
            Write-Host ("  {0} → ERROR (HTTP {1}): {2}" -f $num, $code, (Get-ErrorBody $_)) -ForegroundColor Red
            $motivo = "HTTP $code"
        }
        $fallidas += [pscustomobject]@{ numero = $num; motivo = $motivo }
        continue
    }
    # Nombre legible: el que ya compone el backend (Factura_{serie}-{codigo}_{cliente}.pdf),
    # de la cabecera Content-Disposition; si no llega, uno mínimo por serie-número.
    $cd = [string]$resp.Headers["Content-Disposition"]
    $fname = "Factura_$num.pdf"
    if ($cd -match 'filename\*?=(?:UTF-8'''')?"?([^";]+)"?') { $fname = $matches[1] }
    $fname = Get-SafeName $fname "Factura_$num.pdf"
    $path = Join-Path $OutDir $fname
    try {
        $resp.RawContentStream.Position = 0
        $fs = [System.IO.File]::Create($path)
        $resp.RawContentStream.CopyTo($fs)
        $fs.Close()
    } catch {
        Write-Host ("  {0} → ERROR al guardar: {1}" -f $num, $_.Exception.Message) -ForegroundColor Red
        $fallidas += [pscustomobject]@{ numero = $num; motivo = "guardar" }
        continue
    }
    Write-Host ("  {0} → {1}" -f $num, $fname) -ForegroundColor Green
    $descargadas += [pscustomobject]@{ numero = $num; archivo = $fname }
    if ($PausaSegundos -gt 0) { Start-Sleep -Seconds $PausaSegundos }
}

# --- 5) Resumen -------------------------------------------------------------
Write-Host ""
Write-Host "== RESUMEN ==" -ForegroundColor Cyan
Write-Host ("Descargadas: {0}" -f $descargadas.Count) -ForegroundColor Green
Write-Host ("Fallidas / no existentes: {0}" -f $fallidas.Count) -ForegroundColor Yellow
if ($fallidas.Count -gt 0) {
    $fallidas | Format-Table numero, motivo -AutoSize | Out-String | Write-Host
}
Write-Host ("Carpeta: {0}" -f $OutDir) -ForegroundColor Gray
Write-Host ("Log: {0}" -f $LogFile) -ForegroundColor Gray
Stop-Transcript | Out-Null
exit 0
