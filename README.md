# 黑白测评组官网

黑白游戏杂志前台 + Flask/SQLite 持久化后台。此仓库公开；不含数据库、管理员密码、真实用户资料或邮件密钥。

**状态：本地可测试版本 + GitHub Pages 前台展示版。尚未接入线上后端/真实邮件，示例内容不是正式运营内容。**

## 直接查看
双击根目录 `index.html`：CSS、脚本与原创示例插画已全部内嵌，无需联网或安装。也可打开 `site/index.html`（保留同目录文件）。

公开入口：首页、测评列表/详情、资讯、活动/详情、关于、商务、账号、发行商、管理后台及法律草案。支持中英切换、平台/类型/评分筛选、排序、搜索与移动端。

## 本机启动
依赖已安装到 `D:\codex\black-white-review-site\python`。双击 `启动本地后台.cmd`，访问 http://127.0.0.1:8766 。

首次创建自己的管理员（终端交互输入，不将密码写进代码）：

```powershell
$env:PYTHONPATH='D:/codex/black-white-review-site/python'
$python='C:/Users/18110/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
& $python scripts/manage.py create-admin
& $python scripts/manage.py serve
```

在“玩家中心”登录，进入“管理工作台”。可编辑测评/资讯、游戏档案、首页宣言/精选、统计口径、团队/分众网络、授权案例和联系方式；可审核评论、发行商、活动，管理招募、商务、用户权限与发奖。

本地注册邮件不会发送至真实收件人；执行 `scripts/manage.py outbox` 在私有终端查看验证/重置链接。开发邮件箱没有公开 API。`seed-demo` 仅允许空开发数据库，防止覆盖编辑内容。

## 验证

```powershell
& $python -m unittest discover -s tests -p 'test_*.py' -v
& 'C:/Users/18110/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe' tests/frontend.test.cjs
```

见 `docs/ACCEPTANCE.md`、`docs/DEPLOYMENT.md`、`PROGRESS.md`。每阶段单独 Git 提交；测试不使用线上账号，不发送真实邮件。

## 管理员编辑与 Pages
本地后台编辑立即保存到私有 SQLite 数据库并反映到本地前台。GitHub Pages 是静态快照，编辑后执行 `export-static` 导出公开数据、运行测试并提交新快照。Pages 不运行注册、审核或开奖。

正式后台选择持久磁盘服务器后，可同源托管全部站点。禁止把 SQLite、outbox、SMTP 凭证或 `instance/` 上传公开仓库。
