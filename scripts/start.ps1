param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'start.py'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    Write-Host '技能文件不完整：缺少 scripts/start.py。请重新完整克隆或解压技能包。'
    exit 2
}
$python = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$prefix = @()
if ($python -and $python.Source -match '[\\/]WindowsApps[\\/]') { $python = $null }
if (-not $python) {
    $python = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $prefix = @('-3.12')
}
if (-not $python) {
    Write-Host '环境检查失败：未找到可用的 Python。'
    Write-Host '请安装 64 位 Python 3.12，安装时勾选 Add Python to PATH，然后重新打开终端重试。'
    Write-Host '本技能无法在未安装 Python 时启动；无需安装 C++ 编译器。'
    exit 2
}
$arguments = @('-X','utf8',$launcher)
if ($CheckOnly) { $arguments += '--check-env' }
$arguments += $args
try {
    & $python.Source @prefix @arguments
    $code = $LASTEXITCODE
    if ($code -ne 0 -and $prefix.Count -gt 0) {
        Write-Host '如 Python 启动器提示找不到 3.12，请安装 64 位 Python 3.12 并重新打开终端。'
    }
    exit $code
} catch {
    Write-Host '无法运行 Python：请检查 Python 安装和 PATH，推荐使用 64 位 Python 3.12。'
    exit 2
}
