## 问题描述
- 修复了打开旧文件时的 `AttributeError: Writing to ID classes in this context is not allowed` 错误
- 确保插件从插件目录读取设置，而不是从场景中读取

## 修复内容
- ✅ 移除了 `draw` 方法中对场景数据的修改，避免在 UI 绘制时修改场景数据（这是导致错误的主要原因）
- ✅ 添加了场景加载后的回调函数 `on_load_post`，从插件目录重新加载设置
- ✅ 改进了 `load_global_settings` 函数的错误处理机制
- ✅ 添加了 `.gitignore` 文件，排除 `__pycache__` 等文件

## 修改的文件
- `panels.py`: 修复 AttributeError 和设置加载逻辑
- `.gitignore`: 添加 Python 和 Blender 相关忽略规则

## 测试
- ✅ 新场景正常工作
- ✅ 打开旧文件不再报错
- ✅ 设置从插件目录正确加载，不再依赖场景中保存的设置

## 相关 Issue
修复了在打开旧文件时出现的 AttributeError 错误


