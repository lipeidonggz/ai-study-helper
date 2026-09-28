"""核对 global_search / global_search_streaming 的签名默认值，以及 CLI 是否有默认层。"""

from __future__ import annotations

import inspect

from graphrag.api import query as q


def show(fn) -> None:
    print(f"== {fn.__name__} ==")
    for name, p in inspect.signature(fn).parameters.items():
        # pydantic validate_call 会把注解换成 _PydanticGeneralMetadata 之类，只报"有无默认值"
        has_default = p.default is not inspect.Parameter.empty
        print(f"  {name:<28} {'有默认值: ' + repr(p.default) if has_default else '无默认值（必传）'}")
    print()


def main() -> None:
    show(q.global_search)
    show(q.global_search_streaming)


if __name__ == "__main__":
    main()
