# Axiom-Flow 推理基础设施启动脚本
# 用途：启动 WSL 容器（mineru-api，内嵌 vLLM + hybrid-engine）
# 前置：WSL Ubuntu-24.04 已安装 Docker Engine 且镜像已构建（见 operations.md）
$ErrorActionPreference = "Stop"

# 仓库根目录的 WSL 路径（自动转换盘符为小写，避免硬编码）
$repoWin = Split-Path $PSScriptRoot -Parent
$repoWsl = "/mnt/" + $repoWin[0].ToString().ToLower() + ($repoWin.Substring(2) -replace '\\', '/')
$composeWsl = "$repoWsl/scripts/compose.yaml"

Write-Host "==> 确保 WSL 就绪（wsl -e 会自动唤醒 VM；不 shutdown，避免误杀活跃 daemon）..."
wsl -e sh -c "true" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] WSL 不可达。运行 wsl --shutdown 后重试" -ForegroundColor Red
    exit 1
}

Write-Host "==> 启动容器（docker compose up -d）..."
wsl -e docker compose -f $composeWsl up -d
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] compose 启动失败。请确认镜像已构建：wsl -e docker images" -ForegroundColor Red
    exit 1
}

Write-Host "==> 等待健康检查..."
$ready = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 3
    $health = wsl -e docker inspect --format "{{.State.Health.Status}}" mineru-api 2>$null
    if ($health -eq "healthy") { $ready = $true; break }
    Write-Host "    等待容器健康... ($health)"
}
if (-not $ready) {
    Write-Host "[ERROR] 容器未在 120s 内健康。查看日志：wsl -e docker logs mineru-api" -ForegroundColor Red
    exit 1
}

Write-Host "==> 验证 GPU 穿透..."
wsl -e docker exec mineru-api nvidia-smi -L

# WSL 2.6.x 行为：最后一个 wsl 客户端退出后 ~30s VM 会被挂起（即使 vmIdleTimeout 已禁用），
# 导致容器冻结、端口不通。保持一个常驻 wsl 客户端（keepalive）防挂起；down 时回收。
$keepalive = Join-Path $PSScriptRoot ".keepalive.pid"
if (-not (Test-Path $keepalive) -or -not (Get-Process -Id (Get-Content $keepalive) -ErrorAction SilentlyContinue)) {
    $p = Start-Process wsl -ArgumentList '-e', 'sleep', '2147483' -WindowStyle Hidden -PassThru
    $p.Id | Out-File $keepalive -Encoding ascii
    Write-Host "keepalive 会话已启动 (PID $($p.Id))"
} else {
    Write-Host "keepalive 会话已存在 (PID $(Get-Content $keepalive))"
}

Write-Host "==> 就绪：mineru-api 端点 http://127.0.0.1:8002（OpenAPI /docs）" -ForegroundColor Green
