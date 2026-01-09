import bpy

# UI列表显示预设
class UI_UL_gradient_preset_list(bpy.types.UIList):
    """预设列表UI"""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            layout.label(text=item.name, icon='PRESET')
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text="", icon='PRESET')

# 预设方案数据结构
class GradientColorItem(bpy.types.PropertyGroup):
    """单个渐变颜色项"""
    color: bpy.props.FloatVectorProperty(
        name="颜色",
        subtype='COLOR',
        default=(1.0, 1.0, 1.0),
        min=0.0, max=1.0
    )

class GradientPreset(bpy.types.PropertyGroup):
    """渐变预设方案"""
    name: bpy.props.StringProperty(name="预设名称", default="新预设")
    colors: bpy.props.CollectionProperty(type=GradientColorItem)
    active_color_index: bpy.props.IntProperty(default=0)

class ColorfulConnectionsSettings(bpy.types.PropertyGroup):
    """彩色连线设置"""
    
    # --- 追踪模式设置 ---
    trace_mode: bpy.props.EnumProperty(
        name="追踪模式",
        description="选择连线显示的逻辑",
        items=[
            ('ALL_SELECTED', '所有选中', '显示所有被选中节点的连线 (默认)'),
            ('ACTIVE_FLOW', '活动节点流', '仅显示当前活动节点的数据流向'),
        ],
        default='ALL_SELECTED'
    )
    
    flow_direction: bpy.props.EnumProperty(
        name="流向",
        description="数据流追踪的方向",
        items=[
            ('BOTH', '双向', '显示输入来源和输出去向'),
            ('UPSTREAM', '向上追溯 (输入)', '仅显示数据的来源'),
            ('DOWNSTREAM', '向下传递 (输出)', '仅显示数据被哪里使用了'),
        ],
        default='DOWNSTREAM'
    )
    
    lock_flow: bpy.props.BoolProperty(
        name="固定当前流",
        description="勾选后固定当前显示的流，不会随活动节点变化而刷新。取消勾选后恢复自动刷新",
        default=False
    )
    
    connection_color_type: bpy.props.EnumProperty(
        name="连线颜色类型",
        description="选择连线颜色的类型",
        items=[
            ('RAINBOW', '彩虹色', '使用彩虹色显示连线'),
            ('CUSTOM', '自定义', '使用自定义渐变色显示连线'),
        ],
        default='RAINBOW'
    )
    
    # --- 动态颜色系统 ---
    gradient_colors: bpy.props.CollectionProperty(type=GradientColorItem)
    gradient_color_count: bpy.props.IntProperty(
        name="颜色数量",
        description="渐变中使用的颜色数量",
        default=5,
        min=2,
        max=10,
        update=lambda self, context: self._update_color_count()
    )
    
    def _update_color_count(self):
        """更新颜色数量时，确保集合中有足够的颜色项"""
        target_count = self.gradient_color_count
        current_count = len(self.gradient_colors)
        
        # 如果数量减少，只保留前N个
        while len(self.gradient_colors) > target_count:
            self.gradient_colors.remove(len(self.gradient_colors) - 1)
        
        # 如果数量增加，添加默认颜色
        while len(self.gradient_colors) < target_count:
            item = self.gradient_colors.add()
            # 设置一些默认颜色
            idx = len(self.gradient_colors) - 1
            defaults = [
                (0.0, 0.5, 1.0),  # 蓝
                (0.0, 1.0, 0.8),  # 青
                (1.0, 1.0, 0.0),  # 黄
                (1.0, 0.5, 0.0),  # 橙
                (1.0, 0.0, 0.5),  # 红粉
            ]
            if idx < len(defaults):
                item.color = defaults[idx]
            else:
                item.color = (1.0, 1.0, 1.0)
    
    # --- 预设系统 ---
    gradient_presets: bpy.props.CollectionProperty(type=GradientPreset)
    active_preset_index: bpy.props.IntProperty(default=-1)
    
    animation_speed: bpy.props.FloatProperty(
        name="动画速度",
        description="设置颜色流动动画的速度",
        default=2.0,
        min=0.1,
        max=10.0
    )
    
    line_thickness: bpy.props.FloatProperty(
        name="连线粗细",
        description="设置连线的粗细程度",
        default=5.0,
        min=1.0,
        max=20.0
    )

    node_border_thickness: bpy.props.FloatProperty(
        name="节点边框粗细",
        description="设置节点彩色边框的粗细程度",
        default=3.0,
        min=1.0,
        max=20.0
    )
    
    enable_colorful_connections: bpy.props.BoolProperty(
        name="启用彩色连线",
        description="启用或禁用彩色连线功能",
        default=True
    )

# 操作符：保存预设
class NODE_OT_save_gradient_preset(bpy.types.Operator):
    """保存当前颜色配置为预设"""
    bl_idname = "node.save_gradient_preset"
    bl_label = "保存预设"
    bl_options = {'REGISTER', 'UNDO'}
    
    preset_name: bpy.props.StringProperty(name="预设名称", default="新预设")
    
    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)
    
    def execute(self, context):
        settings = context.scene.colorful_connections_settings
        preset = settings.gradient_presets.add()
        preset.name = self.preset_name
        
        # 复制当前颜色到预设
        for color_item in settings.gradient_colors:
            new_color = preset.colors.add()
            new_color.color = color_item.color[:]
        
        settings.active_preset_index = len(settings.gradient_presets) - 1
        return {'FINISHED'}

# 操作符：应用预设
class NODE_OT_apply_gradient_preset(bpy.types.Operator):
    """应用选中的预设"""
    bl_idname = "node.apply_gradient_preset"
    bl_label = "应用预设"
    bl_options = {'REGISTER', 'UNDO'}
    
    preset_index: bpy.props.IntProperty(default=-1)
    
    def execute(self, context):
        settings = context.scene.colorful_connections_settings
        idx = self.preset_index if self.preset_index >= 0 else settings.active_preset_index
        
        if idx < 0 or idx >= len(settings.gradient_presets):
            return {'CANCELLED'}
        
        preset = settings.gradient_presets[idx]
        
        # 清空当前颜色
        settings.gradient_colors.clear()
        
        # 复制预设颜色
        for preset_color in preset.colors:
            new_color = settings.gradient_colors.add()
            new_color.color = preset_color.color[:]
        
        # 更新颜色数量
        settings.gradient_color_count = len(preset.colors)
        settings.active_preset_index = idx
        
        return {'FINISHED'}

# 操作符：删除预设
class NODE_OT_delete_gradient_preset(bpy.types.Operator):
    """删除预设"""
    bl_idname = "node.delete_gradient_preset"
    bl_label = "删除预设"
    bl_options = {'REGISTER', 'UNDO'}
    
    preset_index: bpy.props.IntProperty(default=-1)
    
    def execute(self, context):
        settings = context.scene.colorful_connections_settings
        idx = self.preset_index if self.preset_index >= 0 else settings.active_preset_index
        
        if idx < 0 or idx >= len(settings.gradient_presets):
            return {'CANCELLED'}
        
        settings.gradient_presets.remove(idx)
        
        # 调整活动索引
        if settings.active_preset_index >= len(settings.gradient_presets):
            settings.active_preset_index = len(settings.gradient_presets) - 1
        
        return {'FINISHED'}

class NODE_PT_colorful_connections_panel(bpy.types.Panel):
    """彩色连线面板"""
    bl_label = "彩色连线设置"
    bl_idname = "NODE_PT_colorful_connections_panel"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "彩色连线"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # 获取面板设置
        settings = scene.colorful_connections_settings

        col = layout.column()
        
        # 启用/禁用开关
        col.prop(settings, "enable_colorful_connections")
        
        col.separator()
        
        # --- 追踪逻辑 UI ---
        col.label(text="追踪逻辑:")
        row = col.row()
        row.prop(settings, "trace_mode", expand=True)
        
        if settings.trace_mode == 'ACTIVE_FLOW':
            row = col.row()
            row.prop(settings, "flow_direction", expand=True)
            
            # 固定流选项
            row = col.row()
            row.prop(settings, "lock_flow", text="固定当前流", icon='LOCKED' if settings.lock_flow else 'UNLOCKED')
            
            # 快速选择按钮
            op = col.operator("node.select_flow_nodes", text="选中数据流节点", icon='RESTRICT_SELECT_OFF')
        
        col.separator()
        
        # 颜色类型选择
        col.label(text="颜色设置:")
        row = col.row()
        row.prop_enum(settings, "connection_color_type", 'RAINBOW')
        row.prop_enum(settings, "connection_color_type", 'CUSTOM')
        
        # --- 自定义颜色UI ---
        if settings.connection_color_type == 'CUSTOM':
            box = col.box()
            
            # 预设管理 - 紧凑布局
            row = box.row()
            row.label(text="预设方案:")
            
            if len(settings.gradient_presets) > 0:
                # 预设列表和操作按钮在同一行
                split = box.split(factor=0.7)
                split.template_list(
                    "UI_UL_gradient_preset_list",
                    "",
                    settings,
                    "gradient_presets",
                    settings,
                    "active_preset_index",
                    rows=2
                )
                
                col_presets = split.column(align=True)
                col_presets.operator("node.apply_gradient_preset", text="应用", icon='PLAY')
                col_presets.operator("node.save_gradient_preset", text="保存", icon='FILE_TICK')
                col_presets.operator("node.delete_gradient_preset", text="删除", icon='TRASH')
            else:
                box.operator("node.save_gradient_preset", text="保存当前为预设", icon='FILE_TICK')
            
            box.separator()
            
            # 颜色数量控制
            row = box.row()
            row.label(text="颜色数量:")
            row.prop(settings, "gradient_color_count", text="")
            
            box.separator()
            
            # 紧凑的颜色列表 - 只显示实际使用的颜色
            box.label(text="颜色列表 (循环渐变):")
            
            # 使用紧凑布局：每行2个颜色
            active_count = settings.gradient_color_count
            colors = settings.gradient_colors
            
            # 确保有足够的颜色
            while len(colors) < active_count:
                item = colors.add()
                defaults = [
                    (0.0, 0.5, 1.0), (0.0, 1.0, 0.8), (1.0, 1.0, 0.0),
                    (1.0, 0.5, 0.0), (1.0, 0.0, 0.5)
                ]
                idx = len(colors) - 1
                item.color = defaults[idx % len(defaults)]
            
            # 紧凑显示：每行2个
            for i in range(0, active_count, 2):
                row = box.row()
                for j in range(2):
                    idx = i + j
                    if idx < active_count:
                        row.prop(colors[idx], "color", text=f"{idx+1}")
        
        col.separator()
        col.label(text="外观设置:")
        col.prop(settings, "line_thickness")
        col.prop(settings, "node_border_thickness")
        col.prop(settings, "animation_speed")
        
        col.separator()
        col.label(text="编辑器设置:")
        layout.prop(context.preferences.themes[0].node_editor, "noodle_curving", text="曲线因子")

classes = [
    UI_UL_gradient_preset_list,
    GradientColorItem,
    GradientPreset,
    ColorfulConnectionsSettings,
    NODE_PT_colorful_connections_panel,
    NODE_OT_save_gradient_preset,
    NODE_OT_apply_gradient_preset,
    NODE_OT_delete_gradient_preset,
]

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    
    bpy.types.Scene.colorful_connections_settings = bpy.props.PointerProperty(type=ColorfulConnectionsSettings)
    
    # 初始化默认颜色（如果是第一次创建）
    def init_default_colors():
        try:
            scene = bpy.context.scene
            if hasattr(scene, 'colorful_connections_settings'):
                settings = scene.colorful_connections_settings
                if len(settings.gradient_colors) == 0:
                    settings.gradient_color_count = 5  # 这会触发 _update_color_count
        except:
            pass
    
    # 延迟初始化
    bpy.app.timers.register(init_default_colors, first_interval=0.1)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.colorful_connections_settings
