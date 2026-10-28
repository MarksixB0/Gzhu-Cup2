<#
.SYNOPSIS
    以「数据隔离」方式启动本应用，供手动对话验收使用。

.DESCRIPTION
    手动聊天会把测试对话写进真实记忆库，从此污染所有后续测试。
    本脚本把应用的全部**可重定向写盘点**指向仓库内的 .manual-test/<tag>/，
    并在退出时逐项核对「真实数据是否被触碰」。

    隔离覆盖情况（依据代码实测，不是猜的）：

      已隔离（env 可重定向）
        LONG_MEMORY_DIR          → memory.sqlite3 / sessions.sqlite3 /
                                   care_runtime.json / pet_runtime.json /
                                   memory_privacy.json 全部改道
        GZHU_CONNECTION_PATH     → 含明文 Key 的 runtime_connection.json 改道
                                   （默认会把真实那份**复制**过来，Key 照常可用，
                                     但设置页的改动只落在副本上）

      无法隔离（config 里是硬编码常量，没有 env 钩子）
        PET_CARD_DIR             → PATCH /api/pet/greeting 会**写回角色卡源文件**。
                                   本脚本改为「启动前后指纹比对」+ 退出时提示还原命令。
                                   见 loader.py: card_dir() / set_greeting()

.PARAMETER Mode
    backend = 只起后端（配前端 npm run dev 用）
    desktop = 起 Electron 桌宠（开发态会继承本进程环境，故隔离同样生效）

.PARAMETER Tag
    本次运行的数据目录名（默认按时间戳生成，便于多次运行互不干扰）。

.PARAMETER Port
    后端端口（默认 8000）。桌宠会用 GZHU_BACKEND_PORT 作为起始端口。

.PARAMETER User
    覆盖 MEMORY_USER_KEY，用独立用户身份（默认沿用应用默认 local_user）。

.PARAMETER ApiKey
    覆盖 LLM_API_KEY。给了就不必依赖连接文件里的 Key。

.PARAMETER Fresh
    若同名 Tag 目录已存在，先删除再建（清空本次数据重来）。

.PARAMETER NoSeedConnection
    不把真实的 runtime_connection.json 复制进隔离目录。

.PARAMETER VerifyOnly
    不启动应用，只核对「真实数据是否被触碰」（对比该 Tag 目录里的启动基线）。

    存在的理由：Ctrl+C 停止时 Windows PowerShell 5.1 不保证执行 finally 块，
    于是"退出核对"会在最常用的停止方式下丢失。基线已在启动时落盘到
    .manual-test/<tag>/_baseline.json，故随时可以补做核对。

.EXAMPLE
    .\test\isolate-run.ps1                       # 后端，默认 8000，时间戳目录
.EXAMPLE
    .\test\isolate-run.ps1 -Mode desktop         # 桌宠（隔离同样生效）
.EXAMPLE
    .\test\isolate-run.ps1 -Tag chat-a -Fresh    # 固定目录名，可重复跑
.EXAMPLE
    .\test\isolate-run.ps1 -Port 8123 -User tester01 -ApiKey sk-xxx
.EXAMPLE
    .\test\isolate-run.ps1 -Tag chat-a -VerifyOnly   # 聊完之后补做核对
#>
# ⚠ 本文件必须保存为「UTF-8 带 BOM」。
#   Windows PowerShell 5.1 读取无 BOM 的 .ps1 时按系统 ANSI(GBK) 解码，中文会被拆坏并抛
#   "Unexpected token"。很多编辑器保存时会丢掉 BOM —— 若改动后报解析错误，重存一次即可：
#       Get-Content .\test\isolate-run.ps1 -Raw -Encoding UTF8 |
#           Set-Content .\test\isolate-run.ps1 -Encoding UTF8 -NoNewline
[CmdletBinding()]
param(
    [ValidateSet('backend', 'desktop')]
    [string]$Mode = 'backend',
    [string]$Tag = '',
    [int]$Port = 8000,
    [string]$User = '',
    [string]$ApiKey = '',
    [switch]$Fresh,
    [switch]$NoSeedConnection,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'

# ============================ 路径准备 ============================
$RepoRoot = Split-Path -Parent $PSScriptRoot          # 脚本在 test/ 下
if (-not (Test-Path (Join-Path $RepoRoot 'server.py'))) {
    # 兜底：万一脚本被挪走
    $RepoRoot = (Get-Location).Path
}

if (-not $Tag) { $Tag = 'run-' + (Get-Date -Format 'yyyyMMdd-HHmmss') }
$RunRoot  = Join-Path $RepoRoot '.manual-test'
$RunDir   = Join-Path $RunRoot $Tag
$DataDir  = Join-Path $RunDir 'memory_data'

if ($Fresh -and (Test-Path $RunDir)) {
    Remove-Item -Recurse -Force $RunDir
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

# ---- 真实数据位置（只读引用，绝不改写）----
$RealMemDir  = Join-Path $RepoRoot 'app\Agent\memory_data'
$RealConn    = Join-Path $RepoRoot 'app\Agent\config\runtime_connection.json'
$CardDir     = Join-Path $RepoRoot 'app\Agent\pet\character'

# ============================ 指纹工具 ============================
function Get-Fingerprint {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @() }
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) {
        return @(('{0}|{1}|{2}' -f $item.Name, $item.Length, $item.LastWriteTimeUtc.Ticks))
    }
    return @(
        Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            ForEach-Object {
                '{0}|{1}|{2}' -f $_.FullName.Substring($Path.Length), $_.Length, $_.LastWriteTimeUtc.Ticks
            }
    )
}

function Compare-Fingerprint {
    param([string]$Label, [array]$Before, [array]$After)
    $diff = Compare-Object -ReferenceObject $Before -DifferenceObject $After
    if (-not $diff) {
        Write-Host ("  [OK]   {0} 未被触碰" -f $Label) -ForegroundColor Green
        return $true
    }
    Write-Host ("  [!!]   {0} 发生了变化：" -f $Label) -ForegroundColor Red
    $diff | ForEach-Object {
        $mark = if ($_.SideIndicator -eq '=>') { '新增/改动' } else { '消失' }
        Write-Host ("         {0} {1}" -f $mark, $_.InputObject)
    }
    return $false
}

function Show-Verification {
    param([string]$RunDirPath)

    $baselineFile = Join-Path $RunDirPath '_baseline.json'
    if (-not (Test-Path $baselineFile)) {
        Write-Host ''
        Write-Host (' [错误] 找不到基线文件：{0}' -f $baselineFile) -ForegroundColor Red
        Write-Host '        该目录不是本脚本创建的，或已被清理，无法核对。' -ForegroundColor Red
        return $false
    }
    $b = Get-Content -LiteralPath $baselineFile -Raw -Encoding UTF8 | ConvertFrom-Json

    Write-Host ''
    Write-Host '===================================================================' -ForegroundColor Cyan
    Write-Host ' 真实数据是否被触碰（对比启动时落盘的基线）' -ForegroundColor Cyan
    Write-Host (' 基线时间: {0}' -f $b.generated) -ForegroundColor DarkGray
    Write-Host '===================================================================' -ForegroundColor Cyan

    $okMem   = Compare-Fingerprint '真实记忆目录 app/Agent/memory_data' @($b.memory) (Get-Fingerprint $b.memory_dir)
    $okConn  = Compare-Fingerprint '真实 API 连接文件（含 Key）'          @($b.conn)   (Get-Fingerprint $b.connection_file)
    $okCards = Compare-Fingerprint '角色卡目录（未隔离项）'               @($b.cards)  (Get-Fingerprint $b.card_dir)

    Write-Host ''
    Write-Host (' 本次测试数据全部落在: {0}' -f $RunDirPath)
    $produced = @(
        Get-ChildItem -LiteralPath $RunDirPath -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne '_baseline.json' }
    )
    if ($produced.Count -gt 0) {
        Write-Host ' 产物：'
        $produced | Sort-Object FullName | ForEach-Object {
            Write-Host ('   {0}  ({1} bytes)' -f $_.FullName.Substring($RunDirPath.Length), $_.Length)
        }
    }
    else {
        Write-Host ' 产物：（空——数据目录未被写入，请确认真的发过消息）' -ForegroundColor Yellow
    }

    if (-not $okCards) {
        Write-Host ''
        Write-Host ' 角色卡被改动了（很可能是测了「开场白」）。还原：' -ForegroundColor Yellow
        Write-Host ('   git checkout -- "{0}"' -f (Join-Path $RepoRoot 'app\Agent\pet\character')) -ForegroundColor Yellow
    }
    if (-not $okMem) {
        Write-Host ''
        Write-Host ' [警告] 真实记忆目录被写入了！请检查是否有其他进程用默认路径启动过。' -ForegroundColor Red
    }

    Write-Host ''
    Write-Host ' 一键清零本次测试数据：' -ForegroundColor DarkGray
    Write-Host ('   Remove-Item -Recurse -Force "{0}"' -f $RunDirPath) -ForegroundColor DarkGray
    Write-Host '===================================================================' -ForegroundColor Cyan

    return ($okMem -and $okConn -and $okCards)
}

# ============================ 只核对，不启动 ============================
if ($VerifyOnly) {
    if (Show-Verification -RunDirPath $RunDir) { exit 0 } else { exit 1 }
}

# ---- 启动前快照 ----
$snapMem   = Get-Fingerprint $RealMemDir
$snapConn  = Get-Fingerprint $RealConn
$snapCards = Get-Fingerprint $CardDir

# ---- 基线落盘 ----
# Ctrl+C 停止时 PowerShell 5.1 不保证执行 finally，故基线必须落盘，
# 否则"退出核对"会在最常用的停止方式下静默丢失（-VerifyOnly 靠它补做）。
[ordered]@{
    generated       = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    memory_dir      = $RealMemDir
    connection_file = $RealConn
    card_dir        = $CardDir
    memory          = $snapMem
    conn            = $snapConn
    cards           = $snapCards
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $RunDir '_baseline.json') -Encoding UTF8

# ============================ 环境变量 ============================
$env:LONG_MEMORY_DIR         = $DataDir
$env:LONG_MEMORY_BACKEND     = 'sqlite'          # 显式声明：要的就是落盘
$env:GZHU_CONNECTION_PATH    = Join-Path $RunDir 'runtime_connection.json'
$env:GZHU_BACKEND_PORT       = "$Port"
if ($User)   { $env:MEMORY_USER_KEY = $User }
if ($ApiKey) { $env:LLM_API_KEY    = $ApiKey }

# ---- 把真实的连接文件复制过来：Key 照常可用，改动只落副本 ----
$seeded = $false
if (-not $NoSeedConnection -and (Test-Path $RealConn) -and -not (Test-Path $env:GZHU_CONNECTION_PATH)) {
    Copy-Item -LiteralPath $RealConn -Destination $env:GZHU_CONNECTION_PATH -Force
    $seeded = $true
}

# ---- 让应用自己报告"隔离是否真的生效" ----
# 只输出 ASCII 判定结果：Python 的 stdout 在本机是 GBK，中文路径经管道会被解码成乱码，
# 而 PowerShell 自己打印中文是正常的。故让 Python 只回 OK / MISMATCH，路径由本脚本打印。
$probe = @"
import sys, os
sys.path.insert(0, os.path.join(os.getcwd(), "app", "Agent"))
from config import config as cfg
from config import runtime_connection as rc
data = r"$DataDir"
run  = r"$RunDir"
def verdict(path, expected):
    a = os.path.normcase(os.path.abspath(str(path)))
    b = os.path.normcase(os.path.abspath(expected))
    return "OK" if a == b else "MISMATCH"
card = os.path.normcase(os.path.abspath(str(cfg.PET_CARD_DIR)))
card_isolated = card.startswith(os.path.normcase(data)) or card.startswith(os.path.normcase(run))
print("MEMORY_DIR      = " + verdict(cfg.MEMORY_DIR, data))
print("SESSIONS_DB_DIR = " + verdict(os.path.dirname(cfg.SESSIONS_DB_PATH), data))
print("CARE_STATE_DIR  = " + verdict(os.path.dirname(cfg.CARE_STATE_PATH), data))
print("PET_STATE_DIR   = " + verdict(os.path.dirname(cfg.PET_STATE_PATH), data))
print("PRIVACY_DIR     = " + verdict(os.path.dirname(cfg.MEMORY_PRIVACY_PATH), data))
print("CONN_FILE_DIR   = " + verdict(os.path.dirname(str(rc._CONN_FILE)), run))
print("CARD_DIR        = " + ("ISOLATED" if card_isolated else "NOT-ISOLATED(by-design)"))
print("BACKEND         = " + str(os.getenv("LONG_MEMORY_BACKEND", "unset")))
print("DEV_FAST_MODE   = " + str(cfg.DEV_FAST_MODE))
"@
$probeOut = @($probe | & (Join-Path $RepoRoot '.venv\Scripts\python.exe') - 2>&1)
$probeText = ($probeOut | Out-String)
$isolationOk = ($probeText -notmatch 'MISMATCH')

# ============================ 打印隔离地图 ============================
Write-Host ''
Write-Host '===================================================================' -ForegroundColor Cyan
Write-Host ' 数据隔离启动器' -ForegroundColor Cyan
Write-Host '===================================================================' -ForegroundColor Cyan
Write-Host (" 仓库根      : {0}" -f $RepoRoot)
Write-Host (" 本次数据目录: {0}" -f $RunDir)
Write-Host (" 启动模式    : {0}   端口: {1}" -f $Mode, $Port)
Write-Host ''
Write-Host ' 应用自检（由 config 实际解析结果判定，不是推测）：' -ForegroundColor Yellow
$probeOut | ForEach-Object { Write-Host ("   {0}" -f $_) }
if (-not $isolationOk) {
    Write-Host ' [警告] 出现 MISMATCH：隔离未完全生效，请勿用真实数据继续聊天！' -ForegroundColor Red
}
Write-Host ''
Write-Host ' 隔离状态：'
Write-Host '   [已隔离] 记忆 / 会话 / 关怀 / 桌宠 / 隐私状态  → .manual-test 目录'
Write-Host ('   [已隔离] API 连接文件（含 Key）                → {0}' -f $(if ($seeded) { '已复制真实 Key，改动只落副本' } else { '独立空文件' }))
Write-Host '   [未隔离] 角色卡目录（PET_CARD_DIR 无 env 钩子）' -ForegroundColor Yellow
Write-Host '            改「开场白」会写回源文件；退出时本脚本会比对并提示还原' -ForegroundColor DarkGray
Write-Host ''

if ($probeText -match 'DEV_FAST_MODE\s*=\s*True') {
    Write-Host ' [提醒] DEV_FAST_MODE=True：关怀 36 秒/轮、免扰关闭、记忆 2 轮即固化。' -ForegroundColor Yellow
    Write-Host '        测「真实体验」请把它改回 False（config.py 末尾，是源码常量，无法用 env 覆盖）。' -ForegroundColor Yellow
}
Write-Host ''
Write-Host ' 收工清理：' -ForegroundColor DarkGray
Write-Host ('   Remove-Item -Recurse -Force "{0}"' -f $RunDir) -ForegroundColor DarkGray
Write-Host '===================================================================' -ForegroundColor Cyan
Write-Host ''

# ============================ 启动 ============================
# 关键：原生命令(uvicorn / npm)会往 stderr 写 INFO 日志。在 Windows PowerShell 5.1 下
# $ErrorActionPreference='Stop' 会把这类 stderr 当作**终止性错误**，导致脚本在后端
# 还没起来时就中断。故启动阶段一律放宽为 Continue，退出码自己看 $LASTEXITCODE。
$ErrorActionPreference = 'Continue'
$exitCode = 0
try {
    if ($Mode -eq 'backend') {
        & (Join-Path $RepoRoot '.venv\Scripts\python.exe') -m uvicorn server:app --host 127.0.0.1 --port $Port
        $exitCode = $LASTEXITCODE
    }
    else {
        Push-Location (Join-Path $RepoRoot 'desktop')
        try {
            # 开发态 Electron 的 backendEnv() 直接返回 process.env → 后端继承上面所有隔离变量
            npm start
            $exitCode = $LASTEXITCODE
        }
        finally { Pop-Location }
    }
}
finally {
    # ============================ 退出核对 ============================
    # ⚠ Ctrl+C 停止时，Windows PowerShell 5.1 不保证执行本块 —— 基线已在启动时
    #   落盘到 _baseline.json，事后可用 -VerifyOnly 补做核对。
    [void](Show-Verification -RunDirPath $RunDir)
}

exit $exitCode
