# 部署与维护

## 本地环境
Python 3.12+；现有 bundled Python 在 README 指定位置。新安装依赖放到 D 盘：

```powershell
$env:TEMP='D:/codex/black-white-review-site/tmp'
$env:TMP=$env:TEMP
python -m pip install --target D:/codex/black-white-review-site/python -r requirements.txt
$env:PYTHONPATH='D:/codex/black-white-review-site/python'
python scripts/manage.py seed-demo
python scripts/manage.py create-admin
python scripts/manage.py serve
```

`instance/` 包含私人数据库、开发邮件和上传图片，应由当前用户/服务用户独占访问。开发邮件链接不要截图或提交。用户密码以 scrypt 保存；会话及验证令牌只存散列。公开上传图片经过 Pillow 解码、尺寸校验和 WebP 重编码；不接受 SVG 上传。

## GitHub Pages
仓库 `obedientdem/black-white-review-site` 设为公开，main 为稳定分支。Pages 选择 main / 根目录；根 `index.html` 为独立打包前台。深层页面使用 hash 路由，因此刷新无需服务端路由。

更新编辑内容：

```powershell
python scripts/manage.py export-static --file <新的空导出目录>
python scripts/build_standalone.py
```

若使用导出目录构建单文件，在 Python 中调用 `scripts.build_standalone.build(导出目录, 根目录index.html)`；先浏览检查，再提交 Pages 快照。导出只读取公开文章、公开活动、公开游戏与设置；不导出评论/用户/商务/报名/中奖私人记录。`export-static` 复制公开引用的上传图片，修正相对路径。

## 真实后台上线（待提供服务）
需要单机持久磁盘、HTTPS 反向代理、SMTP、备份位置和每分钟调度器。SQLite 不能放在临时容器磁盘或多实例共享网络盘。多实例部署须先迁移数据库并重新测试并发事务。

生产环境变量由服务器密钥管理注入：`BW_ENV=production`、`BW_DATABASE`、`BW_PUBLIC_URL=https://实际域名`、`BW_SECRET`（至少32字符随机值）、`SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASSWORD`、`SMTP_FROM`。不要提交 `.env`。密钥用于令牌 HMAC；更换密钥会使旧会话与邮件令牌失效。生产模式拒绝开发 outbox 或缺失 HTTPS/SMTP/密钥。

使用 Waitress，绑定 127.0.0.1:8766；反向代理向此地址转发，并强制 HTTPS。API 校验固定 PUBLIC_URL Origin；不允许跨域任意来源或 file:// 调用正式后台。配置代理日志时不要记录密码、CSRF、Cookie、邮件令牌与申请正文。公网域名邮件需要 SPF/DKIM/DMARC 和实际投递验收，此处尚未接入或验证。

自动开奖：用系统调度器每分钟运行 `python scripts/manage.py draw-due --database <私有数据库路径>`，必须使用相同生产环境与依赖路径。任务不依赖网页打开。失败返回非零，应监控退出状态并重试；数据库事务/唯一约束防止重开奖与重复发奖。尚无服务器，因此未创建线上定时任务。

开奖发布前生成种子承诺；报名合格时固化资格，截止后匿名entry ID集合保持稳定。使用 HMAC-SHA256(seed, entry ID)排序取前N位，开奖后公布seed、commitment、entry ID快照与结果供复算，不公布邮箱。管理员能接触服务器秘密，因此这不是对运营者恶意操控的零信任方案；需要更强保证时接入独立公证随机源。地区和18岁声明尚非身份/地理验证；高于18岁的活动在没有额外年龄验证服务时拒绝报名。

## 备份、恢复和迁移
`python scripts/manage.py backup --file <私有路径/backup.zip>` 使用 SQLite online backup API 并备份图片。将备份放在加密且受限的独立存储；不得发布到 GitHub。

先停止服务，恢复到**新的数据库路径**：`python scripts/manage.py restore --file <backup.zip> --database <新路径/site.sqlite>`。恢复校验完整性、外键和压缩包路径；拒绝覆盖现有数据库。测试启动后切换 BW_DATABASE。Git 回退不会回退数据库。

本次为 schema v1，首次部署初始化空数据库。升级已有生产数据库前必须编写并验收迁移，不应直接覆盖数据库。本次没有操作任何线上数据库。

## 内容导入与正式上线资料
`python scripts/manage.py import-content --file articles.json` 每次仅导入1–50篇**草稿**，每项需要 kind/title/body/category/cover、source/source_url(HTTPS)、rights_confirmed:true；测评还需要score/pros/cons。验证所有行后一次性写入，失败不留下半批内容。管理员核对后发布；不自动抓取、翻译或伪造历史数据。IGDB/Steam 当前仅保留隔离接口，未获访问资质与条款确认前不启用。

正式运营前还需提供30–50篇精选历史原文/来源、授权封面、确认后的统计口径、真实团队/合作案例、运营主体和联系邮箱，并完成隐私/年龄/抽奖及版权条款审查。当前法律页面明确标记草案。此版本不应开启真实用户公开注册，直至这些事项和线上环境验收完成。
