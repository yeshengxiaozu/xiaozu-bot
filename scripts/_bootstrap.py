"""让 gdlevelsearch 的子模块能脱离 bot 进程被 import。

`import xiaozu_bot.plugins.gdlevelsearch.draw` 会先执行包的 __init__.py，
而那个 __init__.py 要拉 onebot 适配器、htmlkit、注册 matcher —— 只有真在
bot 里跑才装得齐。

这里往 sys.modules 里塞一个同名的空壳包（把 __path__ 指对），Python 就不会
再去执行真正的 __init__.py，但子模块之间的相对 import（draw.py 里的
`from .gdapi import ...`）照样能正常解析。
"""

import importlib
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = "xiaozu_bot.plugins.gdlevelsearch"
PLUGIN_DIR = REPO_ROOT / "xiaozu_bot" / "plugins" / "gdlevelsearch"


def load_gdlevelsearch() -> types.ModuleType:
    """装好空壳包，返回它。之后就能 import_submodule('draw') 了。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    # 父包也要是空壳，否则 import xiaozu_bot.plugins 会去跑 plugins/ 下的东西
    for name, path in (
        ("xiaozu_bot", REPO_ROOT / "xiaozu_bot"),
        ("xiaozu_bot.plugins", REPO_ROOT / "xiaozu_bot" / "plugins"),
        (PKG, PLUGIN_DIR),
    ):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            mod.__path__ = [str(path)]  # 标成 package
            sys.modules[name] = mod

    return sys.modules[PKG]


def import_submodule(name: str) -> types.ModuleType:
    """import gdlevelsearch 下的某个子模块，比如 'draw' / 'nlwapi'。"""
    load_gdlevelsearch()
    return importlib.import_module(f"{PKG}.{name}")
