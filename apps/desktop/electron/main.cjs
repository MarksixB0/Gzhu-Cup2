// Gzhu-Cup2 桌面应用主进程。
//
// 「单实例 + 双窗口 + 托盘常驻」的桌面程序：
//   - 桌宠窗口：透明 / 无边框 / 置顶，常驻桌面；
//   - 主窗口：功能界面（对话 / 日历 / 设置），由桌宠右键打开；
//   - **托盘常驻**：关闭窗口只是缩到托盘后台运行，**只有菜单里的「退出」才真正结束**
//     （连同后端子进程一起带走）。
//
// 四件事由本进程统一负责：
//   1. **后端托管** —— 启动时挑一个空闲端口拉起 Python(FastAPI) 后端，再把地址注入
//      渲染进程。默认 8000，被占用则自动 +1 试探，避免端口冲突；
//   2. **单实例** —— 第二次启动不会新开进程，而是把已有窗口提到前面；
//   3. **干净退出 + 残留自愈** —— 退出时连同后端进程树一起结束；启动时还会扫描并清掉
//      历史遗留的本项目后端进程（强杀 Electron 会留下它们，占内存也占端口）；
//   4. **首次启动免责声明** —— 只在用户第一次使用时弹一次系统对话框（见 maybeShowDisclaimer）。
//
// 路径解析：开发态用仓库根，打包态用 resourcesPath（见 resolvePaths）。
//
// 打包注意（P5）：安装包需设置「卸载时删除用户数据目录」，否则 userData 里的配置会残留。
// Electron Builder 对应 `nsis.deleteAppDataOnUninstall`。
//
// 素材不经过 Vite：35MB 的帧走构建管线既慢又会被复制进 dist。自定义协议让开发态与
// 打包态用同一套 URL。

const { app, BrowserWindow, ipcMain, Menu, Tray, Notification, nativeImage, net, protocol, screen, shell, dialog } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const nodeNet = require('node:net')
const { execFileSync, spawn } = require('node:child_process')
const { pathToFileURL } = require('node:url')

// ---------------------------------------------------------------- 路径解析
// 开发态：/<repo>/desktop/electron/main.cjs → 仓库根 = 上两级。
// 打包态：__dirname 在 app.asar 内，仓库根不存在，一切资源改从 process.resourcesPath 取
//         （由 electron-builder 的 extraResources 拷贝进去）。
// 把「两种形态」收在一个函数里，避免每处路径各自判断 app.isPackaged。
const PACKAGED = app.isPackaged
const REPO_ROOT = path.join(__dirname, '..', '..', '..')
/** 打包态的资源根：<安装目录>/resources */
const RESOURCES_DIR = PACKAGED ? process.resourcesPath : REPO_ROOT

/** 桌宠动画素材目录（pet/assets）。 */
const ASSETS_DIR = PACKAGED
  ? path.join(RESOURCES_DIR, 'pet-assets')
  : path.join(REPO_ROOT, 'pet', 'assets')
/** 功能界面（apps/web）构建产物目录。 */
const FRONTEND_DIST = PACKAGED
  ? path.join(RESOURCES_DIR, 'frontend-dist')
  : path.join(REPO_ROOT, 'apps', 'web', 'dist')
/** 随包分发的 Python 后端可执行文件（打包态才有）。 */
const BACKEND_EXE = PACKAGED ? path.join(RESOURCES_DIR, 'backend', 'gzhu-backend.exe') : ''
/**
 * 托盘图标。打包态**必须**走 extraResources（`resources/tray.png`），不能用 asar 内的
 * `assets/tray.png`：`nativeImage.createFromPath` 走 C++ 层的文件读取、**不认 asar**，
 * 指针落进 asar 只会得到空图 —— 表现为「托盘图标静默消失」。
 * 图标缺失时下面的 createTray 会告警，但应用仍能启动。
 */
const TRAY_ICON = PACKAGED
  ? path.join(RESOURCES_DIR, 'tray.png')
  : path.join(__dirname, '..', 'assets', 'tray.png')

// 开发期可指向各自的 Vite dev server；留空则加载内嵌构建产物
const PET_DEV_SERVER_URL = process.env.PET_DEV_SERVER_URL || ''
const MAIN_DEV_SERVER_URL = process.env.PET_MAIN_DEV_SERVER_URL || ''

// 桌宠窗口尺寸。拖拽落位时会在 setBounds 里重申这两个值：
// 只调 setPosition（仅改坐标、不声明尺寸）时，部分 Windows + DPI 缩放组合会在移动
// 过程中把窗口按缩放比例放大，表现为「越拖越大」。用 setBounds 钉死宽高可根除。
const WIN_WIDTH = 360
const WIN_HEIGHT = 480

// 后端端口：默认 8000，被占用则依次 +1（最多试 100 个）
const BACKEND_PORT_START = Number(process.env.GZHU_BACKEND_PORT || 8000)
const BACKEND_PORT_TRIES = 100
const BACKEND_READY_TIMEOUT_MS = 40_000

// ---------------------------------------------------------------- 全局快捷键（已移除）
// 曾经给「唤起输入框 / 打开主界面 / 显隐桌宠 / 勿扰切换」配过 Ctrl+Alt+<字母> 全局热键。
// 全局快捷键会**独占**组合键,极易与用户机器上其它软件的既有快捷键冲突,
// 已整体移除;对应功能全部保留在托盘右键菜单里(显隐 / 主界面 / 勿扰 / 退出)。

// ---------------------------------------------------------------- 单实例
// 必须在任何窗口创建之前判定；拿不到锁说明已有实例在跑，直接退出。
if (!app.requestSingleInstanceLock()) {
  app.quit()
  process.exit(0)
}

protocol.registerSchemesAsPrivileged([
  {
    scheme: 'pet-asset',
    // corsEnabled: 让渲染进程能用 canvas 采样素材像素（点击穿透的逐像素命中判定），
    // 否则跨源会污染 canvas，getImageData 抛错。
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true },
  },
])

/** @type {BrowserWindow | null} */
let petWin = null
/** @type {BrowserWindow | null} */
let mainWin = null
/** @type {Tray | null} */
let tray = null
/** @type {import('node:child_process').ChildProcess | null} */
let backendProc = null
/** 后端地址；窗口创建前就已确定（端口探测很快），因此可直接注入渲染进程 */
let backendUrl = `http://127.0.0.1:${BACKEND_PORT_START}`
/**
 * **真正的退出标志**。只有托盘「退出」把它置 true；
 * 其余情况（用户关窗）一律拦下并隐藏到托盘。
 */
let quitting = false

// ---------------------------------------------------------------- 素材协议
function registerAssetProtocol() {
  protocol.handle('pet-asset', (request) => {
    const url = new URL(request.url)
    // pet-asset://vup/IDEL/Boring/A_Nomal/x.png -> host="vup", pathname="/IDEL/..."
    const rel = decodeURIComponent(`${url.host}${url.pathname}`)
    const file = path.normalize(path.join(ASSETS_DIR, rel))
    // 目录穿越防护：解析后必须仍在素材根内
    if (file !== ASSETS_DIR && !file.startsWith(ASSETS_DIR + path.sep)) {
      return new Response('forbidden', { status: 403 })
    }
    return net.fetch(pathToFileURL(file).toString())
      .then((res) => {
        // 带 CORS 头：渲染进程 canvas 采样像素需要（点击穿透逐像素命中）。
        const headers = new Headers(res.headers)
        headers.set('Access-Control-Allow-Origin', '*')
        return new Response(res.body, { status: res.status, headers })
      })
      .catch((err) => {
        // 冷盘 / 杀软实时扫描会让文件读取偶发失败。这里绝不能把拒绝的 Promise
        // 抛回 protocol.handle：渲染层 <img> 只会得到「没有任何可重试信号」的
        // 破图。返回可读的 500，交给渲染层的重试自愈兜底。
        // 只记路径，**绝不**记文件内容。
        console.warn(`[assets] 读取失败: ${file} (${err.message})`)
        return new Response('asset read failed', { status: 500 })
      })
  })
}

// ---------------------------------------------------------------- 素材预热
/**
 * 启动时把素材先「读一遍」，喂给系统文件缓存。
 *
 * 为什么需要：安装包首次运行时，素材是刚从安装包里解压出来的 ~30MB 冷文件，
 * 冷盘 + 杀软实时扫描会让第一次读取偶发失败。失败一旦落在渲染层，就是一张
 * **永久破图**（`preloadFrames` 会吞掉单帧错误，`<img>` 自身没有重试入口，
 * 重开应用才恢复）。这里在开窗口之前先读一遍（读完即弃，不保留 buffer），
 * 让文件进系统缓存、也让杀软完成扫描，尽量把失败窗口挡在窗口创建之前；
 * 渲染层的重试是第二道防线。
 *
 * 边界：单文件失败只记调试日志、不阻塞；整体 10s 硬超时，最坏情况也不拖住启动。
 */
const WARM_CONCURRENCY = 16
const WARM_TIMEOUT_MS = 10_000
/** 预热每进程只跑一次（whenReady 本身也只触发一次，这里是显式约束）。 */
let assetWarmStarted = false

/** 递归收集目录下全部 .png 的绝对路径（素材目录层级不深，不需要 glob 依赖）。 */
async function collectPngs(dir, out) {
  let entries
  try {
    entries = await fs.promises.readdir(dir, { withFileTypes: true })
  } catch (err) {
    console.debug(`[assets] 预热跳过目录 ${dir}: ${err.message}`)
    return
  }
  for (const entry of entries) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) await collectPngs(full, out)
    else if (entry.isFile() && entry.name.toLowerCase().endsWith('.png')) out.push(full)
  }
}

/** 有界并发（16）把 files 全部读一遍；单个失败只记日志、不抛出。 */
async function readAll(files) {
  let cursor = 0
  const worker = async () => {
    while (cursor < files.length) {
      const file = files[cursor]
      cursor += 1
      try {
        // 读完即弃：目的只是让系统缓存持有这些数据块，buffer 不做任何保留
        await fs.promises.readFile(file)
      } catch (err) {
        console.debug(`[assets] 预热读取失败（忽略）: ${file} (${err.message})`)
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(WARM_CONCURRENCY, files.length) }, worker))
}

/** 预热全部素材：清单 + vup 下所有 png。自身永不抛出。 */
async function warmAssetCache() {
  const root = path.join(ASSETS_DIR, 'vup')
  const files = [path.join(root, 'manifest.json')]
  await collectPngs(root, files)
  console.log(`[assets] 预热 ${files.length} 个文件（并发 ${WARM_CONCURRENCY}）`)
  await readAll(files)
}

/**
 * 预热入口：每进程一次 + 整体硬超时。
 * Promise.race 保证无论磁盘多慢，启动都不会卡在预热上；超时后预热在后台继续，
 * 其内部错误已被吞掉，不会产生 unhandled rejection。
 */
function warmAssetCacheOnce() {
  if (assetWarmStarted) return Promise.resolve()
  assetWarmStarted = true
  let timer = null
  const timeout = new Promise((resolve) => {
    timer = setTimeout(resolve, WARM_TIMEOUT_MS)
  })
  return Promise.race([warmAssetCache(), timeout])
    .catch(() => {})
    .finally(() => {
      if (timer !== null) clearTimeout(timer)
    })
}

// ---------------------------------------------------------------- 后端托管
/** 从 start 起找一个能 listen 的端口。 */
function findFreePort(start, triesLeft = BACKEND_PORT_TRIES) {
  return new Promise((resolve, reject) => {
    const probe = nodeNet.createServer()
    probe.unref()
    probe.on('error', (err) => {
      probe.close()
      if (err && err.code === 'EADDRINUSE' && triesLeft > 0) {
        resolve(findFreePort(start + 1, triesLeft - 1))
      } else {
        reject(err)
      }
    })
    probe.listen(start, '127.0.0.1', () => {
      const address = probe.address()
      const port = address && typeof address === 'object' ? address.port : start
      probe.close(() => resolve(port))
    })
  })
}

/** 轮询 /api/health 直到后端可用。 */
async function waitForBackend(timeoutMs = BACKEND_READY_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await net.fetch(`${backendUrl}/api/health`)
      if (res.ok) return true
    } catch {
      // 还没起来：继续等
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return false
}

// ---- 残留进程自愈 ----
// 为什么需要：Electron 被「任务管理器强杀」时不会走 before-quit，后端子进程
// (uv → python) 会变成孤儿继续占内存与端口 —— 反复调试几次就会堆出一串
// 8001/8002/8003… 的后端，机器明显变慢。
//
// 早先用「PID 落盘」只记得住最后一个，清不掉更早的历史遗留；这里改成启动时
// **按命令行扫描**：凡是命令行同时包含本项目路径与 uvicorn 的 python/uv/uvicorn
// 进程，一律结束。既清得干净，也不会误伤别的项目。
// 扫描名单必须含 uvicorn.exe：开发态直接跑 .venv/Scripts/uvicorn.exe，只扫
// python.exe/uv.exe 会漏掉它，强杀后留一堆孤儿。

function killStaleBackends() {
  if (process.platform !== 'win32') return
  // 打包态没有 uv/python 子进程（后端是 exe），残留扫描反而可能误伤 —— 只清开发态。
  if (PACKAGED) return
  const cmd =
    `Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='uv.exe' or Name='uvicorn.exe'" | ` +
    `Where-Object { $_.CommandLine -like '*${REPO_ROOT}*uvicorn*' } | ` +
    `ForEach-Object { Write-Output $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }`
  try {
    const out = execFileSync('powershell', ['-NoProfile', '-Command', cmd], {
      encoding: 'utf8',
      timeout: 20_000,
    })
    // 只在确实清掉了东西时才出声：没有残留却打印「已清理」会误导排障
    const killed = out.split(/\s+/).filter((s) => /^\d+$/.test(s)).length
    if (killed > 0) console.log(`[backend] 清理了 ${killed} 个历史遗留的后端进程`)
  } catch {
    /* 没有残留、或 PowerShell 不可用：都不影响启动 */
  }
}

/** 结束一个进程及其子进程（uv → python 是进程树）。 */
function killTree(pid) {
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(pid), '/T', '/F'], { stdio: 'ignore' })
    } else {
      process.kill(pid, 'SIGTERM')
    }
  } catch {
    /* 进程已经不在了 */
  }
}

/**
 * 用户数据根目录 —— 数据跟在程序自己旁边，三种形态各落一处：
 *
 *   便携版（portable）  <exe 所在目录>/data   ← 拷走整个文件夹就能带着用
 *   安装版             <安装目录>/data       ← NSIS 默认装 %LOCALAPPDATA%\Programs(可写)
 *   开发态             Electron userData     ← 不污染仓库，也和打包态分开
 *
 * 为什么不再一律用 userData:便携版的意义就是"数据随程序走",放 %APPDATA% 就带不走;
 * 安装版同理 —— 安装位置由用户选定(allowToChangeInstallationDirectory),数据跟在旁边。
 */
function dataRoot() {
  if (!PACKAGED) return app.getPath('userData')
  // electron-builder 的 portable 目标注入此变量,指向"用户放 exe 的那个目录"
  const portableDir = process.env.PORTABLE_EXECUTABLE_DIR
  if (portableDir) return path.join(portableDir, 'data')
  return path.join(path.dirname(app.getPath('exe')), 'data')
}

/** 目录是否真的可写(万一被装到只读位置,要能退回去,而不是让保存直接报权限错误)。 */
function writable(dir) {
  try {
    fs.mkdirSync(dir, { recursive: true })
    const probe = path.join(dir, '.write-probe')
    fs.writeFileSync(probe, '')
    fs.unlinkSync(probe)
    return true
  } catch {
    return false
  }
}

/**
 * 把旧位置的数据搬进新数据根(仅在"新位置还没有、旧位置有"时做一次)。
 *
 * 背景:1.0 把用户数据放在 Electron userData(%APPDATA%\Gzhu-Cup2\memory_data);
 * 1.1 起改成"数据跟在 exe 旁边",落点变了 —— 少了这一步,老用户的长期记忆会像
 * 凭空消失(其实还在旧位置躺着)。移动失败只告警,绝不阻断启动。
 *
 * 产品名由 Gzhu-Cup2 改为「心伴」后,`app.getPath('userData')` 也会跟着变成
 * `%APPDATA%\心伴`。所以除了当前 userData,还要显式认一下**改名前的** `%APPDATA%\Gzhu-Cup2`
 * —— 否则靠 userData 回落存下的那批数据会永远迁不过来。
 */
function migrateLegacyData(root) {
  const legacyDirs = [
    app.getPath('userData'),
    path.join(app.getPath('appData'), 'Gzhu-Cup2'),
  ]
  for (const legacyDir of legacyDirs) {
    const moves = [
      [path.join(legacyDir, 'memory_data'), path.join(root, 'memory')],
      [path.join(legacyDir, 'runtime_connection.json'),
       path.join(root, 'runtime_connection.json')],
    ]
    for (const [from, to] of moves) {
      try {
        if (!fs.existsSync(from) || fs.existsSync(to)) continue
        fs.mkdirSync(path.dirname(to), { recursive: true })
        fs.renameSync(from, to)
        console.log(`[data] 旧数据已迁移: ${from} -> ${to}`)
      } catch (e) {
        console.warn(`[data] 旧数据迁移失败(不影响启动) ${from}: ${e.message}`)
      }
    }
  }
}

/**
 * 随包分发的**初始角色卡**卡名(资源目录见 package.json 的 extraResources: `card-seed`)。
 *
 * 设计取舍:安装包**不把人格塞进只读资源当"预设人格"**,而是在首启时把它**装进可写数据目录** ——
 * 卡因此是"用户自己的数据":可编辑、可删除、可换成别的卡,与「角色卡是人格唯一来源」的方针一致。
 * 对评委/新用户友好(装完即有人格),又不违反原来那条"不在包内预置只读人格"的初衷。
 *
 * 两条边界:
 *   1. **只在没有任何卡时投放** —— 老用户升级不会被凭空塞一张卡;
 *   2. **只做一次**(落一个标记文件)—— 用户把它删掉之后不会被复活。
 */
const SEED_CARD_NAME = 'Sari'

function seedInitialCard(root) {
  const marker = path.join(root, '.cards-seeded')
  if (fs.existsSync(marker)) return
  try {
    const src = path.join(process.resourcesPath, 'card-seed', `${SEED_CARD_NAME}.json`)
    const cardsDir = path.join(root, 'cards')
    const dst = path.join(cardsDir, `${SEED_CARD_NAME}.json`)
    const existing = fs.existsSync(cardsDir)
      ? fs.readdirSync(cardsDir).filter((f) => f.toLowerCase().endsWith('.json'))
      : []
    fs.mkdirSync(cardsDir, { recursive: true })
    if (fs.existsSync(src) && existing.length === 0 && !fs.existsSync(dst)) {
      fs.copyFileSync(src, dst)
      console.log(`[cards] 已放入初始角色卡: ${SEED_CARD_NAME}`)
    }
    // 出厂默认**启用**它。用户若在界面里显式选过「不搭载任何卡」,active_card.json 已存在
    // (内容是空串 name),这里不动 —— 尊重用户的选择。
    const activePath = path.join(root, 'memory', 'active_card.json')
    if (!fs.existsSync(activePath)) {
      fs.mkdirSync(path.dirname(activePath), { recursive: true })
      fs.writeFileSync(
        activePath, `${JSON.stringify({ name: SEED_CARD_NAME }, null, 2)}\n`, 'utf8')
      console.log('[cards] 已默认启用初始角色卡')
    }
    fs.writeFileSync(marker, `${new Date().toISOString()}\n`, 'utf8')
  } catch (e) {
    // 投放失败只是"少了张初始卡",绝不能挡启动
    console.warn(`[cards] 初始角色卡投放失败(不影响启动): ${e.message}`)
  }
}

/**
 * 后端进程的环境变量。
 *
 * 打包态把**四个数据落点**统一指向数据根 —— 否则「保存设置」「新建角色卡」都会直接
 * 报权限错误(安装目录只读)。开发态**不设**,数据留在仓库内方便直接翻看。
 *
 * 变量名由后端定义:
 *   config.MEMORY_DIR          <- LONG_MEMORY_DIR
 *   config.runtime_connection  <- GZHU_CONNECTION_PATH
 *   config.PET_CARD_DIR        <- GZHU_CARD_DIR
 *   (GZHU_DATA_DIR 只是把这个根显式标出来,便于排障与以后加新的数据域)
 */
function backendEnv() {
  if (!PACKAGED) return process.env

  let root = dataRoot()
  if (!writable(root)) {
    const fallback = app.getPath('userData')
    console.warn(`[data] ${root} 不可写,回退到 ${fallback}`)
    root = fallback
  }
  migrateLegacyData(root)
  seedInitialCard(root)

  return {
    ...process.env,
    GZHU_DATA_DIR: root,
    LONG_MEMORY_DIR: path.join(root, 'memory'),
    GZHU_CONNECTION_PATH: path.join(root, 'runtime_connection.json'),
    GZHU_CARD_DIR: path.join(root, 'cards'),
  }
}

/**
 * 开发态后端启动命令：优先仓库 venv 里的 uvicorn，其次 `uv run uvicorn`。
 *
 * 为什么不能只靠 `uv`：uv 默认装在 `%USERPROFILE%\.local\bin`，这个目录通常
 * **不在系统 PATH 里**（双击/快捷方式启动的 Electron 继承的是系统 PATH，
 * 而不是某个终端的 shell 配置）。裸 spawn('uv') 会直接 ENOENT，后端从未
 * 启动 —— 表现就是「记忆和模型连接一起失灵」，且界面上没有任何提示。
 * venv 里的 uvicorn.exe 不依赖 PATH，最稳。
 */
function venvUvicorn() {
  const candidates = process.platform === 'win32'
    ? [path.join(REPO_ROOT, '.venv', 'Scripts', 'uvicorn.exe')]
    : [path.join(REPO_ROOT, '.venv', 'bin', 'uvicorn')]
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) return p
    } catch {
      // 权限/路径异常都当"没有"，继续找下一个
    }
  }
  return ''
}

/** 找 uv：环境变量 > 常见安装位置 > 交给 PATH（找不到时 spawn 会报 ENOENT）。 */
function resolveUv() {
  if (process.env.GZHU_UV) return process.env.GZHU_UV
  const home = process.env.USERPROFILE || process.env.HOME || ''
  const candidates = process.platform === 'win32'
    ? [
        path.join(home, '.local', 'bin', 'uv.exe'),
        path.join(process.env.LOCALAPPDATA || '', 'Programs', 'uv', 'uv.exe'),
        path.join(home, '.cargo', 'bin', 'uv.exe'),
      ]
    : [path.join(home, '.local', 'bin', 'uv'), '/usr/local/bin/uv']
  for (const p of candidates) {
    try {
      if (p && fs.existsSync(p)) return p
    } catch {
      // 同上：找不到就继续
    }
  }
  return 'uv'
}

/**
 * 拉起后端。三条路径，按优先级：
 *   1. `GZHU_BACKEND_CMD` —— 排障/自定义用（用户自带解释器）；
 *   2. 随包分发的 `backend/gzhu-backend.exe` —— 打包态的默认（目标机器无需 Python）；
 *   3. 开发态：仓库 `.venv` 的 uvicorn（不依赖 PATH）→ 退回 `uv run uvicorn`。
 * 三者都没有时不会崩，只是 waitForBackend 超时；超时会弹窗说明原因，
 * 而不是留下一个"记忆和模型都没了"的空界面。
 */
function spawnBackend() {
  const port = String(new URL(backendUrl).port)
  const custom = process.env.GZHU_BACKEND_CMD
  const portArgs = ['--host', '127.0.0.1', '--port', port]
  const env = backendEnv()

  if (custom) {
    backendProc = spawn(custom, portArgs, {
      cwd: PACKAGED ? RESOURCES_DIR : REPO_ROOT,
      shell: process.platform === 'win32',
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  } else if (PACKAGED && fs.existsSync(BACKEND_EXE)) {
    backendProc = spawn(BACKEND_EXE, portArgs, {
      // 后端需要读角色卡等自带资源：用 exe 所在目录当 cwd（PyInstaller onedir 布局）
      cwd: path.dirname(BACKEND_EXE),
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
  } else {
    const venvCmd = venvUvicorn()
    if (venvCmd) {
      // ⚠ 应用参数不能省：uvicorn.exe 的调用形式是 `uvicorn <APP> --host ...`，
      // 少了 `gzhu_cup2.entrypoints.api:app` 会立刻以
      // `Error: Missing argument 'APP'` 退出（exit 2），后端等于没起。
      backendProc = spawn(venvCmd,
        ['gzhu_cup2.entrypoints.api:app', ...portArgs], {
          cwd: REPO_ROOT,
          env,
          stdio: ['ignore', 'pipe', 'pipe'],
        })
    } else {
      const args = ['run', 'uvicorn', 'gzhu_cup2.entrypoints.api:app', ...portArgs]
      backendProc = spawn(resolveUv(), args, {
        cwd: REPO_ROOT,
        shell: process.platform === 'win32',
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
    }
  }

  const relay = (stream) => {
    stream.on('data', (chunk) => process.stdout.write(`[backend] ${chunk}`))
  }
  relay(backendProc.stdout)
  relay(backendProc.stderr)

  backendProc.on('error', (err) => {
    console.error(`[backend] 启动失败: ${err.message}`)
    console.error('[backend] 开发态请确认仓库 .venv 存在，或把 uv 放进 PATH；'
      + '打包态请确认 resources/backend/gzhu-backend.exe 存在')
    backendProc = null
  })
  backendProc.on('exit', (code) => {
    console.log(`[backend] 已退出 code=${code}`)
    backendProc = null
  })

  return waitForBackend().then((ok) => {
    console.log(`[backend] ${ok ? '就绪' : '超时未就绪'} @ ${backendUrl}`)
    if (!ok) {
      // 后端没起来时，界面上"记忆空白 + 模型连不上"看起来像两个独立 bug；
      // 这里必须把真实原因说出来，否则用户只会以为数据丢了。
      try {
        dialog.showErrorBox(
          '后端未启动',
          `连不上本机后端（${backendUrl}）。\n\n`
          + '记忆、对话与模型连接都依赖它，所以会一起不可用。\n\n'
          + '开发态：确认仓库 .venv 存在（优先走 .venv\\Scripts\\uvicorn.exe），'
          + '或把 uv 加入 PATH。\n'
          + '打包态：确认 resources\\backend\\gzhu-backend.exe 存在。\n\n'
          + '控制台会打印 [backend] 开头的具体错误。',
        )
      } catch {
        // 某些无桌面会话下弹窗不可用，日志已足够排障
      }
    }
    return ok
  })
}

function stopBackend() {
  const proc = backendProc
  backendProc = null
  if (!proc || proc.killed || proc.pid == null) return
  killTree(proc.pid)
}

// ---------------------------------------------------------------- 窗口
function withBackendArg(extra = {}) {
  return {
    ...extra,
    preload: path.join(__dirname, 'preload.cjs'),
    contextIsolation: true,
    nodeIntegration: false,
    // 注入给渲染进程（preload 读取）：后端地址（端口可能不是 8000）
    additionalArguments: [`--gzhu-backend=${backendUrl}`],
  }
}

function relayConsole(tag) {
  return (_event, ...rest) => {
    // Electron 新旧版本的 console-message 签名不同，这里做兼容处理
    const first = rest[0]
    const message =
      first && typeof first === 'object' ? String(first.message ?? '') : String(rest[1] ?? '')
    console.log(`[${tag}] ${message}`)
  }
}

function createPetWindow() {
  if (petWin && !petWin.isDestroyed()) return petWin
  petWin = new BrowserWindow({
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
    transparent: true,
    frame: false,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: withBackendArg({ backgroundThrottling: false }),
  })
  // 置顶到屏保层级，避免被普通窗口压住
  petWin.setAlwaysOnTop(true, 'screen-saver')
  // 默认让鼠标事件穿透到桌面：透明区域不再拦截点击、不挡其他应用。
  // 渲染进程检测到指针落在桌宠/气泡/输入框上时，再通过 IPC 切回「接收」。
  petWin.setIgnoreMouseEvents(true, { forward: true })
  petWin.webContents.on('console-message', relayConsole('pet'))

  // 关窗 = 缩到托盘，而不是退出
  petWin.on('close', (event) => {
    if (!quitting) {
      event.preventDefault()
      petWin.hide()
    }
  })
  petWin.on('closed', () => {
    petWin = null
  })

  if (PET_DEV_SERVER_URL) petWin.loadURL(PET_DEV_SERVER_URL)
  else petWin.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))

  return petWin
}

/**
 * 打开主窗口（功能界面）。已开则聚焦。
 *
 * `view` 非空时顺带把主窗口切到该视图（'chat' / 'calendar' / 'settings'）——
 * 桌宠悬停图标点开就是靠它直达，不必让用户再点一次右侧导航。
 * 窗口刚建时渲染层还没订阅事件，故等 did-finish-load 再发。
 */
function openMainWindow(view) {
  if (mainWin && !mainWin.isDestroyed()) {
    mainWin.show()
    mainWin.focus()
    if (view) mainWin.webContents.send('app:switch-view', view)
    return mainWin
  }
  mainWin = new BrowserWindow({
    width: 1180,
    height: 780,
    minWidth: 900,
    minHeight: 600,
    title: '心伴',
    autoHideMenuBar: true,
    backgroundColor: '#101010',
    webPreferences: withBackendArg(),
  })
  mainWin.webContents.on('console-message', relayConsole('main'))

  if (view) {
    mainWin.webContents.once('did-finish-load', () => {
      if (mainWin && !mainWin.isDestroyed()) mainWin.webContents.send('app:switch-view', view)
    })
  }

  // 关窗 = 缩到托盘，而不是退出
  mainWin.on('close', (event) => {
    if (!quitting) {
      event.preventDefault()
      mainWin.hide()
    }
  })
  mainWin.on('closed', () => {
    mainWin = null
  })

  if (MAIN_DEV_SERVER_URL) mainWin.loadURL(MAIN_DEV_SERVER_URL)
  else mainWin.loadFile(path.join(FRONTEND_DIST, 'index.html'))

  return mainWin
}

function showPetWindow() {
  if (!petWin || petWin.isDestroyed()) {
    createPetWindow()
    return
  }
  petWin.show()
  petWin.setAlwaysOnTop(true, 'screen-saver')
}

/** 显示 / 收起桌宠；返回切换后的可见状态。 */
function togglePetWindow() {
  if (!petWin || petWin.isDestroyed()) {
    createPetWindow()
    return true
  }
  if (petWin.isVisible()) {
    petWin.hide()
    return false
  }
  showPetWindow()
  return true
}

// ---------------------------------------------------------------- 托盘
function createTray() {
  let image = nativeImage.createFromPath(TRAY_ICON)
  if (image.isEmpty()) {
    console.warn(`[tray] 图标缺失或不可读: ${TRAY_ICON}`)
  } else {
    image = image.resize({ width: 16, height: 16 })
  }
  tray = new Tray(image)
  tray.setToolTip('心伴 桌宠（在后台运行）')
  // 桌宠右键菜单已移除：显隐 / 勿扰 / 退出等系统级动作统一收口在这里。
  // 勿扰切换仍转发给渲染层执行（等级状态在渲染层，见 pet/mode.ts）。
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '显示/隐藏桌宠', click: () => togglePetWindow() },
      { label: '打开主界面', click: () => openMainWindow() },
      { type: 'separator' },
      // 刻意**不做成勾选项**：勾选态要求主进程反向知道渲染层的等级，
      // 而「按一下切、再按一下恢复」更简单且不会漂移。
      { label: '勿扰 / 专注 切换', click: () => sendToPet('toggle-dnd') },
      { type: 'separator' },
      { label: '退出', click: () => quitApp() },
    ]),
  )
  tray.on('click', () => showPetWindow())
  tray.on('double-click', () => openMainWindow())
}

/** 唯一真正的退出入口：置标志后走正常 quit，让 before-quit 收掉后端子进程。 */
function quitApp() {
  quitting = true
  app.quit()
}

/**
 * 把动作转达给渲染层的桌宠窗口。
 *
 * 窗口可能是刚创建的、或刚从托盘里捞回来的 —— 必须**等首帧加载完**再发，否则消息会丢。
 * 主进程不重复实现渲染层的状态（输入框显隐、打扰等级都在渲染层），只负责转达动作名。
 */
function sendToPet(action) {
  showPetWindow()
  if (!petWin || petWin.isDestroyed()) return
  const send = () => petWin.webContents.send('pet:shortcut', action)
  if (petWin.webContents.isLoading()) petWin.webContents.once('did-finish-load', send)
  else send()
}

// ---------------------------------------------------------------- 系统通知
/**
 * 发一条系统通知（桌宠窗口不可见时的兜底送达，见 PLAN_NEXT.md §3）。
 *
 * 职责划分：
 *   - **渲染层**判断「业务上该不该通知」（打扰等级 ≥ dnd 不出、气泡正显示时不重复）；
 *   - **主进程**判断「用户其实是不是已经看得见」—— 窗口可见时直接**拒绝**，
 *     否则同一句话会以气泡 + 通知两种形式打扰两遍。
 *
 * 通知正文可能含记忆内容：渲染层已保证它走过与气泡相同的过滤链路；
 * 这里**不落盘、也不把正文写进日志**（日志只记「发了一条」这种事实），
 * 失败（系统拒绝权限/平台不支持）一律静默，主线不受影响。
 */
ipcMain.handle('pet:notify', (_event, payload) => {
  const body = String(payload?.body ?? '').trim()
  if (!body) return false
  // 窗口可见 → 用户已经能看到气泡，不发（避免双份打扰）
  if (petWin && !petWin.isDestroyed() && petWin.isVisible()) {
    console.info('[notify] 桌宠窗口可见，跳过通知（气泡已经看得到了）')
    return false
  }
  if (!Notification.isSupported()) {
    console.info('[notify] 当前系统不支持通知，跳过')
    return false
  }
  try {
    const notification = new Notification({
      // 标题用应用名，不写任何角色专名（人格只来自角色卡，见 PET_POLICY.md）
      title: String(payload?.title || app.getName()),
      body,
    })
    // 点通知 = 想说话：唤起桌宠并直接聚焦输入框（复用快捷键那条路径）
    notification.on('click', () => sendToPet('focus-composer'))
    notification.show()
    // 只记"发了一条"，**不打印正文** —— 正文可能含记忆内容
    console.info('[notify] 已发送系统通知（正文不入日志）')
    return true
  } catch (err) {
    // 系统拒绝通知权限 / 平台不支持：静默。桌宠主线不受影响（气泡仍会显示）
    console.warn(`[notify] 发送失败: ${err}`)
    return false
  }
})

// ---------------------------------------------------------------- 全局快捷键（注册）
// ---------------------------------------------------------------- 拖拽
// **窗口起点由主进程持有**，渲染进程只上报鼠标位移。
// 早先的做法是渲染进程 await 一次 getPosition 再算绝对坐标，但 pointermove 会先于
// IPC 往返到达，于是用未初始化的基准（0,0）把窗口甩到屏幕角落 —— 表现为「一拖就飞」。

/** 当前拖拽会话的窗口起点；未在拖拽时为 null。 */
let dragOrigin = null

ipcMain.handle('pet:drag-start', () => {
  if (!petWin || petWin.isDestroyed()) return
  const [x, y] = petWin.getPosition()
  dragOrigin = { x, y }
})

ipcMain.handle('pet:drag-move', (_event, dx, dy) => {
  if (!petWin || petWin.isDestroyed() || !dragOrigin) return // 起点未就绪时忽略，绝不猜坐标
  const ox = Number(dx)
  const oy = Number(dy)
  petWin.setBounds({
    x: Math.round(dragOrigin.x + (Number.isFinite(ox) ? ox : 0)),
    y: Math.round(dragOrigin.y + (Number.isFinite(oy) ? oy : 0)),
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
  })
})

ipcMain.handle('pet:drag-end', () => {
  dragOrigin = null
})

// ---------------------------------------------------------------- 自主移动（主动靠近）
// 渲染进程负责「往哪走、走到没到」的决策（它知道动画状态），主进程只做原子操作：
// 取光标、取窗口位置、按增量移动窗口。所有移动都走 setBounds 并重申宽高，
// 避免只改坐标时 Windows+DPI 把窗口越移越大（见文件头注释）。

/** 当前光标屏幕坐标（用于「靠近鼠标」）。 */
ipcMain.handle('pet:get-cursor', () => {
  const p = screen.getCursorScreenPoint()
  return { x: p.x, y: p.y }
})

/** 当前桌宠窗口的屏幕矩形（渲染进程据此判断距离/方向）。 */
ipcMain.handle('pet:get-bounds', () => {
  if (!petWin || petWin.isDestroyed()) return null
  return petWin.getBounds()
})

/** 按增量移动窗口（dx/dy 像素）；自动钳制在当前屏幕工作区内，绝不走出屏幕。 */
ipcMain.handle('pet:move-by', (_event, dx, dy) => {
  if (!petWin || petWin.isDestroyed()) return
  const ox = Number(dx)
  const oy = Number(dy)
  if (!Number.isFinite(ox) || !Number.isFinite(oy)) return
  const [x, y] = petWin.getPosition()
  let nx = x + ox
  let ny = y + oy
  const wa = screen.getDisplayNearestPoint({ x: Math.round(nx), y: Math.round(ny) }).workArea
  nx = Math.min(Math.max(nx, wa.x), wa.x + wa.width - WIN_WIDTH)
  ny = Math.min(Math.max(ny, wa.y), wa.y + wa.height - WIN_HEIGHT)
  petWin.setBounds({
    x: Math.round(nx),
    y: Math.round(ny),
    width: WIN_WIDTH,
    height: WIN_HEIGHT,
  })
})

ipcMain.handle('app:open-main', (_event, view) => {
  openMainWindow(typeof view === 'string' ? view : '')
})

/** 桌宠右键菜单里的「隐藏到托盘」 */
ipcMain.handle('app:hide-pet', () => {
  if (petWin && !petWin.isDestroyed()) petWin.hide()
})

/**
 * 切换「是否接收鼠标」。
 * ignore=true  → 鼠标穿透到桌面（透明区域不挡其他应用）；
 * ignore=false → 正常接收（指针在桌宠/气泡/输入框上时）。
 * 必须带 { forward: true }，否则忽略期间收不到 mousemove，无法再检测进入。
 */
ipcMain.handle('pet:set-ignore-mouse', (_event, ignore) => {
  if (petWin && !petWin.isDestroyed()) {
    petWin.setIgnoreMouseEvents(!!ignore, { forward: true })
  }
})

/** 桌宠右键菜单里的「退出」：与托盘退出同一入口，确保后端被带走 */
ipcMain.handle('app:quit', () => {
  quitApp()
})

/**
 * 用系统默认浏览器打开**白名单**外链（当前用于「关于」页里的素材来源链接）。
 *
 * 为什么不直接把渲染层传来的字符串交给 `shell.openExternal`：它可以打开任意
 * 协议与主机（`file:` / `javascript:` / 自定义协议都是攻击面），一旦渲染层被
 * 注入就等于把系统浏览器变成任意命令的入口。故这里做
 * **协议 + 主机名双重白名单**，只放行 `https://github.com/`。
 *
 * 只记主机名、不记完整 URL（链接本身可能带查询串，没必要入日志）。
 */
const EXTERNAL_ALLOWED_HOSTS = new Set(['github.com'])
ipcMain.handle('app:open-external', (_event, rawUrl) => {
  let target
  try {
    target = new URL(String(rawUrl ?? ''))
  } catch {
    console.warn('[open-external] 非法 URL，已拒绝')
    return false
  }
  if (target.protocol !== 'https:' || !EXTERNAL_ALLOWED_HOSTS.has(target.hostname)) {
    console.warn(`[open-external] 非白名单链接，已拒绝: ${target.protocol}//${target.hostname}`)
    return false
  }
  void shell.openExternal(target.toString())
  return true
})

// ---------------------------------------------------------------- 首次启动免责声明
/**
 * 首次启动弹出免责声明（系统对话框），用户点「确定」后写标记、永不再弹。
 *
 * 标记落在 userData（随「卸载时删除用户数据目录」一起清掉），因此重装即重新弹一次 ——
 * 这正是想要的行为：每一位新用户都至少看过一次。
 */
const DISCLAIMER_FLAG = 'disclaimer-accepted.json'
const DISCLAIMER_TEXT = [
  '本应用对话由 AI 生成，内容仅供参考，不构成医疗诊断，请仔细甄别。',
  '若遇紧急情况，请拨打 12356 心理援助热线。',
  '',
  '动画素材复刻自开源项目 VPet（虚拟桌宠模拟器）',
  'https://github.com/LorisYounger/VPet',
  '',
  '本项目仅在竞赛中作非商业使用。',
].join('\n')

async function maybeShowDisclaimer() {
  const flagPath = path.join(app.getPath('userData'), DISCLAIMER_FLAG)
  try {
    if (JSON.parse(fs.readFileSync(flagPath, 'utf8')).accepted) return
  } catch {
    // 无标记 / 文件损坏 → 视作未读过，正常弹出
  }
  const { response } = await dialog.showMessageBox({
    type: 'info',
    title: '使用须知',
    message: DISCLAIMER_TEXT,
    buttons: ['确定'],
    noLink: true,
  })
  if (response === 0) {
    try {
      fs.writeFileSync(flagPath, JSON.stringify({ accepted: true, at: new Date().toISOString() }))
    } catch (err) {
      console.warn(`[disclaimer] 标记写入失败（下次启动会再弹一次）：${err}`)
    }
  }
}

// ---------------------------------------------------------------- 启动
app.on('second-instance', () => {
  // 已有实例在跑：把窗口提到前面，而不是再开一个进程
  showPetWindow()
  openMainWindow()
})

app.whenReady().then(async () => {
  registerAssetProtocol()

  // 先清掉历史遗留的本项目后端，再决定本次端口 —— 否则它们会一直占着 8000…
  killStaleBackends()

  // 端口先定下来，窗口才能把正确地址注入渲染进程
  try {
    const port = await findFreePort(BACKEND_PORT_START)
    backendUrl = `http://127.0.0.1:${port}`
  } catch (err) {
    console.error(`[backend] 端口探测失败，回落到 ${BACKEND_PORT_START}: ${err}`)
  }

  // 开窗前预热素材：首次安装运行时素材是冷的，先读一遍避免第一帧偶发读失败。
  // 有 10s 硬超时，最坏情况也不拖住窗口创建（见 warmAssetCacheOnce）。
  await warmAssetCacheOnce()

  createPetWindow()
  createTray()
  void spawnBackend()
  // 免责声明不阻塞启动：窗口照常创建，对话框浮在最前
  void maybeShowDisclaimer()

  app.on('activate', () => {
    showPetWindow()
  })
})

// 双保险：before-quit 与 will-quit 都收一次，确保后端进程树被带走
app.on('before-quit', () => {
  quitting = true
  stopBackend()
})

app.on('will-quit', () => {
  stopBackend()
})

// 托盘常驻：窗口全关也**不退出**（这是「关窗即缩到托盘」的关键）。
// 若这里不注册监听器，Electron 会在窗口全关时自动退出。
app.on('window-all-closed', () => {
  // 有意留空：退出只走托盘菜单
})
