"""健康检查接口：部署、监控、测试用来确认服务是否存活。"""

from fastapi import APIRouter

# APIRouter：把一组相关路由打包，再由 main.py 挂到应用上
router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """返回固定 JSON，表示服务正常。"""
    return {"status": "ok", "app": "ai-study-helper"}
