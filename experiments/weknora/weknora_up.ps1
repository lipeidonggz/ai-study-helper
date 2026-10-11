<#
.SYNOPSIS
  WeKnora 实验会话脚本：起 Docker 引擎 → 起 WeKnora 服务 → 等健康 → 日志落盘并跟随。

.DESCRIPTION
  为什么需要它（2026-10-08 的教训）：
    · Docker Desktop 的 AutoStart 是关的，引擎会自己退出；
    · 而 WeKnora 的逐次 LLM usage、批次统计（cited_chunks / uncited_slugs / new_slugs…）
      **只写在容器日志里**，容器一重启就没了（A5 那批已经丢过一次）。
  所以每次跑实验前先执行本脚本，它会把日志持续写到
    <repo>\data\tmp\weknora-logs\weknora-<时间戳>.log
  并且把"当前会话"记在 .current 里，之后 snapshot 默认追加到同一个文件。

.PARAMETER Action
  up        (默认) 起引擎 + 起服务 + 等健康 + 写会话文件头 + 快照一次 + 启动后台跟随
  status    只报告引擎/容器/健康检查状态（不动任何东西）
  snapshot  把当前容器日志追加到会话文件（实验跑完用这个收口）
  follow    只启动后台日志跟随（不起服务）
  stop      停掉后台跟随进程（不动容器）

.PARAMETER Session
  指定会话名/日志文件；省略时 up 会新建一个带时间戳的，其余动作用 .current 里记的那个。

.EXAMPLE
  .\experiments\weknora\weknora_up.ps1              # 开工
  .\experiments\weknora\weknora_up.ps1 -Action snapshot   # 收工前把日志落盘
  .\experiments\weknora\weknora_up.ps1 -Action status

.NOTES
  以管理员身份运行、或在普通终端直接跑都行；脚本自身不做任何删除操作。
#>
[CmdletBinding()]
param(
    [ValidateSet('up', 'status', 'snapshot', 'follow', 'stop')]
    [string]$Action = 'up',
    [string]$Session = '',
    [string]$Repo = 'D:\lida-data\vscode\WeKnora',
    [int]$HealthTimeoutSec = 180
)

# 注意：docker 常把正常信息写 stderr，若设 Stop，`2>&1` 会把它们变成
# 终止性错误并打断脚本（2026-10-08 踩过）。这里用 Continue，错误靠 $LASTEXITCODE 判。
$ErrorActionPreference = 'Continue'

$root       = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # ai-study-helper
$logDir     = Join-Path $root 'data\tmp\weknora-logs'
$currentPtr = Join-Path $logDir '.current'
$compose    = Join-Path $Repo 'docker-compose.yml'

function Get-DockerPath {
    $cmd = Get-Command docker -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin\docker.exe'),
            'C:\Program Files\Docker\Docker\resources\bin\docker.exe')) {
        if (Test-Path $p) { return $p }
    }
    throw "找不到 docker.exe：请确认 Docker Desktop 已安装，或把它的 bin 目录加进 PATH。"
}

# 传参必须用显式数组：把 docker 参数直接写在调用位置时，PowerShell 会把
# 形如 -d / -f 的短选项当"参数前缀"吞掉（2026-10-08 实测：`up -d` 里的
# -d 被吃掉，结果跑成前台 `up`，脚本卡在流式输出上）。
function Invoke-Docker {
    param([string[]]$DockerArgs)
    & $script:docker @DockerArgs 2>&1
}

function Test-AppHealth {
    # 不用 Invoke-WebRequest：PS 5.1 会走系统代理（本机 Clash），可能挂住。
    # 用 .NET 请求并显式 Proxy=$null，只连本机。
    try {
        $req = [System.Net.WebRequest]::Create('http://localhost:8080/health')
        $req.Proxy = $null
        $req.Timeout = 5000
        $resp = $req.GetResponse()
        $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
        $body = $sr.ReadToEnd()
        $resp.Close()
        return ($body -match 'ok')
    } catch { return $false }
}

function Test-Engine {
    $v = & $script:docker version --format '{{.Server.Version}}' 2>$null
    if (-not $v) { return $false }
    return [bool](("$v").Trim())
}

function Wait-Engine {
    param([int]$TimeoutSec = 120)
    if (Test-Engine) { return $true }
    Write-Host "Docker 引擎未运行，正在启动 Docker Desktop…"
    & $script:docker desktop start | Out-Null
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-Engine) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Get-SessionFile {
    if ($Session) {
        if ([System.IO.Path]::IsPathRooted($Session)) { return $Session }
        return (Join-Path $logDir "$Session.log")
    }
    if (Test-Path $currentPtr) {
        $p = (Get-Content $currentPtr -Raw).Trim()
        if ($p) { return $p }
    }
    return $null
}

function New-SessionFile {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    if ($Session) {
        $f = if ([System.IO.Path]::IsPathRooted($Session)) { $Session } else { Join-Path $logDir "$Session.log" }
    } else {
        $f = Join-Path $logDir ('weknora-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    Set-Content -Path $currentPtr -Value $f -Encoding UTF8
    return $f
}

function Add-Marker {
    param([string]$Text)
    $f = Get-SessionFile
    if (-not $f) { return }
    $line = "`n===== {0}  {1} =====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text
    Add-Content -Path $f -Value $line -Encoding UTF8
}

function Write-Snapshot {
    $f = Get-SessionFile
    if (-not $f) { Write-Warning "没有当前会话，先跑 -Action up（或指定 -Session）"; return }
    Add-Marker 'SNAPSHOT BEGIN'
    # compose logs 会带上服务名前缀；这里不加 --since，取容器当前保留的全部日志
    $out = Invoke-Docker @('compose', '-f', $compose, '--project-directory', $Repo, 'logs', '--timestamps')
    Add-Content -Path $f -Value $out -Encoding UTF8
    Add-Marker 'SNAPSHOT END'
    Write-Host ("已快照到：{0}（{1:N0} 行）" -f $f, @($out).Count)
}

function Start-Follow {
    $f = Get-SessionFile
    if (-not $f) { Write-Warning "没有当前会话，先跑 -Action up（或指定 -Session）"; return }
    $pidFile = "$f.follow.pid"
    if (Test-Path $pidFile) {
        $old = (Get-Content $pidFile -Raw).Trim()
        if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
            Write-Host "日志跟随已在运行（pid $old），跳过。"
            return
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    Add-Marker 'FOLLOW START'
    # 两个坑都要躲开：
    #   · 不能用 Start-Process -RedirectStandardOutput（会截断目标文件，把快照冲掉）；
    #   · 不能用 Start-Process cmd /c "… >> f"（引号会被拼坏，进程起来就死）。
    # 做法：生成一个小 helper 脚本，用 -File 启动它；helper 里用 *>> 追加所有流。
    # 再加两个坑：
    #   · PS 5.1 的 *>> 默认写 UTF-16LE，会和前面的 UTF-8 快照混编码 ⇒ 改成逐行 Add-Content -Encoding UTF8；
    #   · logs -f 默认先把整份历史倒一遍（刚才 15 秒里灌了 10MB 重复）⇒ 用 --since 只取新的。
    $sinceUtc = (Get-Date).ToUniversalTime().AddSeconds(-2).ToString('yyyy-MM-ddTHH:mm:ssZ')
    $helper = "$f.follow.ps1"
    $lines = @(
        '# 由 weknora_up.ps1 自动生成：跟随容器日志并追加到会话文件（UTF-8）',
        ('$docker = "{0}"' -f $script:docker),
        ('$dargs  = @("compose","-f","{0}","--project-directory","{1}","logs","-f","--timestamps","--since","{2}")' -f $compose, $Repo, $sinceUtc),
        ('$out    = "{0}"' -f $f),
        ('& $docker @dargs 2>&1 | ForEach-Object { Add-Content -LiteralPath $out -Value $_ -Encoding UTF8 }')
    )
    Set-Content -Path $helper -Value $lines -Encoding UTF8
    $proc = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $helper)) `
        -WindowStyle Hidden -PassThru
    Set-Content -Path $pidFile -Value $proc.Id -Encoding UTF8
    Write-Host ("日志跟随已启动（pid {0}）→ {1}" -f $proc.Id, $f)
}

function Stop-Follow {
    $f = Get-SessionFile
    if (-not $f) { Write-Warning "没有当前会话"; return }
    $pidFile = "$f.follow.pid"
    if (-not (Test-Path $pidFile)) { Write-Host "没有记录跟随进程。"; return }
    $target = (Get-Content $pidFile -Raw).Trim()
    if ($target) {
        Stop-Process -Id $target -Force -ErrorAction SilentlyContinue
        Write-Host "已停止日志跟随（pid $target）。"
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

function Wait-Healthy {
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-AppHealth) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Show-Status {
    Write-Host ("docker      : {0}" -f $script:docker)
    Write-Host ("engine      : {0}" -f $(if (Test-Engine) { (Invoke-Docker @('version', '--format', '{{.Server.Version}}')) } else { '未运行' }))
    if (Test-Engine) {
        Write-Host "`n-- containers --"
        Invoke-Docker @('compose', '-f', $compose, '--project-directory', $Repo, 'ps', '--format', "{{.Name}}`t{{.Status}}")
        Write-Host ("`napp /health : {0}" -f $(if (Test-AppHealth) { 'ok（200）' } else { '不可达' }))
    }
    $f = Get-SessionFile
    Write-Host ("`nsession log : {0}" -f $(if ($f) { $f } else { '（无）' }))
}

$script:docker = Get-DockerPath

switch ($Action) {
    'status' { Show-Status }
    'snapshot' { Write-Snapshot }
    'follow' { Start-Follow }
    'stop' { Stop-Follow }
    'up' {
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $file = New-SessionFile
        Add-Marker "SESSION START（repo=$Repo）"

        Write-Host "→ 1/4 检查 Docker 引擎…"
        if (-not (Wait-Engine)) { throw "Docker 引擎在 120 秒内没有就绪。" }
        Write-Host "引擎就绪：$((Invoke-Docker @('version', '--format', '{{.Server.Version}}')))"

        Write-Host "→ 2/4 compose up -d…"
        $upOut = Invoke-Docker @('compose', '-f', $compose, '--project-directory', $Repo, 'up', '-d')
        Write-Host ("   完成（{0} 行输出，退出码 {1}）" -f @($upOut).Count, $LASTEXITCODE)

        Write-Host "→ 3/4 等 app 健康检查…"
        if (Wait-Healthy) { Write-Host "app 健康：http://localhost:8080/health 200" }
        else { Write-Warning "app 在 $HealthTimeoutSec 秒内未通过健康检查，请看 status / 日志。" }

        Write-Host "→ 4/4 落盘日志并启动跟随…"
        Write-Snapshot
        Start-Follow
        Write-Host "`n开工完成。UI: http://localhost    日志: $file"
        Write-Host "收工时跑：.\experiments\weknora\weknora_up.ps1 -Action snapshot"
    }
}
