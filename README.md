# VideoGet 视频下载器

一个带中文窗口的 Python 视频下载工具。输入视频页面或视频文件的网址，选择保存文件夹，点击运行即可下载。下载在后台执行，窗口会显示进度和日志，并可取消任务。

## 使用方法

1. Windows 用户双击 `VideoGet.exe`；源码运行方式见下文。
2. 在窗口中输入完整的 `http://` 或 `https://` 网址。
3. 点击目录选择按钮，指定视频保存位置。
4. 在模式下拉框中选择任务类型：默认的“自动下载网页预览”用于公开预览；“接收网站原片下载”用于在网站窗口登录后接收官方按钮提供的文件；“常规视频网址 / 直链”交给 yt-dlp 处理普通页面和直链。
5. 如果需要下载播放列表或预览列表中的多个视频，按界面提示设置数量；“下载整份播放列表”默认关闭。
6. 点击运行，等待日志提示完成。视频保存在所选目录，完成后不会自动打开目录。

运行中可点击取消。已经下载完成的视频会保留；未完成文件通常以 `.part` 等临时文件形式保留，便于后续继续下载。网站是否支持续传由服务器决定。

“接收网站原片下载”由浏览器管理传输，取消时只保留已完整保存的文件，未完成的原片需要重新点击网站按钮下载。

## 支持范围

下载能力由 [yt-dlp](https://github.com/yt-dlp/yt-dlp) 提供，支持其提取器兼容的站点，以及普通 HTML 页面中能被通用提取器解析的视频链接。直接视频地址与部分 HLS、DASH 视频也可以处理。网页只是一个网站入口、视频由复杂 JavaScript 动态加载或站点存在访问验证时，可能无法解析；不保证任意网址都能下载。

不支持绕过 DRM、付费限制或访问权限。接收原片模式会打开一个独立的临时浏览器窗口，你可以在其中手动登录并按网站实际权限点击官方的下载按钮；程序不导入现有浏览器 Cookie，窗口关闭后登录状态与临时上下文会清除。请下载你有权保存的内容。

程序使用 `imageio-ffmpeg` 附带的 FFmpeg 合并音视频、处理流媒体；打包时会一起放入 EXE，无需单独安装 FFmpeg。某些站点的额外 JavaScript 运行环境要求不包含在本程序中，遇到此类错误请以窗口日志为准。

### Magnific 公开预览

输入 `https://www.magnific.com/videos` 时，程序默认使用 Playwright 启动本机已经安装的 Microsoft Edge（`msedge`）或 Google Chrome（`chrome`），读取当前页面中的公开预览视频。EXE 不打包 Chromium；使用此功能前请在 Windows 上安装并保持 Edge 或 Chrome 可启动。程序只扫描当前页面及少量滚动区域，默认最多收集 30 个预览链接；不跨站深爬。页面公开预览不等于原始素材，因此不保证得到原始高清文件；站点改版、登录、地区限制或其他访问验证也可能导致扫描失败。

### 登录后接收网站原片

在模式下拉框选择“接收网站原片下载”并点击运行后，程序会打开独立的临时 Edge/Chrome 窗口。请在该窗口中手动完成登录（如网站要求），再点击网站自己的正式下载按钮；每个文件由 Playwright 的下载事件保存到所选文件夹。默认最多接收 30 个文件后结束，提前关闭本次新开的浏览器窗口也会结束接收。此模式只接收网站实际提供、且当前账号有权限下载的文件，不自动批量抓取原片，不猜测或改写原片地址，也不破解 DRM 或其他访问控制。临时浏览器上下文不导入现有 Cookie，程序退出后不会保存登录状态。不同网站的登录、弹窗和下载流程尚未逐一验收，能否成功取决于网站实现和账号权限。

## 从源码运行

要求 Python 3.11 或更新版本，并包含 Tkinter。Windows 推荐安装 [python.org](https://www.python.org/downloads/windows/) 的 64 位 Python，安装时保留 Tcl/Tk 组件。

Windows：双击 `run_windows.bat`。脚本会在项目目录创建 `.venv`、安装依赖并启动窗口；首次使用需要联网。路径中包含空格也可使用。

也可在项目目录手动运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

macOS/Linux 可运行源码（所用 Python 需提供 Tkinter）：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

## 打包 Windows EXE

必须在 **Windows x64** 上，使用 **x64 Python 3.11 或更新版本** 构建；推荐 Python 3.12。PyInstaller 不能在 macOS/Linux 上原生交叉打包出 Windows EXE。

在 Windows 双击 `build_windows.bat`，它会检查平台、创建 `.venv`、安装/更新构建依赖，并执行：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --clean --noconfirm VideoGet.spec
```

成功后得到 `dist\VideoGet.exe`，是单文件、无控制台窗口的程序。可将这个 EXE 复制到另一台 Windows x64 电脑使用，**目标电脑无需安装 Python 或 FFmpeg**。第一次启动可能稍慢，因为单文件程序需要解压运行资源；下载视频仍需网络和目标目录写入权限。

如果需要使用 Magnific 公开预览或“接收网站原片下载”模式，目标电脑还必须安装 Microsoft Edge 或 Google Chrome；程序不会下载或打包 Chromium 浏览器。

构建失败时窗口会显示错误并保留进程退出码。在命令行或 CI 中可用 `build_windows.bat --no-pause`，避免等待按键。构建过程需要联网安装 Python 依赖。

### 没有 Windows 电脑：使用 GitHub Actions

项目已提供 `.github/workflows/build-windows.yml`。将项目提交到你自己的 GitHub 仓库后，可在仓库 **Actions → Build Windows EXE → Run workflow** 手动运行；推送代码也会触发构建。成功后在该次运行页面的 **Artifacts** 下载 `VideoGet-Windows-x64`，解压获得 `VideoGet.exe`。构建产物保留 14 天。此方式由 GitHub 的 Windows 环境构建，不需要本机是 Windows。

上传项目时必须保留隐藏的 `.github/workflows/build-windows.yml`，否则仓库不会显示这个工作流。推荐在项目目录用 Git 命令上传，文件夹中的隐藏路径会一起提交：

```bash
git init
git add .
git commit -m "Add VideoGet"
git branch -M main
git remote add origin https://github.com/<账号>/<私有仓库>.git
git push -u origin main
```

网页操作可以按三步完成：

1. 在 GitHub 创建 **Private** 仓库，先不要勾选自动生成 README 或 `.gitignore`。
2. 将最终项目 ZIP 解压，通过 **Add file → Upload files** 上传解压后的全部内容；直接上传 ZIP 不会展开工程，网页文件选择器也容易漏掉 `.github` 隐藏目录。
3. 确认仓库中存在 `.github/workflows/build-windows.yml`，再打开 **Actions → Build Windows EXE → Run workflow**。

涉及隐藏目录时，前面的 Git 命令方式更可靠。

## 更新依赖与常见问题

网站经常调整页面与接口。遇到原先可下载的网站突然失败，可以在源码目录更新 yt-dlp 后重试：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade -r requirements.txt
```

EXE 用户需要重新执行 `build_windows.bat` 或重新运行 GitHub Actions，分发新生成的 EXE；已有 EXE 不会自动更新。

- **找不到 Python**：安装 Python 3.11+ 后重试，或确认 `py` / `python` 在命令行可用。
- **提示缺少 Tkinter**：安装带 Tcl/Tk 的 Python；部分 Linux 发行版需要单独安装 `python3-tk`。
- **现有 `.venv` 平台或版本不兼容**：重命名旧 `.venv` 目录后重新运行脚本，让其创建新环境。
- **不支持的网址、需要登录、403 或验证页面**：先确认网址可正常打开，再查看窗口日志；当前版本无法满足的站点访问要求需要另行适配。
- **FFmpeg、权限或磁盘空间错误**：检查日志、目标文件夹写入权限与剩余空间；源码环境可重新安装 `imageio-ffmpeg` 后再试。

主要文件：`main.py` 为图形界面，`downloader.py` 为下载逻辑，`VideoGet.spec` 为包含 yt-dlp 提取器与 FFmpeg 的打包配置。
