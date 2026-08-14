# Axiom-Flow 推理基础设施状态脚本
# 用途：检查容器健康、GPU 可见性与端口连通
$ErrorActionPreference = "Continue"

$repoWin = Split-Path $PSScriptRoot -Parent
$repoWsl = "/mnt/" + $repoWin[0].ToString().ToLower() + ($repoWin.Substring(2) -replace '\\', '/')
$composeWsl = "$repoWsl/scripts/compose.yaml"

Write-Host "==> WSL 发行版状态："
wsl -l -v

# 注：本环境（mirrored 网络）wsl -l -v 的 STATE 列可能显示 Stopped 假象，
# 以实际 docker 命令是否可执行为准，不做 wsl --shutdown 类破坏性动作。

Write-Host "`n==> Docker 引擎："
wsl -e docker info --format "Server: {{.ServerVersion}} / Containers: {{.Containers}} / Images: {{.Images}}" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker 引擎不可达。运行：wsl -e sudo service docker start" -ForegroundColor Red
    exit 1
}

Write-Host "`n==> 容器状态："
wsl -e docker compose -f $composeWsl ps

Write-Host "`n==> GPU 穿透验证（容器内）："
wsl -e docker exec mineru-api nvidia-smi -L 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] 容器内 GPU 不可见（容器可能未运行）" -ForegroundColor Yellow
}

Write-Host "`n==> keepalive 会话："
$keepalive = Join-Path $PSScriptRoot ".keepalive.pid"
if (Test-Path $keepalive) {
    $kp = Get-Content $keepalive
    if (Get-Process -Id $kp -ErrorAction SilentlyContinue) {
        Write-Host "活跃 (PID $kp) —— VM 防挂起中" -ForegroundColor Green
    } else {
        Write-Host "失效 (PID $kp) —— VM 可能已挂起，建议重跑 infra-up.ps1" -ForegroundColor Yellow
    }
} else {
    Write-Host "无 —— VM 在最后一个 wsl 客户端退出后约 30s 挂起，建议 infra-up.ps1" -ForegroundColor Yellow
}

Write-Host "`n==> 端点探测："
$probe = Invoke-WebRequest -Uri "http://127.0.0.1:8002/health" -TimeoutSec 5 -UseBasicParsing -ErrorAction SilentlyContinue
if ($probe -and $probe.StatusCode -eq 200) {
    Write-Host "mineru-api 8002: OK ($($probe.StatusCode))" -ForegroundColor Green
} else {
    Write-Host "mineru-api 8002: 不可达（容器未运行或仍在启动）" -ForegroundColor Yellow
}
