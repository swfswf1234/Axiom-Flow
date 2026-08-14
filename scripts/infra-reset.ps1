# Axiom-Flow 推理基础设施重置脚本（危险操作）
# 用途：删除容器（模型在镜像内，如需重建镜像则同时删除镜像）
# 注意：这是破坏性操作，执行后需重新构建镜像（约 20GB+ 下载）。
# 仅当镜像损坏或需要升级 MinerU 版本时使用。
$ErrorActionPreference = "Stop"
Write-Warning "即将删除 mineru-api 容器。确认后输入 RESET 继续："
$confirm = Read-Host "输入 RESET 确认"
if ($confirm -ne "RESET") { Write-Host "已取消"; exit 1 }

$repoWin = Split-Path $PSScriptRoot -Parent
$repoWsl = "/mnt/" + $repoWin[0].ToString().ToLower() + ($repoWin.Substring(2) -replace '\\', '/')

wsl -e docker rm -f mineru-api
Write-Host "容器已删除。如需重建镜像："
Write-Host "  wsl -e docker build -t mineru:latest -f $repoWsl/scripts/docker/Dockerfile $repoWsl/scripts/docker/"
Write-Host "然后运行 scripts/infra-up.ps1"
