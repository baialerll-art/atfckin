# ArityFlow 每日签到（GitHub Actions）

用 Python + Playwright 自动登录 [ArityFlow](https://www.arityflow.top/)，调用官方签到 API，截图 Profile 页，并通过 Telegram 推送结果。

## 功能

- 登录 → 签到 API（已签到也算成功）
- 成功 / 失败都截图 Profile
- Telegram 推送文字 + 截图
- 支持 `workflow_dispatch` 手动跑
- 定时：UTC `12 16 * * *` = 北京时间每天 **00:12**

## 仓库 Secrets（Settings → Secrets and variables → Actions）

| Name | 说明 |
|---|---|
| `ARITYFLOW_USERNAME` | 登录用户名 |
| `ARITYFLOW_PASSWORD` | 登录密码 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（@BotFather） |
| `TELEGRAM_CHAT_ID` | 接收推送的 Chat ID |

可选 Variables：

| Name | 默认 | 说明 |
|---|---|---|
| `ARITYFLOW_BASE_URL` | `https://www.arityflow.top` | 站点地址 |
| `QUOTA_DIVISOR` | `5000` | 原始 quota ÷ 该值 = 🍀 显示 |

## 本地验证

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium

export ARITYFLOW_USERNAME='你的用户名'
export ARITYFLOW_PASSWORD='你的密码'
export TELEGRAM_BOT_TOKEN='123:ABC'
export TELEGRAM_CHAT_ID='你的chat_id'

python scripts/checkin.py
```

退出码：

- `0` 签到成功或今日已签到，且 TG 推送成功
- `1` 签到失败
- `2` 签到成功但 TG 推送失败

## 部署

1. 把本目录推到 GitHub 仓库（可新建私有仓）
2. 按上表配置 Secrets
3. Actions 页启用 workflow，点 **Run workflow** 先手动验证
4. 通过后等定时任务即可

## 说明

- 密码只放在 GitHub Secrets / 本地环境变量，不要写进代码
- 截图依赖 Chromium；API 签到本身不依赖浏览器
- 若站点改版导致 Profile 选择器变化，截图可能不全，但 API 签到仍可用
