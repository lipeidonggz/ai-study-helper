"""backend/app 包：AI 助手的后端应用。

这里只放版本号。真正的功能按职责拆在子包里：
- api/     对外 HTTP 接口（前后端契约层）
- agent/   对话大脑（Agent loop、LLM 客户端、上下文组装）
- tools/   工具（注册表、内置工具、执行器）
- storage/ 存储（接口定义 + SQLite/内存实现）
- kb/      知识库服务（阶段 2 再实现）
"""

__version__ = "0.1.0"
