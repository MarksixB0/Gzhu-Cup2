/** preload.cjs 暴露到渲染进程的能力。 */
declare global {
  interface Window {
    /** 主进程注入的运行环境。浏览器里直接跑前端时不存在，需回落到默认值。 */
    gzhuEnv?: {
      /** 后端地址，例如 http://127.0.0.1:8000（端口由主进程决定） */
      backendUrl: string
      /**
       * 用系统浏览器打开外链（仅白名单 `https://github.com/`，见 main.cjs）。
       * 返回是否真的打开了；非白名单或非法 URL 返回 false。
       */
      openExternal(url: string): Promise<boolean>
      /**
       * 订阅「主窗口切到某个视图」（桌宠悬停图标点开后直达）。
       * 返回取消订阅函数。
       */
      onSwitchView?(handler: (view: string) => void): () => void
    }
    /** 桌宠窗口专用能力（主窗口不调用） */
    petWindow?: {
      /** 按下时通知主进程记录窗口起点 */
      dragStart(): Promise<void>
      /** 上报相对按下点的位移（像素） */
      dragMove(dx: number, dy: number): Promise<void>
      /** 松开时结束拖拽会话 */
      dragEnd(): Promise<void>
      /** 打开主窗口（功能界面）；传 view 则同时切到该页 */
      openMain(view?: 'chat' | 'calendar' | 'settings'): Promise<void>
      /** 把桌宠收进托盘（应用继续后台运行，不退出） */
      hidePet(): Promise<void>
      /** 退出应用（含后端进程） */
      quit(): Promise<void>
      /**
       * 切换「是否忽略鼠标」：true=穿透到桌面（透明区不挡其他应用），
       * false=正常接收（指针在桌宠/气泡/输入框上）。
       */
      setIgnoreMouse(ignore: boolean): Promise<void>
      /** 当前光标屏幕坐标（用于「主动靠近鼠标」） */
      getCursor(): Promise<{ x: number; y: number }>
      /** 当前桌宠窗口屏幕矩形 */
      getBounds(): Promise<{ x: number; y: number; width: number; height: number } | null>
      /** 按增量移动窗口（dx/dy 像素） */
      moveBy(dx: number, dy: number): Promise<void>
      /** 订阅主进程转达的动作（托盘菜单的勿扰切换）；返回取消订阅函数 */
      onShortcut(handler: (action: string) => void): () => void
      /**
       * 发系统通知；返回**是否真的弹了**。
       * 桌宠窗口可见时主进程会拒绝（用户已经能看到气泡，不必再打扰一次）。
       */
      notify(payload: { title?: string; body: string }): Promise<boolean>
    }
  }
}

export {}
