# P0 技术决策

采用可迁移的 Flask + SQLite 后端与无构建 HTML/CSS/JS 前台。Python 依赖安装在 D:/codex/black-white-review-site/python；使用已有运行时，不另外安装系统软件。公开前台可双击 site/index.html，后端模式由同源服务器提供。GitHub Pages 只部署 site/，不部署数据库、密钥或私人业务记录。

GitHub Pages 是静态托管，不能执行本项目的 Python API。用户已确认：先完成代码、本地测试和 Pages 前台，后端线上服务、真实邮件与正式运营准备待后续接入。

## 目录
- site/：完整中英界面、内容浏览、账号/后台与业务表单；离线展示明确标记的示例。
- backend/：HTTP API、数据库、权限、审计、邮件适配器与开奖任务。
- tests/：分阶段功能、攻击输入、权限隔离、并发及持久化测试。
- scripts/：启动、建管理员、开奖、备份与恢复命令。
- docs/：需求、部署、验收、数据导入说明。

## 实体与关系
users → sessions / tokens / ratings / favorites / comments / applications / entries / notifications。
games → articles（测评/资讯，多语言稿共享 ID、分数、评论）；articles → views（滚动30天）。
users → publisher_requests / business / activities；activities → applications / entries / draws / awards。
settings 维护首页指标、理念和联系方式；audit 记录特权操作。所有业务表具有外键；报名唯一约束；draws.activity_id 唯一保证一次开奖。

## 权限矩阵
| 操作 | 游客 | 玩家（验证邮箱） | 发行商 | 编辑 | 超管 |
|---|---|---|---|---|---|
|公开阅读|是|是|是|是|是|
|评分/评论/收藏/报名|否|本人|本人|本人|本人|
|官方内容与游戏档案|否|否|否|是|是|
|发行商草稿、商务申请|否|申请入驻|仅本人|否|全部|
|活动上架、认证、审核、角色/设置|否|否|否|否|是|
|招募隐私|否|本人|仅明确授权推荐|否|全部|

## 安全和能力边界
密码使用 Werkzeug scrypt；会话使用随机不透明令牌，数据库只存散列，HttpOnly/SameSite cookie；更改状态需 Origin + CSRF 校验；生产要求 HTTPS、Secure cookie 与外置密钥。API 限流、字段校验、参数化 SQL、纯文本安全渲染、受控图片上传、审核与审计。不宣称年龄声明能核实身份，邮箱唯一不等于真人唯一。
开奖使用事务锁、资格截止快照、secrets 随机选择和唯一结果表，重复任务返回原结果。需独立调度器每分钟执行 CLI；不依赖网页打开。
开发邮件写入私有数据库 outbox；只在显式开发模式启用。生产需要 SMTP 与固定公网域名，未配置拒绝启动。SQLite 适合单机持久磁盘；水平扩展前迁移 PostgreSQL。依赖/框架已实际安装测试；生产邮件、DNS、托管、法律审查待验证。

## 五阶段与闸门
1. P0 架构、目录、实体、权限及能力验证；测试配置与结构。
2. P1 杂志视觉、六个公开入口、详情、响应式、搜索与中英；测试路由/筛选/渲染。
3. P2 真实账号、一次性验证/重置、RBAC、内容/游戏/站点 CRUD、上传；测试持久化和越权。
4. P3 互动审核、发行商、商务、招募、幂等开奖、发奖；测试流程和并发。
5. P4 导入验证、备份/恢复、安全回归、浏览器与上线打包；全量测试及验收清单。

失败先修复，不删除测试或降低标准。每阶段独立 Git 提交和 PROGRESS 更新。没有历史文章和授权图片时保留明确示例，真实内容与正式法律文件待提供，不伪造完成。

