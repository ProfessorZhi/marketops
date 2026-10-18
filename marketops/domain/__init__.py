"""**纯领域层**：业务事实的形状与其不变量。

这一层的唯一纪律是：**不 import 任何 I/O 或厂商模块**。没有 ``httpx``、没有
``crawl4ai``、没有 ``mcp``、没有 ``sqlite3``、没有飞书、没有 ``os.environ``。
它只依赖标准库与 pydantic。

为什么值得为此单开一个包 —— 因为在此之前，这个仓库里**没有任何地方**能回答
「哪些代码是纯的」。``grading/engine.py``（今天的 ``application/grading.py``）
自己不碰 I/O，却 import 了
``adapters/profile_loader.py``，后者 import ``yaml`` 与 ``config.py``，而后者读 ``os.environ``。
于是「纯判定」只在单文件内成立，任何想给引擎写一个不需要磁盘的测试的人，
都会发现它拉进了一整条装载链。

``tests/test_domain_is_pure.py`` 把这条纪律变成可执行的断言：
``marketops/domain/**`` 下的任何模块，其 AST 里不得出现禁止模块。
写在文档里的分层是自觉，写进测试的分层才是约束。
"""
