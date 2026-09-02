"""pytest 全局配置：把向量库指向测试专属目录。

原因：Qdrant 本地模式单进程锁——开发服务器（8000/8899）正占用 backend/data/qdrant，
测试进程必须用独立目录，否则收集阶段就会撞锁。
"""

import os
from pathlib import Path

# 必须在 import app.config（settings 单例）之前设置环境变量
os.environ.setdefault(
    "ASH_QDRANT_PATH", str(Path(__file__).resolve().parent.parent / ".pytest-tmp" / "qdrant")
)
