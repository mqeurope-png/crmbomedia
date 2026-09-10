<#
.SYNOPSIS
  Registra en FACTUSOL, EN LOTE, los cobros conciliados a mano en el Excel
  «Listado de facturas - cuenta y forma de pago», conduciendo el registro de
  cobro de BoHub (F-4-B: escribe la línea en F_LCO y marca la factura cobrada).

.DESCRIPTION
  Para cada factura del CSV: consulta EN VIVO su total/saldo/estado en FACTUSOL
  y —con -Apply— registra UN cobro con: importe = total de la factura (el saldo
  pendiente; no el importe del banco), cuenta = contrapartida de la columna
  CUENTA, forma de pago = columna FORMA (va al concepto), fecha = FECHA COBRO.

  Dry-run por defecto: lista qué registraría (nº de cobros = localizados −
  excluidos − ya cobradas) sin escribir nada. Con -Apply pide confirmación
  («escribe SI») y registra de uno en uno, deteniéndose ante el primer error
  real. Idempotente: si la factura ya está cobrada en FACTUSOL, la salta.
  Solo escribe cobros: no toca líneas, totales ni nada más.

  Entrada: CSV UTF-8 con cabecera y estas columnas (derivado del Excel):
    serie,codigo,cuenta,forma,fecha[,observaciones]
  o bien  numero (p.ej. 1-260729),cuenta,forma,fecha[,observaciones]
  Las filas SIN cuenta (cobro no localizado) se saltan. Las facturas de la
  lista EXCLUIR (doble cobro, anticipos, Scalapay, a confirmar) se saltan
  aunque estén en el CSV, salvo con -IncluirExcluidas.

  PRUEBA PRIMERO con un CSV de 1-2 facturas (1-260729 Neonled 72,60 € y
  5-260082 Rocío Bueno 70,18 €), comprueba el cobro en FACTUSOL y luego lanza
  el lote completo.

.EXAMPLE
  .\registrar_cobros_lote.ps1 -BaseUrl https://tu-dominio -Csv .\prueba.csv
.EXAMPLE
  .\registrar_cobros_lote.ps1 -BaseUrl https://tu-dominio -Csv .\cobros.csv -Apply
#>

[CmdletBinding()]
param(
    [string]$BaseUrl = "",
    [string]$Csv = ".\cobros.csv",
    [switch]$Apply,
    # Registrar también las de la lista EXCLUIR (por defecto NO).
    [switch]$IncluirExcluidas,
    [int]$PausaSegundos = 1,
    [string]$LogFile = ".\registrar_cobros_$(Get-Date -Format yyyyMMdd_HHmmss).log"
)

$ErrorActionPreference = "Stop"
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# Facturas que se registran A MANO (tienen criterio): NO entran en el lote.
$EXCLUIR = @(
    "2-526081",                                   # doble cobro
    "2-526082", "2-526085", "5-260068", "5-260067", # anticipos / parciales
    "1-260738", "2-526075",                       # Scalapay (comisión / IVA)
    "5-260085", "5-260064", "5-260074"            # a confirmar / importe no cuadra
)

# Columna CUENTA del Excel → contrapartida de FACTUSOL (solo informativo en el
# dry-run: quien resuelve y VALIDA el nombre es el backend, contra el catálogo
# de /erp/settings). Confirmado por Bart.
$CUENTAS = @{
    "bomedia (sabadell)"   = "6  Bomedia Sabadell"
    "streamtec (sabadell)" = "8  Streamtec Sabadell"
    "mq europe (belfius)"  = "2  MQ Europe Belfius"
    "paypal streamtec"     = "14 Paypal Streamtec"
    "paypal mq europe"     = "12 Paypal MQ Europe"
}

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
function Get-Contrapartida($cuenta) {
    $k = ("$cuenta").Trim().ToLower()
    if ($CUENTAS.ContainsKey($k)) { return $CUENTAS[$k] }
    return "(la resuelve BoHub: '$cuenta')"
}

Start-Transcript -Path $LogFile -Append | Out-Null
Write-Host "== Registrar cobros en FACTUSOL en lote (F_LCO + cobrada) ==" -ForegroundColor Cyan

if (-not $BaseUrl) { $BaseUrl = Read-Host "URL base de BoHub (p.ej. https://tu-dominio)" }
$BaseUrl = $BaseUrl.TrimEnd("/")
if (-not $BaseUrl.StartsWith("http")) { Stop-Transcript | Out-Null; throw "BaseUrl no válida: $BaseUrl" }
if (-not (Test-Path $Csv)) { Stop-Transcript | Out-Null; throw "No encuentro el CSV: $Csv" }

# --- 1) CSV ------------------------------------------------------------------
$filas = Import-Csv -Path $Csv
if (-not $filas) { Stop-Transcript | Out-Null; throw "El CSV está vacío." }
$cols = $filas[0].PSObject.Properties.Name
$tieneNumero = $cols -contains "numero"
if (-not $tieneNumero -and -not (($cols -contains "serie") -and ($cols -contains "codigo"))) {
    Stop-Transcript | Out-Null
    throw "El CSV debe tener 'serie,codigo' o 'numero' (p.ej. 1-260729), más cuenta,forma,fecha."
}
foreach ($c in @("cuenta", "fecha")) {
    if ($cols -notcontains $c) { Stop-Transcript | Out-Null; throw "Falta la columna '$c' en el CSV." }
}
Write-Host "CSV: $($filas.Count) filas." -ForegroundColor Gray

# --- 2) Login (mismo esquema que emitir_/enviar_/exportar_) ------------------
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
    Write-Host "Tu usuario tiene 2FA; este script no completa el 2FA. Usa un usuario sin 2FA con permiso de EDICIÓN de ERP (admin/pedidos)." -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
}
$headers = @{ Authorization = "Bearer $($login.access_token)" }
Write-Host "Login OK (token ~8 h)." -ForegroundColor Green

# --- 3) Plan (consulta EN VIVO, no escribe) ---------------------------------
$plan = @()
foreach ($f in $filas) {
    if ($tieneNumero) {
        $parts = ("$($f.numero)").Trim() -split "-", 2
        if ($parts.Count -ne 2) { $plan += [pscustomobject]@{ numero = "$($f.numero)"; estado = "número inválido"; registrable = $false }; continue }
        $serie = [int]$parts[0]; $codigo = [int]$parts[1]
    } else {
        $serie = [int]("$($f.serie)").Trim(); $codigo = [int]("$($f.codigo)").Trim()
    }
    $num = "{0}-{1}" -f $serie, $codigo
    $cuenta = ("$($f.cuenta)").Trim()
    $forma  = if ($cols -contains "forma") { ("$($f.forma)").Trim() } else { "" }
    $fecha  = ("$($f.fecha)").Trim()
    $obs    = if ($cols -contains "observaciones") { ("$($f.observaciones)").Trim() } else { "" }
    $row = [pscustomobject]@{
        numero = $num; serie = $serie; codigo = $codigo; cliente = ""
        total = $null; saldo = $null; cuenta = $cuenta
        contrapartida = (Get-Contrapartida $cuenta); forma = $forma; fecha = $fecha
        observaciones = $obs; estado = ""; registrable = $false
    }
    if (-not $cuenta) { $row.estado = "SIN CUENTA (cobro no localizado; se salta)"; $plan += $row; continue }
    if (-not $fecha)  { $row.estado = "SIN FECHA (se salta)"; $plan += $row; continue }
    if (($EXCLUIR -contains $num) -and -not $IncluirExcluidas) { $row.estado = "EXCLUIDA (registrar a mano)"; $plan += $row; continue }
    # Estado en vivo: total, cobros, saldo pendiente y ESTFAC.
    try {
        $d = Invoke-RestMethod -Headers $headers -Uri "$BaseUrl/api/erp/factusol/documents/facturas/$serie/$codigo"
    } catch {
        $code = Get-HttpStatus $_
        if ($code -eq 404) { $row.estado = "NO EXISTE en FACTUSOL (se salta)" }
        else { $row.estado = "error al consultar (HTTP $code): $(Get-ErrorBody $_)" }
        $plan += $row; continue
    }
    $row.cliente = "$($d.cliente_nombre)"; $row.total = $d.total
    $row.saldo = if ($null -ne $d.saldo_pendiente) { $d.saldo_pendiente } else { $d.total }
    $estfac = "$($d.estado)"
    if ($estfac -eq "2" -or ($null -ne $row.saldo -and [math]::Abs([double]$row.saldo) -lt 0.005)) {
        $row.estado = "YA COBRADA (se salta)"; $plan += $row; continue
    }
    $row.estado = "a registrar"; $row.registrable = $true
    $plan += $row
}

Write-Host ""
Write-Host "== PLAN (importe = saldo pendiente = total si no había cobros) ==" -ForegroundColor Cyan
$plan | Format-Table numero, cliente, total, saldo, contrapartida, forma, fecha, estado -AutoSize | Out-String | Write-Host
$aRegistrar = @($plan | Where-Object { $_.registrable })
$excluidas  = @($plan | Where-Object { $_.estado -like "EXCLUIDA*" }).Count
$yaCobradas = @($plan | Where-Object { $_.estado -like "YA COBRADA*" }).Count
$sinCuenta  = @($plan | Where-Object { $_.estado -like "SIN CUENTA*" }).Count
Write-Host ("A registrar: {0} · Excluidas: {1} · Ya cobradas: {2} · Sin cuenta: {3} · Otras saltadas: {4}" -f `
    $aRegistrar.Count, $excluidas, $yaCobradas, $sinCuenta,
    ($plan.Count - $aRegistrar.Count - $excluidas - $yaCobradas - $sinCuenta)) -ForegroundColor Yellow
$sumaImportes = ($aRegistrar | Measure-Object -Property saldo -Sum).Sum
Write-Host ("Importe total a registrar: {0:N2} €" -f ($sumaImportes)) -ForegroundColor Yellow

# --- 4) Dry-run por defecto ---------------------------------------------------
if (-not $Apply) {
    Write-Host "`nMODO PREVISUALIZACIÓN (dry-run): no se ha registrado nada." -ForegroundColor Green
    Write-Host "Para registrar de verdad, vuelve a ejecutar con  -Apply" -ForegroundColor Green
    Stop-Transcript | Out-Null; exit 0
}
if ($aRegistrar.Count -eq 0) { Write-Host "No hay nada que registrar." -ForegroundColor Green; Stop-Transcript | Out-Null; exit 0 }

# --- 5) Confirmación ----------------------------------------------------------
Write-Host "`nSe registrará UN cobro por cada una de estas facturas (escribe en la contabilidad de FACTUSOL):" -ForegroundColor Yellow
$aRegistrar | ForEach-Object { Write-Host ("  {0}  {1,10:N2} €  → {2}  ({3})" -f $_.numero, $_.saldo, $_.contrapartida, $_.fecha) }
$resp = Read-Host ("`nVas a REGISTRAR {0} cobros en FACTUSOL por {1:N2} €. Escribe SI para continuar" -f $aRegistrar.Count, $sumaImportes)
if ($resp -ne "SI") { Write-Host "Cancelado. No se ha registrado nada." -ForegroundColor Yellow; Stop-Transcript | Out-Null; exit 0 }

# --- 6) Registro de uno en uno, parando ante el primer error real ------------
$hechos = @(); $fallo = $null
foreach ($p in $aRegistrar) {
    Write-Host ""
    Write-Host ("→ {0}  {1:N2} €  → {2}  ({3})" -f $p.numero, $p.saldo, $p.contrapartida, $p.fecha) -ForegroundColor Cyan
    try {
        $body = @{
            confirm = $true
            cuenta  = $p.cuenta          # nombre del Excel; BoHub lo resuelve al código
            fecha   = $p.fecha
            forma   = $p.forma
            observaciones = $p.observaciones
            # importe: NO se manda → BoHub registra el saldo pendiente (= total).
        } | ConvertTo-Json
        $res = Invoke-RestMethod -Method Post -ContentType "application/json" -Headers $headers `
            -Uri "$BaseUrl/api/erp/factusol/documents/facturas/$($p.serie)/$($p.codigo)/collection" -Body $body
    } catch {
        $code = Get-HttpStatus $_
        $fallo = "Factura $($p.numero): error al solicitar el cobro (HTTP $code): $(Get-ErrorBody $_)"
        break
    }
    if ($res.status -eq "already") {
        Write-Host "  YA COBRADA en FACTUSOL (se salta)" -ForegroundColor Yellow
        continue
    }
    # Espera al job de escritura (cola factusol:writes, serial).
    $result = $null; $err = $null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try {
            $st = Invoke-RestMethod -Headers $headers `
                -Uri "$BaseUrl/api/erp/factusol/documents/facturas/collection-status/$($res.job_id)"
        } catch { $err = "no se pudo consultar el estado del job: $(Get-ErrorBody $_)"; break }
        if ($st.status -eq "finished") { $result = $st.result; break }
        if ($st.status -eq "failed")   { $err = "$($st.error)"; break }
    }
    if ($err) { $fallo = "Factura $($p.numero): $err"; break }
    if ($null -eq $result) { $fallo = "Factura $($p.numero): el job no terminó a tiempo (revisa el worker-factusol)"; break }
    if (-not $result.registered) {
        if ($result.status -eq "already") { Write-Host "  YA COBRADA (se salta)" -ForegroundColor Yellow; continue }
        $fallo = "Factura $($p.numero): NO registrado ($($result.status)): $($result.motivo)"
        break
    }
    $flag = if ($result.estfac_marked) { "cobrada" } else { "línea escrita pero SIN marcar cobrada: $($result.motivo)" }
    Write-Host ("  OK → F_LCO línea {0}, {1:N2} € → contrapartida {2} · {3}" -f $result.linlco, $result.importe, $result.contrapartida, $flag) -ForegroundColor Green
    $hechos += [pscustomobject]@{ numero = $p.numero; importe = $result.importe; contrapartida = $result.contrapartida; fecha = $result.fecha; cobrada = $result.estfac_marked }
    if (-not $result.estfac_marked) { $fallo = "Factura $($p.numero): la línea de cobro se escribió pero no se pudo marcar cobrada: $($result.motivo)"; break }
    if ($PausaSegundos -gt 0) { Start-Sleep -Seconds $PausaSegundos }
}

# --- 7) Resumen ---------------------------------------------------------------
Write-Host ""
Write-Host "== RESUMEN ==" -ForegroundColor Cyan
Write-Host ("Registrados: {0}" -f $hechos.Count) -ForegroundColor Green
$hechos | Format-Table numero, importe, contrapartida, fecha, cobrada -AutoSize | Out-String | Write-Host
if ($fallo) {
    Write-Host "DETENIDO ante un error (no se siguió con el resto):" -ForegroundColor Red
    Write-Host "  $fallo" -ForegroundColor Red
    Write-Host "Corrige y vuelve a ejecutar: las ya cobradas se saltan solas. Log: $LogFile" -ForegroundColor Red
    Stop-Transcript | Out-Null; exit 1
}
Write-Host "Lote registrado sin errores. Log: $LogFile" -ForegroundColor Green
Stop-Transcript | Out-Null; exit 0
