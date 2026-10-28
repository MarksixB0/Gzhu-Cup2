# AGENTS.md — 版本号与发布约定（给 AI 助手）

> **本文件是机器可读的约定，不是介绍文章。** 任何 AI 助手在本仓库执行「升版本 / 打包 / 发布」
> 相关操作前，必须先读本文件并严格照做；人类协作者同样适用。
>
> 当前范围：**产品版本号命名、升级规则、发布流程**。其他工程约定见 `README.md` 与 `docs/`。

---

## 0. 版本号的唯一真相（Single Source of Truth）

产品版本号**同时存在于 4 个文件**，它们必须**完全一致**：

| # | 文件 | 字段 | 作用 |
|---|---|---|---|
| 1 | `pyproject.toml` | `[project] version` | 后端包版本（`uv lock` 会同步到 `uv.lock`） |
| 2 | `src/gzhu_cup2/entrypoints/api.py` | `FastAPI(version=...)` | HTTP API 对外暴露的版本 |
| 3 | `apps/web/package.json` | `version` | 功能界面版本 |
| 4 | `apps/desktop/package.json` | `version` | **桌面端版本 —— 安装包名、Release 版本都以它为准** |

**不要手改**这两个自动同步的文件：

- `uv.lock` —— 改完 `pyproject.toml` 后跑 `uv lock`；
- `apps/web/package-lock.json`、`apps/desktop/package-lock.json` —— 改完对应 `package.json` 后跑
  `npm install --package-lock-only`。

---

## 1. 命名规范

采用**语义化版本** `MAJOR.MINOR.PATCH`：

| 位 | 何时 +1 | 本仓库的例子 |
|---|---|---|
| **MAJOR** | 不兼容变更：API 契约破坏、记忆库/会话库 schema 不兼容 | 改了 `/api/chat` 请求体、改了 `sessions.sqlite3` 表结构 |
| **MINOR** | 向后兼容的**新功能** | 新增晚安仪式、新增打扰等级、新增隐私控制面 |
| **PATCH** | 向后兼容的**修复** | 修危机快闸误报、修 UI 错位、修打包路径 |

配套规则：

- **Tag 必须带 `v` 前缀**：`v1.3.2`（不是 `1.3.2`）。
- **Release 标题**格式：`心伴 v<版本>`（仓库名为 `Gzhu-Cup2`，产品名为「心伴」）。
- **预发布**写作 `1.4.0-rc.1`（tag `v1.4.0-rc.1`），正式发布时去掉后缀再发一次。
- **构建号/日期不进版本号** —— 需要追溯时用 git tag 与提交哈希。

---

## 2. 升级时的同步清单（缺一不可）

```
[ ] pyproject.toml                        version
[ ] src/gzhu_cup2/entrypoints/api.py      FastAPI(version=...)
[ ] apps/web/package.json                 version
[ ] apps/desktop/package.json             version
[ ] uv lock                               （同步 uv.lock）
[ ] cd apps/web && npm install --package-lock-only
[ ] cd apps/desktop && npm install --package-lock-only
[ ] 全仓复核：旧版本号应无残留
    grep -rn "旧版本号" --include="*.toml" --include="*.json" --include="*.py" .
```

> 复核时要排除 `node_modules/`、`.venv/`、`dist/` —— 那里面的版本号是第三方依赖的，与本项目无关。

---

## 3. 发布流程

```bash
# 1. 前置：工作区干净 + 测试全绿
uv run python -m pytest -q

# 2. 升版本（按 §0 改 4 处 + §2 同步 lock），提交
git commit -m "chore(release): 版本号定为 X.Y.Z"

# 3. 打包（产出一个产物：NSIS 安装包）
cd apps/desktop && npm run dist
#    release/XinBan-Setup-<版本>.exe          （NSIS，可选安装目录 + 桌面快捷方式）
#    portable 目标已从 build.win.target 移除，不再产出单文件便携版

# 4. 打 tag 并推送
git tag vX.Y.Z && git push origin main --tags

# 5. 创建 GitHub Release 并上传安装包（仓库无 gh CLI；用 API + 本地凭据，见 §5）
```

**发布前必须核对产物**（见 §4）：安装包应在 **100–140 MB** 量级；明显偏小（几十 MB）说明后端没进包。

---

## 4. 踩过的坑：包「生成成功」≠ 包「是对的」

**最危险的一种失败**：NSIS 安装包正常生成、日志也不报错，但**包里没有 Python 后端**，
用户装完双击起不来。

判据——打包日志里出现这一行就是出错了：

```
• file source doesn't exist  from=...\apps\desktop\backend-dist\gzhu-backend
```

根因是**两处路径不一致**：

| 位置 | 必须指向 |
|---|---|
| `packaging/build_backend.py` → `DIST_DIR` | `ROOT / "apps" / "desktop" / "backend-dist"` |
| `apps/desktop/package.json` → `build.extraResources` | `{ "from": "backend-dist/gzhu-backend", "to": "backend" }`（相对 `apps/desktop/`） |

`DIST_DIR` 若写回 `desktop/backend-dist`（无 `apps/` 前缀），PyInstaller 会把后端输出到旧路径、
还会**凭空重建一个 `desktop/` 目录**，而 electron-builder 在新路径找不到它 —— 于是静默跳过。

**每次打包后的校验动作（三者任一）**：

```bash
ls -lh "apps/desktop/release/XinBan-Setup-<版本>.exe"                        # 100–140 MB
ls "apps/desktop/release/win-unpacked/resources/"                            # 应见 backend/ frontend-dist/ pet-assets/ card-seed/ tray.png
ls -lh "apps/desktop/release/win-unpacked/resources/backend/gzhu-backend.exe"  # ≈16 MB
```

`resources/` 下设目录缺一即视为发布失败：`backend/`（≈56 MB）、`frontend-dist/`、`pet-assets/`、
`card-seed/`、`tray.png`。

> 注：`build_backend.py` 的 `strip_py_sources` 会删掉 `_internal/` 下随包拷入的 `.py` 源码
> （约 500 个 / 7 MB）。它只影响体积、不影响运行；若删除被环境限制而中断，安装包会偏大几 MB，
> 不构成发布失败。

---

## 5. 创建 Release（无 gh CLI 时）

`gh` 在本机不可用。用本地 git 凭据中的 PAT 调 GitHub API：

```text
POST https://api.github.com/repos/MarksixB0/Gzhu-Cup2/releases
     {"tag_name":"vX.Y.Z","name":"心伴 vX.Y.Z","body":"...", "draft":false,"prerelease":false}

POST https://uploads.github.com/repos/MarksixB0/Gzhu-Cup2/releases/{id}/assets?name=<文件名>
     （body 为该文件的二进制内容）
```

- token 由本机凭据管理器提供（`git credential fill`，`protocol=https` / `host=github.com`）；
  **不要打印、不要写进任何文件、不要提交**；
- 本仓库为**公开仓库** → Release 附件任何人都能下载。仓库改私有后附件仅协作者可见。

---

## 6. 不要做的事

- ❌ 在 `README.md` 或文档正文里**硬编码**具体版本号（本文件 §7 的"当前版本"除外）—— 会腐烂；
- ❌ 手改 `uv.lock` / `package-lock.json` 的版本字段；
- ❌ 只改 `apps/desktop/package.json` 就发布（四处不一致会导致「关于」页、API 版本、安装包名互相矛盾）；
- ❌ 跳过 §4 的产物校验 —— 少了后端也能"打包成功"；
- ❌ 往仓库里提交用户数据、本地配置、安装产物 —— 见 `.gitignore` 的隐私兜底规则。

---

## 7. 当前版本

| 项 | 值 |
|---|---|
| 版本 | **v1.3.2** |
| 日期 | 2026-10-28 |
| 性质 | 本仓库首个 tag。代码基线为 1.3.2：记忆检索 FTS 召回修正（小库不筛 / 命中集并 top-N / 主题词扩展 / 窗口外按 key 直取）+ 读侧检索留痕（独立 `retrieval_trace` 命名空间）+ 叙事接入用户自述偏好（称呼 / likes / dislikes）+ 关怀侧轻量 likes 话题（7 天频控）；全部向后兼容：旧库、旧留痕记录、旧 `care_state.json` 均可直接读。`1.4.0` 预留给 `docs/PLAN_FINALS.md` 的守护层加固，避免版本号被提前占用。 |
