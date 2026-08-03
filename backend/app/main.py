"""FastAPI 应用入口：负责"组装"整个应用并暴露给 uvicorn 启动。

设计角度：为什么用 create_app() 工厂函数而不是直接写 app = FastAPI()？
- 测试里可以反复创建全新实例（每个实例状态独立，互不干扰）
- 依赖组装（build_deps）集中在这一处，替换依赖（比如测试用内存存储）只需改这里
"""

from fastapi import FastAPI

from app.api import chat, health, settings as settings_api  # 起别名避免与配置变量同名
from app.config import settings  # 应用配置对象（注意：这里的 settings 不是 api 模块）
from app.di import build_deps  # 依赖组装工厂


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.state.deps = build_deps()  # 把依赖挂到 app.state，路由里通过 request.app.state.deps 取
    app.include_router(health.router)  # 注册 /health
    app.include_router(chat.router)  # 注册 /api/chat
    app.include_router(settings_api.router)  # 注册 /api/settings
    return app


# 模块级实例：uvicorn 启动命令里的 app.main:app 加载的就是它
app = create_app()
