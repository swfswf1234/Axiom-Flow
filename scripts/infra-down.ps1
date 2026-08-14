# Axiom-Flow 推理基础设施停止脚本
# 用途：优雅停止容器（保留容器与模型缓存，镜像内模型不删）
$ErrorActionPreference = "Stop"

$repoWin = Split-Path $PSScriptRoot -Parent
$repoWsl = "/mnt/" + $repoWin[0].ToString().ToLower() + ($repoWin.Substring(2) -replace '\\', '/')
$composeWsl = "$repoWsl/scripts/compose.yaml"

Write-Host "==> 停止容器（down 保留数据卷）..."
wsl -e docker compose -f $composeWsl down
if ($LASTEXITCODE -eq 0) {
    Write-Host "==> 已停止。镜像保留，下次 infra-up.ps1 秒级恢复。" -ForegroundColor Green
} else {
    Write-Host "[ERROR] 停止失败" -ForegroundColor Red
    exit 1
}

# 回收 keepalive 会话（wsl 客户端断开后 VM 将按配置休眠，释放资源）
$keepalive = Join-Path $PSScriptRoot ".keepalive.pid"
if (Test-Path $keepalive) {
    $pid_ = Get-Content $keepalive
    Stop-Process -Id $pid_ -ErrorAction SilentlyContinue
    Remove-Item $keepalive -Force
    Write-Host "keepalive 会话已回收 (PID $pid_)"
}
