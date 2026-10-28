// 渲染进程 ↔ 主进程的唯一桥。不暴露任何 Node 能力。
//
// 两件事：
//   - gzhuEnv：主进程注入的运行环境（后端地址可能因端口冲突而不是 8000）；
//   - petWindow：桌宠窗口专用的拖拽、服务端控制与主进程动作订阅。
const { contextBridge, ipcRenderer } = require('electron')

/** 主进程通过 webPreferences.additionalArguments 注入后端地址 */
const BACKEND_ARG_PREFIX = '--gzhu-backend='
const backendArg = process.argv.find((a) => a.startsWith(BACKEND_ARG_PREFIX))
const backendUrl = backendArg
  ? backendArg.slice(BACKEND_ARG_PREFIX.length)
  : 'http://127.0.0.1:8000'

contextBridge.exposeInMainWorld('gzhuEnv', {
  backendUrl,
  /**
   * 用系统浏览器打开**白名单**外链（仅 `https://github.com/`，见 main.cjs 的
   * `app:open-external`）。返回是否真的打开了 —— 非白名单/非法 URL 会返回 false。
   */
  openExternal: (url) => ipcRenderer.invoke('app:open-external', url),
  /**
   * 订阅「主窗口切到某个视图」（由桌宠的悬停图标触发，值见 main.cjs 的 app:open-main）。
   * 返回**取消订阅函数**：卸载时必须调用，否则重建窗口会累积监听器。
   */
  onSwitchView: (handler) => {
    const listener = (_event, view) => handler(view)
    ipcRenderer.on('app:switch-view', listener)
    return () => ipcRenderer.removeListener('app:switch-view', listener)
  },
})

contextBridge.exposeInMainWorld('petWindow', {
  /** 按下时通知主进程记录窗口起点（勿 await 后再拖） */
  dragStart: () => ipcRenderer.invoke('pet:drag-start'),
  /** 上报相对按下点的位移；主进程换算成绝对坐标落位 */
  dragMove: (dx, dy) => ipcRenderer.invoke('pet:drag-move', dx, dy),
  /** 松开时结束拖拽会话 */
  dragEnd: () => ipcRenderer.invoke('pet:drag-end'),
  /** 打开主窗口（功能界面）；已开则聚焦。传 view 则同时切到该页 */
  openMain: (view) => ipcRenderer.invoke('app:open-main', view),
  /** 把桌宠收进托盘（应用继续在后台运行，不退出） */
  hidePet: () => ipcRenderer.invoke('app:hide-pet'),
  /** 退出应用（连同后端进程；这是唯一的真正退出入口） */
  quit: () => ipcRenderer.invoke('app:quit'),
  /**
   * 切换「是否接收鼠标」：
   *   true  = 忽略（穿透到桌面，透明区域不挡其他应用）；
   *   false = 接收（指针在桌宠/气泡/输入框上）。
   */
  setIgnoreMouse: (ignore) => ipcRenderer.invoke('pet:set-ignore-mouse', ignore),
  /** 当前光标屏幕坐标（用于「主动靠近鼠标」） */
  getCursor: () => ipcRenderer.invoke('pet:get-cursor'),
  /** 当前桌宠窗口屏幕矩形 */
  getBounds: () => ipcRenderer.invoke('pet:get-bounds'),
  /** 按增量移动窗口（dx/dy 像素） */
  moveBy: (dx, dy) => ipcRenderer.invoke('pet:move-by', dx, dy),
  /**
   * 订阅主进程转达的动作（当前只有托盘菜单的 'toggle-dnd'）。
   * 返回**取消订阅函数**：渲染层卸载时必须调用，否则重建窗口/热更新会累积监听器。
   */
  onShortcut: (handler) => {
    const listener = (_event, action) => handler(action)
    ipcRenderer.on('pet:shortcut', listener)
    return () => ipcRenderer.removeListener('pet:shortcut', listener)
  },
  /**
   * 发一条系统通知（见 main.cjs 的 `pet:notify`）。
   *
   * 返回**是否真的弹了** —— 主进程会在「桌宠窗口已可见」时拒绝（用户已经能看到气泡，
   * 再弹通知就是同一句话打扰两遍）。渲染层只负责判断**业务上该不该**通知。
   */
  notify: (payload) => ipcRenderer.invoke('pet:notify', payload),
})
