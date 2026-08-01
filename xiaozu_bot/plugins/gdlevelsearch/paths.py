"""
用于在各插件之间维持一致的路径统一常量文件
"""

from pathlib import Path

from xiaozu_bot.utils.json_storage import plugin_storage

PLUGIN_DIR = Path(__file__).resolve().parent
RES_DIR = PLUGIN_DIR / "resources"
DATA_DIR = PLUGIN_DIR / "data"
ICONS_ZIP = RES_DIR / "icons_uhd.zip"


def storage(name: str = "storage.json") -> Path:
    return plugin_storage(PLUGIN_DIR / "__init__.py", name)
