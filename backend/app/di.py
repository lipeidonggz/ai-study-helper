"""依赖组装（简单工厂）：骨架阶段默认全部使用内存适配器。

未来替换：SQLite 适配器就绪后，在这里换成对应实现即可，业务层无感知。
"""

from dataclasses import dataclass

from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore


@dataclass
class AppDeps:
    session_store: InMemorySessionStore
    setting_store: InMemorySettingStore
    log_store: InMemoryLogStore


def build_deps() -> AppDeps:
    return AppDeps(
        session_store=InMemorySessionStore(),
        setting_store=InMemorySettingStore(),
        log_store=InMemoryLogStore(),
    )
