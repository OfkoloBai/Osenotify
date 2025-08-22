"""
地震速报监听程序 - Osenotify
监听日本气象厅(JMA)和中国地震预警网(CEA)的地震预警信息
当检测到达到设定阈值的地震时，自动通过Gotify推送通知
去除OBS录制、Windows特定功能，适配云服务器环境
"""
import os
import json
import time
import logging
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Set
from logging.handlers import RotatingFileHandler
from enum import Enum, auto
from pathlib import Path
from flask import Flask
import threading
import glob  # 添加到导入部分

# 第三方库
import requests
import websocket
from tenacity import retry, stop_after_attempt, wait_exponential

# =========================
# 枚举和常量定义
# =========================
class AlertSource(Enum):
    """预警来源枚举"""
    JMA = auto()  # 日本气象厅
    CEA = auto()  # 中国地震预警网
    TEST = auto()  # 测试来源

class ProgramState(Enum):
    """程序状态枚举"""
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()

# JMA震度映射表（从小到大）
JMA_INTENSITY_MAP = {
    "0": 0,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5弱": 5,
    "5強": 6,
    "6弱": 7,
    "6強": 8,
    "7": 9
}

# =========================
# 配置类（使用dataclass管理配置）
# =========================
@dataclass
class AppConfig:
    # 触发和冷却配置
    cooldown: int
    trigger_jma_intensity: str
    trigger_cea_intensity: float
    
    # Gotify推送配置
    gotify_url: str
    gotify_app_token: str
    
    # WebSocket和网络配置
    ws_jma: str
    ws_cea: str
    
    # 日志和文件配置
    log_dir: str
    max_log_size: int = 5 * 1024 * 1024  # 5MB
    log_backup_count: int = 5
    log_retention_days: int = 30  # 日志保留天数
    
    # 其他配置
    ws_reconnect_delay: int = 5  # WebSocket重连延迟(秒)
    log_cleanup_interval: int = 86400  # 日志清理间隔(秒)，默认每天一次
    
    def to_dict(self) -> Dict[str, Any]:
        """将配置转换为字典"""
        return asdict(self)

# 默认配置（调整为云服务器环境，添加Gotify配置）
DEFAULT_CONFIG = AppConfig(
    cooldown=int(os.environ.get('QUAKE_COOLDOWN', 360)),
    trigger_jma_intensity=os.environ.get('QUAKE_TRIGGER_JMA_INTENSITY', "5弱"),
    trigger_cea_intensity=float(os.environ.get('QUAKE_TRIGGER_CEA_INTENSITY', 7.0)),
    gotify_url=os.environ.get('QUAKE_GOTIFY_URL', "http://your.gotify.server:port"),
    gotify_app_token=os.environ.get('QUAKE_GOTIFY_APP_TOKEN', "your_gotify_app_token_here"),
    ws_jma=os.environ.get('QUAKE_WS_JMA', "wss://ws-api.wolfx.jp/jma_eew"),
    ws_cea=os.environ.get('QUAKE_WS_CEA', "wss://ws.fanstudio.tech/cea"),
    log_dir=os.environ.get('QUAKE_LOG_DIR', os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"))
)

# =========================
# 全局状态管理类
# =========================
class GlobalState:
    """管理程序全局状态"""
    def __init__(self):
        self.config = DEFAULT_CONFIG
        self.monitoring_enabled = True
        self.program_state = ProgramState.RUNNING
        self.last_trigger_time = 0.0
        self.triggered_event_ids: Set[str] = set()
        self.logger: Optional[logging.Logger] = None
        self.lock = threading.Lock()  # 添加线程锁
        
    def is_in_cooldown(self) -> bool:
        """检查是否处于冷却时间内"""
        return time.time() - self.last_trigger_time < self.config.cooldown
    
    def update_trigger_time(self):
        """更新最后触发时间"""
        self.last_trigger_time = time.time()
    
    def add_triggered_event(self, event_id: str):
        """添加已触发的事件ID"""
        self.triggered_event_ids.add(event_id)
    
    def is_event_triggered(self, event_id: str) -> bool:
        """检查事件是否已触发过"""
        return event_id in self.triggered_event_ids
    
    def cleanup(self):
        """清理资源"""
        pass  # 云服务器版无需特定清理

# 全局状态实例
state = GlobalState()

# =========================
# 工具函数
# =========================
def setup_logging() -> logging.Logger:
    """设置日志系统"""
    # 确保日志目录存在
    os.makedirs(state.config.log_dir, exist_ok=True)
    log_file = os.path.join(state.config.log_dir, "quake_monitor.log")
    
    # 创建日志记录器
    logger = logging.getLogger("quake_monitor")
    logger.setLevel(logging.INFO)
    
    # 清除可能存在的旧处理器
    logger.handlers.clear()
    
    # 文件处理器（带轮转）
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=state.config.max_log_size, 
        backupCount=state.config.log_backup_count, 
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    
    # 添加处理器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def validate_config(config: AppConfig) -> bool:
    """验证配置是否有效"""
    errors = []
    
    # 检查配置值有效性
    if config.trigger_jma_intensity not in JMA_INTENSITY_MAP:
        errors.append(f"JMA阈值设置无效: {config.trigger_jma_intensity}")
    
    if config.trigger_cea_intensity <= 0:
        errors.append(f"CEA阈值必须大于0: {config.trigger_cea_intensity}")
    
    # 检查Gotify配置（可选，但建议验证URL格式）
    if not config.gotify_url or not config.gotify_app_token:
        errors.append("Gotify配置缺失")
    
    # 记录所有错误
    if errors:
        for error in errors:
            state.logger.error(error)
        return False
    
    return True

def ensure_directory_exists(path: str) -> bool:
    """确保目录存在，如果不存在则创建"""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError as e:
        state.logger.error(f"创建目录失败 {path}: {e}")
        return False

def cleanup_old_logs():
    """清理旧的日志文件"""
    try:
        # 计算截止日期
        cutoff_time = time.time() - (state.config.log_retention_days * 86400)  # 86400秒=1天
        
        # 获取所有日志文件
        log_pattern = os.path.join(state.config.log_dir, "quake_monitor.log*")
        log_files = glob.glob(log_pattern)
        
        # 删除过期的日志文件
        for log_file in log_files:
            # 检查文件修改时间
            if os.path.isfile(log_file) and os.path.getmtime(log_file) < cutoff_time:
                os.remove(log_file)
                state.logger.info(f"已删除旧日志文件: {os.path.basename(log_file)}")
                
    except Exception as e:
        state.logger.error(f"清理日志文件时出错: {e}")

def log_cleanup_loop():
    """日志清理循环"""
    while state.program_state != ProgramState.STOPPING:
        try:
            # 等待清理间隔时间
            time.sleep(state.config.log_cleanup_interval)
            
            # 执行日志清理
            if state.program_state != ProgramState.STOPPING:
                cleanup_old_logs()
                
        except Exception as e:
            state.logger.error(f"日志清理循环出错: {e}")
            # 出错后等待一段时间再继续
            time.sleep(3600)  # 1小时

# =========================
# 核心功能函数
# =========================
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def push_gotify(title: str, message: str, priority: int = 10):
    """通过 Gotify 推送通知（带重试机制）"""
    try:
        url = f"{state.config.gotify_url}/message?token={state.config.gotify_app_token}"
        payload = {
            "title": title,
            "message": message,
            "priority": priority
        }

        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        state.logger.info("已通过 Gotify 推送手机通知")

    except requests.exceptions.RequestException as e:
        state.logger.error(f"Gotify 推送失败: {e}")
        raise

def unified_trigger(source: AlertSource, lines: List[str], event_id: Optional[str] = None):
    """
    统一触发处理函数，处理地震预警事件
    
    Args:
        source: 预警来源
        lines: 预警信息内容列表
        event_id: 事件ID，用于去重（可选）
    """
    # 使用线程锁确保状态检查的原子性
    with state.lock:
        # 检查是否启用监控
        if not state.monitoring_enabled:
            state.logger.info("监控已暂停，忽略触发")
            return
            
        # 去重检查
        if event_id and state.is_event_triggered(event_id):
            state.logger.info(f"事件 {event_id} 已触发过，忽略")
            return
            
        # 冷却检查
        if state.is_in_cooldown():
            state.logger.info("冷却时间内，忽略触发")
            return
            
        # 更新触发时间和事件ID
        state.update_trigger_time()
        if event_id:
            state.add_triggered_event(event_id)

    # 生成内容并记录
    source_name = {
        AlertSource.JMA: "日本气象厅 (JMA)",
        AlertSource.CEA: "中国地震预警网 (CEA)",
        AlertSource.TEST: "人工测试"
    }[source]
    
    content = "\n".join(lines)
    
    # 将耗时操作放在单独线程中执行，避免阻塞WebSocket消息处理
    def trigger_operations():
        state.logger.info(f"触发来源: {source_name}\n{content}")
        
        # 推送 Gotify（强提醒）
        push_gotify("⚠️ 强震预警", f"来源: {source_name}\n{content}", priority=10)
    
    # 启动线程执行触发操作
    threading.Thread(target=trigger_operations, daemon=True).start()

# =========================
# WebSocket 处理函数
# =========================
def on_message_jma(ws, message):
    """处理JMA WebSocket消息"""
    if not state.monitoring_enabled:
        return
        
    try:
        data = json.loads(message)
        
        # 忽略取消、训练和假设报文
        if data.get("isCancel", False) or data.get("isTraining", False) or data.get("isAssumption", False):
            state.logger.info("JMA 非正式/取消报文，忽略")
            return
            
        # 获取最大震度
        max_intensity = str(data.get("MaxIntensity", "")).strip()
        
        # 使用映射表进行比较
        current_intensity_value = JMA_INTENSITY_MAP.get(max_intensity, -1)
        threshold_value = JMA_INTENSITY_MAP.get(state.config.trigger_jma_intensity, -1)
        
        # 检查是否达到阈值
        if current_intensity_value >= threshold_value and current_intensity_value != -1:
            place = data.get("Hypocenter", "")
            mag = data.get("Magunitude", "")  # 注意：数据源字段就是这个拼写
            depth = data.get("Depth", "")
            ann = data.get("AnnouncedTime", "")
            eid = str(data.get("EventID", ""))
            
            lines = [
                f"地点: {place}",
                f"最大震度: {max_intensity}",
                f"震级: M{mag}   深度: {depth} km",
                f"发布时间: {ann}",
                f"事件ID: {eid}"
            ]
            
            unified_trigger(AlertSource.JMA, lines, eid)
        else:
            state.logger.info(f"JMA 更新：最大震度 {max_intensity} (阈值: {state.config.trigger_jma_intensity})")
            
    except json.JSONDecodeError as e:
        state.logger.error(f"JMA JSON解析错误: {e}")
    except Exception as e:
        state.logger.error(f"JMA 解析错误: {e}")

def on_message_cea(ws, message):
    """处理CEA WebSocket消息"""
    if not state.monitoring_enabled:
        return
        
    try:
        data = json.loads(message)
        d = data.get("Data", {})
        
        if not d:
            return
            
        place = d.get("placeName", "")
        mag = d.get("magnitude", "")
        depth = d.get("depth", "")
        shock = d.get("shockTime", "")
        eid = str(d.get("eventId", ""))
        epi = d.get("epiIntensity", 0)
        
        try:
            epi_val = float(epi)
        except (ValueError, TypeError):
            epi_val = 0.0
            
        # 检查是否达到阈值
        if epi_val >= state.config.trigger_cea_intensity:
            lines = [
                f"地点: {place}",
                f"预估烈度: {epi_val}",
                f"震级: M{mag}   深度: {depth} km",
                f"发震时刻: {shock}",
                f"事件ID: {eid}"
            ]
            
            unified_trigger(AlertSource.CEA, lines, eid)
        else:
            state.logger.info(f"CEA 更新：烈度 {epi_val} (< {state.config.trigger_cea_intensity})")
            
    except json.JSONDecodeError as e:
        state.logger.error(f"CEA JSON解析错误: {e}")
    except Exception as e:
        state.logger.error(f"CEA 解析错误: {e}")

def ws_loop(name: str, url: str, handler: Callable):
    """WebSocket连接循环（带自动重连）"""
    while state.program_state != ProgramState.STOPPING:
        try:
            ws = websocket.WebSocketApp(
                url,
                on_message=handler,
                on_error=lambda _ws, err: state.logger.error(f"{name} 错误: {err}"),
                on_close=lambda _ws, code, msg: state.logger.info(f"{name} 关闭: {code} {msg}")
            )
            ws.on_open = lambda _ws: state.logger.info(f"已连接 {name}")
            ws.run_forever(ping_interval=25, ping_timeout=10)
        except Exception as e:
            state.logger.error(f"{name} run_forever 异常: {e}")
            
        # 检查是否需要停止
        if state.program_state == ProgramState.STOPPING:
            break
            
        # 断线重连间隔
        time.sleep(state.config.ws_reconnect_delay)

# =========================
# 主程序
# =========================
def main():
    """主程序入口"""
    # 初始化日志
    state.logger = setup_logging()
    
    # 验证配置
    if not validate_config(state.config):
        state.logger.error("配置验证失败，程序退出")
        return
    
    # 确保日志目录存在
    if not ensure_directory_exists(state.config.log_dir):
        state.logger.error("无法创建日志目录，程序退出")
        return
    
    # 启动日志清理线程
    log_cleanup_thread = threading.Thread(target=log_cleanup_loop, daemon=True)
    log_cleanup_thread.start()
    
    state.logger.info(
        f"程序已启动 "
        f"(JMA阈值: {state.config.trigger_jma_intensity}, "
        f"CEA阈值: {state.config.trigger_cea_intensity}, "
        f"日志保留天数: {state.config.log_retention_days})"
    )

    # 启动两个 WS 线程
    jma_thread = threading.Thread(
        target=ws_loop, 
        args=("JMA", state.config.ws_jma, on_message_jma), 
        daemon=True
    )
    
    cea_thread = threading.Thread(
        target=ws_loop, 
        args=("CEA", state.config.ws_cea, on_message_cea), 
        daemon=True
    )
    
    jma_thread.start()
    cea_thread.start()

    # 主线程循环
    try:
        while state.program_state != ProgramState.STOPPING:
            time.sleep(1)
            
    except KeyboardInterrupt:
        state.logger.info("收到中断信号，程序退出")
        
    finally:
        state.program_state = ProgramState.STOPPING
        state.cleanup()

app = Flask(__name__)

@app.route('/health')
def health_check():
    return 'OK', 200

def run_health_server():
    app.run(host='0.0.0.0', port=5000)

# 在主程序启动时启动健康检查服务器
if __name__ == "__main__":
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    main()
