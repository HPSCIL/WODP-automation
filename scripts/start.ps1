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
    Write-Host '请自行准备 64 位 Python 3.12 或更新版本，激活相应环境或确认 PATH 中的 Python 命令可用，再重试。'
    Write-Host '安装方式由您自行决定；本技能只检查和提示，不安装软件或创建环境。'
    exit 2
}
$arguments = @('-X','utf8',$launcher)
if ($CheckOnly) { $arguments += '--check-env' }
$arguments += $args
try {
    & $python.Source @prefix @arguments
    $code = $LASTEXITCODE
    if ($code -ne 0 -and $prefix.Count -gt 0) {
        Write-Host '如启动器提示找不到 3.12，请自行准备兼容环境，并使用所选环境中的 Python 调用 scripts/start.py。'
    }
    exit $code
} catch {
    Write-Host '无法运行 Python：请检查 Python 安装和 PATH，推荐使用 64 位 Python 3.12。'
    exit 2
}
