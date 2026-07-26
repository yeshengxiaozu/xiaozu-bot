"""测试包。

这个 __init__.py 是故意留着的：有了它，tests/ 及其子目录下的测试模块会以
`tests.xxx.test_yyy` 这样的完整包名被 import，不同子目录里重名的
test_*.py 就不会互相顶掉（pytest 默认的 prepend import 模式下，
没有 __init__.py 的同名模块会直接冲突报错）。

有 7 个人同时往 tests/ 里加文件，重名几乎是必然的，所以宁可多这一个空包。
新建子目录时也请顺手补一个 __init__.py。
"""
