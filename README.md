# Osenotify
一个通过开放数据源实时监听地震速报并使用Gotify推送强提醒到手机的云上解决方案
# 地震速报监听程序

感谢[Wolfx Project](https://wolfx.jp/)，[FAN Studio API](https://api.fanstudio.tech/)和[Gotify](https://github.com/gotify)对本项目的大力支持！

这是一个用于监听日本气象厅(JMA)和中国地震预警网(CEA)地震预警信息的Python程序。当检测到达到设定阈值的地震时，程序会自动通过Gotify推送通知。

## 功能特点

- 监听日本气象厅(JMA)和中国地震预警网(CEA)的地震预警信息
- 支持自定义触发阈值
- 通过Gotify推送通知
- 自动日志管理和轮转
- 健康检查接口
- 支持冷却时间设置，避免重复通知

## 安装要求

- Python 3.7+
- 以下Python库：
  - requests
  - websocket-client
  - tenacity
  - flask

## 安装步骤

1. 克隆或下载此仓库到您的服务器
2. 安装所需的Python库：

```bash
pip install requests websocket-client tenacity flask
```

3. 配置程序（见下文）

## 配置说明

程序需要配置以下参数，您可以通过两种方式配置：

### 方式一：使用环境变量（推荐）

设置以下环境变量：

```bash
export QUAKE_COOLDOWN=360  # 冷却时间(秒)
export QUAKE_TRIGGER_JMA_INTENSITY="5弱"  # JMA触发阈值
export QUAKE_TRIGGER_CEA_INTENSITY=7.0  # CEA触发阈值(烈度)
export QUAKE_GOTIFY_URL="http://your.gotify.server:port"  # Gotify服务器地址
export QUAKE_GOTIFY_APP_TOKEN="your_gotify_app_token"  # Gotify应用Token
export QUAKE_WS_JMA="wss://ws-api.wolfx.jp/jma_eew"  # JMA WebSocket地址
export QUAKE_WS_CEA="wss://ws.fanstudio.tech/cea"  # CEA WebSocket地址
export QUAKE_LOG_DIR="./logs"  # 日志目录
```

### 方式二：直接修改代码中的默认配置

在代码中找到`DEFAULT_CONFIG`部分，修改以下参数：

python

```
DEFAULT_CONFIG = AppConfig(
    cooldown=360,  # 冷却时间(秒)
    trigger_jma_intensity="5弱",  # JMA触发阈值
    trigger_cea_intensity=7.0,  # CEA触发阈值(烈度)
    gotify_url="http://your.gotify.server:port",  # Gotify服务器地址
    gotify_app_token="your_gotify_app_token",  # Gotify应用Token
    ws_jma="wss://ws-api.wolfx.jp/jma_eew",  # JMA WebSocket地址
    ws_cea="wss://ws.fanstudio.tech/cea",  # CEA WebSocket地址
    log_dir="./logs"  # 日志目录
)
```

## JMA震度等级说明

JMA使用以下震度等级，您可以根据需要设置触发阈值：

- "0" - 震度0
- "1" - 震度1
- "2" - 震度2
- "3" - 震度3
- "4" - 震度4
- "5弱" - 震度5弱
- "5強" - 震度5强
- "6弱" - 震度6弱
- "6強" - 震度6强
- "7" - 震度7

## 使用说明

1. 确保您已设置好Gotify服务器并获取应用Token

2. 根据您的需求调整触发阈值

3. 运行程序：

   ```bash
   python obsenotify.py
   ```

4. 程序将在后台运行并监听地震预警信息

## 健康检查

程序提供了一个健康检查接口，可通过以下URL访问：

```
http://your-server:5000/health
```

## 日志管理

程序会自动管理日志文件：

- 日志保存在指定的日志目录中（默认为`./logs`）
- 单个日志文件最大为5MB
- 保留最近5个日志备份
- 自动清理30天前的旧日志

## 注意事项

- 请确保服务器时间设置正确
- 确保服务器网络连接稳定，能够访问JMA和CEA的WebSocket服务

## 故障排除

如果程序无法正常工作，请检查：

1. Gotify服务器地址和Token是否正确
2. 网络连接是否正常
3. 防火墙设置是否允许出站连接
4. 查看日志文件获取详细错误信息
