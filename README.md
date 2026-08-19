# 我的影评记录

一个面向个人使用的极轻量 Windows 影评管理工具。双击一个 EXE 即可打开独立窗口，无须安装 Python、Node.js，无须启动本地服务，也不会监听网络端口。

## 功能

- 录入、编辑、删除影评，点击卡片在独立弹窗查看完整详情
- 每条影评可保存一张 JPG、PNG 或 WebP 原始剧照，可点击选择或从 Windows 资源管理器拖入，并支持全屏查看
- 按电影名、导演、评论或标签搜索
- 按评分和标签筛选，按日期、评分或名称排序
- 展示观影总数、平均评分、本月观影数和分类数
- 首次启动选择数据目录，之后可随时切换
- 导出包含 JSON 与剧照的完整 ZIP 备份，同时兼容导入旧版 JSON
- 每次修改前自动保留 JSON 历史版本，最多保留 20 份
- 数据只保存在本机，不上传网络

## 直接使用

1. 从 GitHub Releases 下载 `我的影评记录.exe`。
2. 双击运行，首次启动时选择一个数据保存目录。
3. 如已有旧版数据，请选择包含 `movie-reviews.json` 的目录；程序会直接读取它。

数据目录内的主文件是 `movie-reviews.json`，剧照位于 `media` 子目录，历史 JSON 版本位于 `backups` 子目录。剧照按内容哈希保存，同一图片不会重复复制；更换剧照或删除影评时旧图片仍会保留，以便历史版本恢复。建议把数据目录放在你日常备份的位置。应用设置仅记录所选目录，保存在 `%APPDATA%\MovieReview\settings.json`。

剧照为可选项，单张最大 50 MB，保留原始画质及 EXIF/GPS 等元数据。在新建或编辑影评时，可点击“选择剧照”，也可将 Windows 资源管理器中的一张 JPG、PNG 或 WebP 直接拖到主剧照区域。需要迁移或备份时，请使用应用顶部的“导出”，生成同时包含当前 JSON 和被引用剧照的 ZIP，不要只复制 JSON。

> Windows 10/11 通常已经包含 Microsoft Edge WebView2 Runtime。若窗口无法打开，请先安装微软官方 WebView2 Runtime。

## 本地开发

要求 Windows 与 Python 3.10 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 构建单文件 EXE

```powershell
.\build.ps1
```

构建产物位于 `dist\我的影评记录.exe`。PyInstaller 单文件程序启动时会临时解包内部运行文件，因此应用界面被打进 EXE，而用户数据始终写入首次选择的外部目录。

## 项目结构

- `app.py`：数据校验、原子保存、滚动备份和桌面桥接
- `index.html`：内嵌桌面界面
- `movie-review.spec`：PyInstaller 单文件构建配置
- `tests/`：存储与数据校验测试
