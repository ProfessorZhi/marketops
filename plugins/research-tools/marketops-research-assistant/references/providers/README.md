# 包内 provider 手册副本

这些文件是 `plugins/research-tools/providers/*.md` 的**打包副本**：Skill 与 reference
必须能在**完整插件包内**自洽解析，而 provider 手册的规范位置在插件目录之外，bundle
不会包含它。此处保留包内副本，使 `references/PROVIDER_ROUTING.md` 的链接在源码树与
打包产物两侧指向同一份内容。

- 规范来源：`plugins/research-tools/providers/<name>.md`
- 本目录内容必须与规范来源逐字节一致；不一致即漂移，测试会报错。
- 只复制文档，不复制任何运行时数据、凭据或客户材料。
