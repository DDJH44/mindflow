# 验证 start-all.ps1 里 Get-DockerEngine 的修复。
#
# 复现条件：Docker 引擎不可达（用 DOCKER_HOST 指向一个不存在的命名管道）。
# 断言两件事：
#   ① 原始写法（`docker info` 直接调用）在 Stop 下会**终止脚本**
#   ② 包装后的 Get-DockerEngine 不会终止，只返回空字符串
#
# 为什么要专门验：这个崩溃的表现是"脚本只说一句话就退出"，
# 看起来像脚本没写完，而不是像环境问题 —— 很容易被当成别的原因。

$ErrorActionPreference = "Stop"

$bogus = "npipe:////./pipe/definitely-not-running"

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

$env:DOCKER_HOST = $bogus

# ① 原始写法：应当抛终止错误（被 catch 到）
$rawDied = $false

try {
    $engine = docker info --format "{{.ServerVersion}}" 2>$null
    Write-Host "    [!!] 原始写法竟然没崩（引擎可能仍可达）"
} catch {
    $rawDied = $true
    Write-Host "    [OK] 原始写法确实被终止（复现成功）"
}

# ② 包装函数：应当存活并返回空
$wrapperSurvived = $true
$engine = ""

try {
    $engine = Get-DockerEngine
} catch {
    $wrapperSurvived = $false
}

Write-Host ""
Write-Host "  原始写法被终止 : $rawDied"
Write-Host "  包装后存活     : $wrapperSurvived"
Write-Host "  返回值为空     : $([string]::IsNullOrWhiteSpace($engine))"

if (-not $rawDied) {
    Write-Host ""
    Write-Host "  [!!] 没能复现崩溃 —— 无法证明修复有效"
    exit 1
}

if (-not $wrapperSurvived) {
    Write-Host ""
    Write-Host "  [!!] 包装函数仍然抛异常 —— 修复无效"
    exit 1
}

if (-not [string]::IsNullOrWhiteSpace($engine)) {
    Write-Host ""
    Write-Host "  [!!] 引擎不可达时返回值不为空 —— 调用方会误判为就绪"
    exit 1
}

Write-Host ""
Write-Host "  结论：崩溃已复现，且包装函数在同样条件下存活"
exit 0
