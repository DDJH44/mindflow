# 给 .ps1 脚本补 UTF-8 BOM
#
# ## 为什么需要
#
# 本机是 **Windows PowerShell 5.1**（没有安装 PowerShell 7 的 `pwsh`）。
# 5.1 读 `.ps1` 时，若文件**没有 BOM**，会按**本地代码页**解析 ——
# 于是中文被截断成乱码：
#
#     "  完成。"    →   瀹屾垚銆?      ← 解析失败
#
# 报错是 `字符串缺少终止符` 与 `意外的标记 }`：
#
# ⚠️ **看起来像括号不配对，实际是编码问题。**
# 这个误导性很强 —— 人会去数括号，而不是去查编码。
#
# ## 什么时候会丢 BOM
#
# 实测：**用工具改写文件时会丢掉 BOM**（例如编辑器的"保存"、
# 或用脚本做文本替换）。因此改完 `.ps1` **必须复查**。
#
# ## 用法
#
#     powershell -File scripts\add-bom.ps1              # 处理 scripts\*.ps1
#     powershell -File scripts\add-bom.ps1 路径1 路径2  # 处理指定文件
#
# 已带 BOM 的文件会被跳过；非 UTF-8 的文件会报错而不是被静默改写。

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Paths
)

$ErrorActionPreference = "Stop"

$Bom = [byte[]](0xEF, 0xBB, 0xBF)

# 没给参数就处理本目录下所有 .ps1
if (-not $Paths -or $Paths.Count -eq 0) {
    $Paths = Get-ChildItem -Path $PSScriptRoot -Filter *.ps1 |
        Select-Object -ExpandProperty FullName
}

if (-not $Paths -or $Paths.Count -eq 0) {
    Write-Host "  没有找到 .ps1 文件"
    exit 0
}

Write-Host ""
Write-Host "==> 检查 $($Paths.Count) 个文件" -ForegroundColor Cyan

$fixed = 0
$alreadyOk = 0
$failed = 0

foreach ($path in $Paths) {
    if (-not (Test-Path $path)) {
        Write-Host "    [!!] 不存在：$path" -ForegroundColor Red
        $failed++
        continue
    }

    $name = Split-Path -Leaf $path
    $bytes = [System.IO.File]::ReadAllBytes($path)

    $hasBom = (
        $bytes.Length -ge 3 -and
        $bytes[0] -eq 0xEF -and
        $bytes[1] -eq 0xBB -and
        $bytes[2] -eq 0xBF
    )

    if ($hasBom) {
        Write-Host "    [OK] $name（已有 BOM）" -ForegroundColor Green
        $alreadyOk++
        continue
    }

    # 确认它确实是 UTF-8。
    #
    # 直接加 BOM 会把"文件本来就不是 UTF-8"这件事掩盖掉 ——
    # 那样 BOM 反而让问题更难查。
    try {
        $strict = New-Object System.Text.UTF8Encoding($false, $true)
        [void]$strict.GetString($bytes)
    } catch {
        Write-Host "    [!!] $name 不是合法 UTF-8，已跳过（请另存为 UTF-8）" `
            -ForegroundColor Red
        $failed++
        continue
    }

    [System.IO.File]::WriteAllBytes($path, $Bom + $bytes)
    Write-Host "    [修复] $name（补上 BOM，$($bytes.Length) 字节）" `
        -ForegroundColor Yellow
    $fixed++
}

# ---------------------------------------------------------------- 语法检查
Write-Host ""
Write-Host "==> 语法检查" -ForegroundColor Cyan

foreach ($path in $Paths) {
    if (-not (Test-Path $path)) { continue }

    $name = Split-Path -Leaf $path
    $errors = $null

    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref]$null, [ref]$errors
    )

    if ($errors.Count -eq 0) {
        Write-Host "    [OK] $name" -ForegroundColor Green
    } else {
        Write-Host "    [!!] $name 有 $($errors.Count) 处语法错误" `
            -ForegroundColor Red

        foreach ($item in $errors | Select-Object -First 5) {
            Write-Host "         行 $($item.Extent.StartLineNumber): $($item.Message)"
        }

        $failed++
    }
}

Write-Host ""
if ($failed -gt 0) {
    Write-Host "  补 BOM $fixed 个，已有 $alreadyOk 个，**有问题 $failed 个**" `
        -ForegroundColor Red
    exit 1
}

Write-Host "  补 BOM $fixed 个，已有 $alreadyOk 个，全部语法正确" `
    -ForegroundColor Green
Write-Host ""
exit 0
