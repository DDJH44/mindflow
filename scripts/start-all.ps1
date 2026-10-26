# MindFlow 一键启动
#
# ## 为什么需要这个脚本
#
# 助手（AI）在会话里启动的服务**活不过那轮对话** ——
# 实测：每次隔一段时间回来，Docker 引擎、后端、前端
# 全都已经退出。原因是那些进程挂在会话的进程树上，
# 会话结束就被一起清理。
#
# 因此把启动过程交给**用户自己执行**：脚本在当前终端里跑完
# 就退出，但它启动的服务是**独立的进程**，不依赖任何会话。
#
# ## 用法
#
#     powershell -File scripts\start-all.ps1
#
# 想停止服务：powershell -File scripts\stop-all.ps1
#
# ⚠️ 用 `powershell` 而不是 `pwsh`：本机是 Windows PowerShell 5.1，
# 没有安装 PowerShell 7（`pwsh`）。脚本对两者都兼容 ——
# 但**文件必须以 UTF-8 with BOM 保存**，否则 5.1 会按本地代码页
# 解析中文，报出看起来像"括号不配对"的语法错误。

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$WebDir = Join-Path $RepoRoot "web"
$LogDir = Join-Path $RepoRoot ".logs"

# 服务日志写到这里。
#
# 为什么不直接输出到终端：后端与前端都要长期运行，
# 若占着终端就没法再执行别的命令；而完全丢弃日志
# 会让"起不来"变成无法排查。
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step($Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Write-Ok($Text) {
    Write-Host "    [OK] $Text" -ForegroundColor Green
}

function Write-Bad($Text) {
    Write-Host "    [!!] $Text" -ForegroundColor Red
}

function Test-Port($Port) {
    $conn = Get-NetTCPConnection -LocalPort $Port `
        -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Wait-Port($Port, $Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-Port $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# 探测 Docker 引擎版本。引擎未运行时返回空字符串。
#
# ## 为什么必须包一层（实测崩溃点）
#
# 本脚本顶部是 `$ErrorActionPreference = "Stop"`，而 PowerShell 5.1
# 会把**原生命令的 stderr** 当成 `NativeCommandError`。于是：
#
#     $engine = docker info --format "{{.ServerVersion}}" 2>$null
#
# 在引擎没起来时**直接终止整个脚本** —— 尽管写了 `2>$null`。
# 结果是脚本死在"启动 Docker Desktop"那一行**之前**，
# 而那个分支恰好就是为本场景准备的（脚本头注释："每次隔一段
# 时间回来，Docker 引擎…"）。实测：关机一天后运行，
# 只说一句 `failed to connect to the docker API` 就退出，
# 用户只能手动去开 Docker Desktop。
#
# 因此把这类**探测性**调用统一包进来：期间放宽为 `Continue`，
# 恢复后返回输出。全局设置仍是 `Stop`（后面真正的失败要能被发现）。
function Get-DockerEngine {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"

    try {
        $version = docker info --format "{{.ServerVersion}}" 2>$null
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }

    if ($code -ne 0) { return "" }
    return ("$version").Trim()
}

# ---------------------------------------------------------------- Docker
Write-Step "1/4 Docker 引擎"

$engine = Get-DockerEngine

if (-not $engine) {
    Write-Host "    引擎未运行，正在启动 Docker Desktop…"

    $exe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"

    if (-not (Test-Path $exe)) {
        Write-Bad "找不到 Docker Desktop.exe"
        exit 1
    }

    Start-Process -FilePath $exe | Out-Null

    # Docker Desktop 启动较慢（实测 10–60 秒）
    for ($i = 1; $i -le 60; $i++) {
        Start-Sleep -Seconds 5
        $engine = Get-DockerEngine
        if ($engine) { break }
    }
}

if ($engine) {
    Write-Ok "引擎就绪（$engine）"
} else {
    Write-Bad "引擎在 5 分钟内未就绪，后续步骤无法继续"
    exit 1
}

# ---------------------------------------------------------------- 容器
Write-Step "2/4 容器（5 个）"

Push-Location $RepoRoot

# ⚠️ `docker compose up -d` 把进度写到 **stderr**（`Container … Running`），
# 而 PowerShell 5.1 会把原生命令的 stderr 当成 `NativeCommandError`。
# 配合脚本顶部的 `$ErrorActionPreference = "Stop"`，
# 那句"进度信息"会让**整个脚本终止** —— 实测就在这一步崩掉。
#
# 因此在调用期间临时放宽为 `Continue`，改由 `$LASTEXITCODE`
# 判断真实成败。**不能**为此把全局设置改成 `Continue`：
# 那样后面真正的失败会被静默跳过。
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"

docker compose up -d 2>&1 | Out-Null
$composeExit = $LASTEXITCODE

$ErrorActionPreference = $previousPreference

Pop-Location

if ($composeExit -ne 0) {
    Write-Bad "docker compose up -d 退出码 $composeExit"
    exit 1
}

# Milvus 依赖 etcd 与 minio，需要一点时间才接受连接
Write-Host "    等待 Milvus 就绪…"
Start-Sleep -Seconds 22

# `docker ps` 同样可能因引擎中途退出而写 stderr，
# 在 `Stop` 下会打断脚本。它只是取状态，不该致命。
$previousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$containers = docker ps --format "{{.Names}}|{{.Status}}"
$ErrorActionPreference = $previousPreference

if (-not $containers) {
    Write-Bad "容器列表为空"
    exit 1
}

foreach ($name in @(
    "mindflow-postgres", "mindflow-redis", "mindflow-etcd",
    "mindflow-minio", "mindflow-milvus"
)) {
    $line = $containers | Select-String -Pattern "^$name\|" |
        Select-Object -First 1

    if ($line -and $line.Line -match "\|Up") {
        Write-Ok "$name 运行中"
    } else {
        Write-Bad "$name 未运行"
    }
}

# ---------------------------------------------------------------- 后端
Write-Step "3/4 后端 :8000"

if (Test-Port 8000) {
    Write-Ok "已在运行，跳过"
} else {
    # `--no-access-log` 不能省：应用自己已记录访问日志（带 request_id），
    # 不关掉 uvicorn 的会让每条请求打印两行。
    #
    # **不要加 `--reload`**：应用进程内跑着索引 worker，
    # reload 会起两个（SKIP LOCKED 保证不会重复处理，但那是浪费）。
    #
    # ⚠️ **不要给 Start-Process 用 `-RedirectStandardOutput`**（实测踩过）：
    # 那个参数会让 PowerShell **等进程结束**再继续（它要读完流）。
    # 而后端与前端都是**长期运行**的进程 —— 于是脚本永远卡住，
    # 明明服务已经起来（日志里能看到 `Uvicorn running on …`），
    # 脚本却再也不往下走。
    #
    # 因此改用 `cmd /c` 自己做重定向：`Start-Process cmd` 立刻返回，
    # 日志照样落到文件里。
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList @(
            "/c",
            "uv run uvicorn app.main:app " +
            "--host 127.0.0.1 --port 8000 --no-access-log " +
            "> `"$LogDir\backend.log`" 2> `"$LogDir\backend.err.log`""
        ) `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden

    if (Wait-Port 8000 60) {
        Write-Ok "已启动"
    } else {
        Write-Bad "60 秒内未监听，看 $LogDir\backend.err.log"
    }
}

# ---------------------------------------------------------------- 前端
Write-Step "4/4 前端 :5173"

if (Test-Port 5173) {
    Write-Ok "已在运行，跳过"
} else {
    # 用 npx 而不是全局 vite：依赖版本由 package.json 决定。
    #
    # 同样**不能**用 `-RedirectStandardOutput` —— 理由见后端那段注释。
    Start-Process -FilePath "cmd.exe" `
        -ArgumentList @(
            "/c",
            "npx vite --port 5173 --host 127.0.0.1 " +
            "> `"$LogDir\frontend.log`" 2> `"$LogDir\frontend.err.log`""
        ) `
        -WorkingDirectory $WebDir `
        -WindowStyle Hidden

    if (Wait-Port 5173 60) {
        Write-Ok "已启动"
    } else {
        Write-Bad "60 秒内未监听，看 $LogDir\frontend.err.log"
    }
}

# ---------------------------------------------------------------- 验证
Write-Step "验证"

foreach ($pair in @(
    @("http://127.0.0.1:8000/api/health", "后端"),
    @("http://127.0.0.1:5173/api/health", "前端代理")
)) {
    try {
        $response = Invoke-WebRequest -Uri $pair[0] `
            -TimeoutSec 15 -UseBasicParsing
        Write-Ok "$($pair[1]) HTTP $($response.StatusCode)"
    } catch {
        Write-Bad "$($pair[1]) 不可达：$($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "  打开： http://127.0.0.1:5173" -ForegroundColor Yellow
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  日志： $LogDir"
Write-Host "  停止： powershell -File scripts\stop-all.ps1"
Write-Host ""
