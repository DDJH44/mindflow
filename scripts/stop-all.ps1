# MindFlow 停止服务
#
# 只停后端与前端（按端口找进程），**不动容器** ——
# 容器是常驻依赖，下次启动还要用；而每次重起 Milvus
# 都要等它接受连接（约 20 秒），没必要。
#
# 想连容器一起停：powershell -File scripts\stop-all.ps1 -Containers
#
# 用法：powershell -File scripts\stop-all.ps1
#
# ⚠️ 用 `powershell` 而不是 `pwsh`：本机是 Windows PowerShell 5.1，
# 没有安装 PowerShell 7（`pwsh`）。脚本对两者都兼容 ——
# 但**文件必须以 UTF-8 with BOM 保存**，否则 5.1 会按本地代码页
# 解析中文，报出看起来像"括号不配对"的语法错误。

param(
    [switch]$Containers
)

$ErrorActionPreference = "Continue"

$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step($Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Stop-Port($Port, $Label) {
    $conns = Get-NetTCPConnection -LocalPort $Port `
        -State Listen -ErrorAction SilentlyContinue

    if (-not $conns) {
        Write-Host "    $Label（:$Port）本来就没在运行"
        return
    }

    # 一个端口可能对应多个监听进程（父子），全部停掉
    $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique

    foreach ($processId in $pids) {
        try {
            $proc = Get-Process -Id $processId -ErrorAction Stop
            Write-Host "    停止 $Label（:$Port）PID $processId ($($proc.ProcessName))"
            Stop-Process -Id $processId -Force -ErrorAction Stop
        } catch {
            Write-Host "    无法停止 PID ${processId}：$($_.Exception.Message)"
        }
    }
}

Write-Step "停止后端与前端"

Stop-Port 8000 "后端"
Stop-Port 5173 "前端"

if ($Containers) {
    Write-Step "停止容器"

    # `docker compose` 把进度写到 **stderr**，而 PowerShell 5.1
    # 会把原生命令的 stderr 当作 `NativeCommandError` 显示成红色。
    # 这里不致命（本脚本的 ErrorActionPreference 是 Continue），
    # 但会让人以为出错了，所以临时压掉。
    Push-Location $RepoRoot

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    docker compose stop 2>&1 | Out-Null
    $ErrorActionPreference = $previousPreference

    Pop-Location

    Write-Host "    容器已停止（下次用 start-all.ps1 启动）"
}

Write-Host ""
Write-Host "  完成。"
Write-Host ""
