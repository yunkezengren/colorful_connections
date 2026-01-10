import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from math import isfinite
import time
from mathutils import Vector
import colorsys
from ctypes import c_void_p, c_float
from math import pi, sqrt, exp, hypot, sin, cos
import re

# Socket类型到色相偏移的映射（基于HSV色相，范围0-360度）
SOCKET_TYPE_HUE_OFFSETS = {
    'NodeSocketFloat': 0.0,        # 灰色，无偏移
    'NodeSocketInt': 15.0,         # 绿色偏黄
    'NodeSocketVector': -60.0,     # 蓝色
    'NodeSocketColor': 30.0,       # 黄色
    'NodeSocketShader': 120.0,     # 绿色
    'NodeSocketBool': 300.0,       # 紫色
    'NodeSocketString': 200.0,     # 青色
    'NodeSocketObject': 20.0,      # 橙黄色
    'NodeSocketImage': 270.0,      # 粉紫色
    'NodeSocketGeometry': 150.0,   # 青绿色
    'NodeSocketCollection': 0.0,   # 白色，无偏移
    'NodeSocketTexture': 60.0,     # 黄色偏橙
    'NodeSocketMaterial': 350.0,   # 红紫色
    'NodeSocketRotation': 240.0,   # 蓝紫色
    'NodeSocketMenu': 0.0,         # 灰色，无偏移
    'NodeSocketMatrix': 330.0,     # 红紫色
    'NodeSocketClosure': 90.0,     # 黄绿色
}

def get_socket_type_name(socket):
    """获取socket的类型名称"""
    if hasattr(socket, 'bl_idname'):
        return socket.bl_idname
    elif hasattr(socket, 'type'):
        # 兼容性：如果只有type属性，尝试转换
        type_map = {
            'VALUE': 'NodeSocketFloat',
            'INT': 'NodeSocketInt',
            'VECTOR': 'NodeSocketVector',
            'RGBA': 'NodeSocketColor',
            'SHADER': 'NodeSocketShader',
            'BOOLEAN': 'NodeSocketBool',
            'STRING': 'NodeSocketString',
            'OBJECT': 'NodeSocketObject',
            'IMAGE': 'NodeSocketImage',
            'GEOMETRY': 'NodeSocketGeometry',
            'COLLECTION': 'NodeSocketCollection',
            'TEXTURE': 'NodeSocketTexture',
            'MATERIAL': 'NodeSocketMaterial',
            'ROTATION': 'NodeSocketRotation',
            'MENU': 'NodeSocketMenu',
            'MATRIX': 'NodeSocketMatrix',
        }
        return type_map.get(socket.type, 'NodeSocketFloat')
    return 'NodeSocketFloat'  # 默认

def shift_hue(rgb, hue_offset):
    """
    对RGB颜色进行HSV色相偏移
    rgb: (r, g, b) 或 (r, g, b, a)，值范围0-1
    hue_offset: 色相偏移角度（度），范围-180到180
    返回: (r, g, b, a) 格式的颜色
    """
    if len(rgb) >= 3:
        r, g, b = rgb[0], rgb[1], rgb[2]
        alpha = rgb[3] if len(rgb) >= 4 else 1.0
    else:
        return rgb
    
    # 转换为HSV
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    
    # 偏移色相（转换为0-1范围）
    h_offset_normalized = hue_offset / 360.0
    h_new = (h + h_offset_normalized) % 1.0
    
    # 转回RGB
    r_new, g_new, b_new = colorsys.hsv_to_rgb(h_new, s, v)
    
    return (r_new, g_new, b_new, alpha)

def get_socket_hue_offset(socket):
    """获取socket类型的色相偏移值"""
    socket_type = get_socket_type_name(socket)
    return SOCKET_TYPE_HUE_OFFSETS.get(socket_type, 0.0)

def get_socket_circle_size(socket, zoom, base_size=5.0):
    """
    根据socket类型返回圆圈大小
    不同数据类型使用不同的显示尺寸，避免全是小圆点
    """
    socket_type = get_socket_type_name(socket)
    # 根据socket类型分配不同的大小系数
    size_multipliers = {
        'NodeSocketFloat': 1.0,      # 标准大小
        'NodeSocketInt': 1.1,        # 略大
        'NodeSocketVector': 1.15,    # 更大
        'NodeSocketColor': 1.2,      # 更大
        'NodeSocketShader': 1.25,    # 最大
        'NodeSocketBool': 0.9,       # 略小
        'NodeSocketString': 1.1,
        'NodeSocketObject': 1.15,
        'NodeSocketImage': 1.2,
        'NodeSocketGeometry': 1.25,
        'NodeSocketCollection': 1.1,
        'NodeSocketTexture': 1.2,
        'NodeSocketMaterial': 1.2,
        'NodeSocketRotation': 1.15,
        'NodeSocketMatrix': 1.25,
    }
    multiplier = size_multipliers.get(socket_type, 1.0)
    return base_size * multiplier * zoom

def apply_type_based_color_shift(colors, from_socket, to_socket, offset_strength=0.4):
    """
    根据socket类型对颜色进行色相偏移
    offset_strength: 偏移强度（0-1），0.4表示偏移40%的强度，避免颜色变化过大
    """
    # 使用目标socket的类型（因为数据流向目标）
    target_socket = to_socket
    hue_offset = get_socket_hue_offset(target_socket)
    
    # 应用强度系数
    effective_offset = hue_offset * offset_strength
    
    # 对所有颜色应用偏移
    shifted_colors = []
    for color in colors:
        shifted = shift_hue(color, effective_offset)
        shifted_colors.append(shifted)
    
    return shifted_colors

def is_field_link(tree, link):
    """
    判断是否为Field(场)数据流连线
    只在几何节点编辑器中有效
    """
    try:
        if not tree or getattr(tree, 'type', '') != 'GEOMETRY':
            return False
        fs = getattr(link, 'from_socket', None)
        if fs is None:
            return False
        
        # 方法1: 检查socket的is_field属性（Blender 3.0+）
        if hasattr(fs, 'is_field'):
            try:
                field_value = fs.is_field
                if field_value:
                    return True
            except:
                pass
        
        # 方法2: 检查socket的display_shape（Field通常使用DIAMOND形状）
        if hasattr(fs, 'display_shape'):
            try:
                # SOCK_DISPLAY_SHAPE_DIAMOND = 'DIAMOND' 通常表示Field
                if fs.display_shape == 'DIAMOND':
                    return True
            except:
                pass
        
        # 方法3: 通过socket的内部属性判断（使用指针访问）
        try:
            # 尝试通过socket的内部结构判断
            # Blender的socket可能有field相关的内部标志
            socket_ptr = fs.as_pointer()
            if socket_ptr:
                # 在某些Blender版本中，可以通过检查socket的类型标志
                # 这里我们尝试通过其他方式判断
                pass
        except:
            pass
        
        # 方法4: 检查连接的节点类型（某些节点类型通常输出Field）
        from_node = getattr(link, 'from_node', None)
        if from_node:
            # 某些节点类型通常输出Field数据
            field_output_nodes = [
                'ATTRIBUTE_DOMAIN', 'FIELD_AT_INDEX', 'SAMPLE_INDEX', 
                'SAMPLE_NEAREST', 'SAMPLE_NEAREST_SURFACE', 'INTERPOLATE_DOMAIN',
                'EVALUATE_AT_INDEX', 'EVALUATE_ON_DOMAIN'
            ]
            if from_node.type in field_output_nodes:
                return True
            
            # 检查节点名称（某些节点名称包含field相关关键词）
            node_name_lower = (getattr(from_node, 'name', '') or '').lower()
            if 'field' in node_name_lower or 'attribute' in node_name_lower:
                return True
        
        # 方法5: 检查目标socket是否接受Field（如果目标socket是Field类型，源也可能是）
        ts = getattr(link, 'to_socket', None)
        if ts:
            if hasattr(ts, 'is_field'):
                try:
                    if ts.is_field:
                        return True
                except:
                    pass
            if hasattr(ts, 'display_shape'):
                try:
                    if ts.display_shape == 'DIAMOND':
                        return True
                except:
                    pass
                    
    except Exception as e:
        # 调试用：可以打印错误信息
        # print(f"Error checking field link: {e}")
        pass
    return False

def create_dashed_line_segments_smooth(points, dash_length=10.0, gap_length=5.0, time_offset=0.0):
    """
    平滑的虚线生成算法，确保虚线均匀且连续（优化版本）
    """
    if len(points) < 2:
        return []
    
    # 预计算累积距离（避免重复计算）
    cumulative_distances = [0.0]
    path_length = 0.0
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        seg_len = hypot(dx, dy)
        path_length += seg_len
        cumulative_distances.append(path_length)
    
    if path_length == 0:
        return []
    
    pattern_length = dash_length + gap_length
    offset = time_offset % pattern_length
    
    dashed_segments = []
    current_pos = -offset
    max_iterations = int(path_length / min(dash_length, gap_length)) + 10  # 防止无限循环
    iteration = 0
    
    # 沿着路径生成均匀的虚线段（优化版本，限制生成数量）
    max_segments_limit = 20  # 限制最大虚线段数量，防止性能问题
    
    while current_pos < path_length and iteration < max_iterations and len(dashed_segments) < max_segments_limit:
        iteration += 1
        pattern_pos = (current_pos + offset + pattern_length) % pattern_length
        
        if pattern_pos < dash_length:
            # 在虚线部分
            dash_start = current_pos
            dash_end = min(current_pos + (dash_length - pattern_pos), path_length)
            
            if dash_end > dash_start and (dash_end - dash_start) >= 2.0:  # 最小长度限制提高到2像素
                # 生成虚线段（大幅减少采样点数量，提高性能）
                segment = []
                dash_seg_length = dash_end - dash_start
                # 最小化采样点：每8像素一个采样点，最少2个点，最多4个点
                num_samples = max(2, min(4, int(dash_seg_length / 8.0)))
                
                for j in range(num_samples + 1):
                    t = j / num_samples if num_samples > 0 else 0
                    dist = dash_start + (dash_end - dash_start) * t
                    point = get_point_at_distance(points, cumulative_distances, dist)
                    if point:
                        segment.append(point)
                
                if len(segment) >= 2:
                    dashed_segments.append(segment)
            
            # 移动到下一个位置（确保有最小步进，防止死循环）
            current_pos = max(dash_end, current_pos + 1.0)
            if current_pos >= path_length:
                break
        else:
            # 跳过间隙（确保有最小步进）
            gap_end = min(current_pos + (pattern_length - pattern_pos), path_length)
            current_pos = max(gap_end, current_pos + 1.0)
            if current_pos >= path_length:
                break
    
    return dashed_segments

def create_dashed_line_segments(points, dash_length=10.0, gap_length=5.0, time_offset=0.0):
    """
    将连续的点列表转换为虚线段的列表
    points: 连续的点列表 [(x1, y1), (x2, y2), ...]
    dash_length: 每段虚线的长度（像素）
    gap_length: 间隙长度（像素）
    time_offset: 时间偏移，用于动画效果（像素单位）
    返回: 虚线段的列表，每个元素是一个点列表
    """
    if len(points) < 2:
        return []
    
    # 计算路径上每个点的累积距离
    cumulative_distances = [0.0]
    total_length = 0.0
    
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        seg_len = hypot(dx, dy)
        total_length += seg_len
        cumulative_distances.append(total_length)
    
    if total_length == 0:
        return []
    
    # 虚线模式：dash + gap 循环
    pattern_length = dash_length + gap_length
    
    # 应用时间偏移（循环，确保偏移在有效范围内）
    offset = time_offset % pattern_length
    
    # 沿着路径生成均匀的虚线段
    dashed_segments = []
    current_pos = -offset  # 从负偏移开始，这样动画会向前移动
    
    # 沿着路径生成虚线段
    while current_pos < total_length:
        # 计算当前模式位置（0 到 pattern_length）
        pattern_pos = (current_pos + offset + pattern_length) % pattern_length
        
        # 判断是否在虚线部分
        if pattern_pos < dash_length:
            # 计算虚线段的起始和结束位置
            dash_start = current_pos
            dash_end = min(current_pos + (dash_length - pattern_pos), total_length)
            
            # 如果虚线段长度足够，生成它
            if dash_end > dash_start:
                segment_points = []
                # 沿着虚线段的路径采样点
                num_samples = max(2, int((dash_end - dash_start) / 2.0))  # 每2像素一个采样点
                for i in range(num_samples + 1):
                    t = i / num_samples if num_samples > 0 else 0
                    dist = dash_start + (dash_end - dash_start) * t
                    point = get_point_at_distance(points, cumulative_distances, dist)
                    if point:
                        segment_points.append(point)
                
                if len(segment_points) >= 2:
                    dashed_segments.append(segment_points)
            
            # 移动到虚线段的结束位置
            current_pos = dash_end
        else:
            # 跳过间隙部分
            gap_start = current_pos
            gap_end = min(current_pos + (pattern_length - pattern_pos), total_length)
            current_pos = gap_end
    
    return dashed_segments

def get_point_at_path_distance(points, target_distance):
    """
    在路径上找到指定距离处的点（直接计算，不需要预计算累积距离）
    points: 点列表
    target_distance: 目标距离
    返回: (x, y) 坐标元组，如果超出范围返回None
    """
    if len(points) < 2:
        return None
    
    if target_distance < 0:
        return points[0]
    
    # 沿着路径累加距离，找到目标距离所在的线段
    current_dist = 0.0
    for i in range(len(points) - 1):
        p1 = points[i]
        p2 = points[i + 1]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        seg_len = hypot(dx, dy)
        
        if current_dist + seg_len >= target_distance:
            # 在这个线段内
            if seg_len < 1e-6:
                return p1
            
            t = (target_distance - current_dist) / seg_len
            x = p1[0] + (p2[0] - p1[0]) * t
            y = p1[1] + (p2[1] - p1[1]) * t
            return (x, y)
        
        current_dist += seg_len
    
    # 超出范围，返回最后一个点
    return points[-1]

def get_point_at_distance(points, cumulative_distances, target_distance):
    """
    在路径上找到指定距离处的点
    points: 点列表
    cumulative_distances: 累积距离列表
    target_distance: 目标距离
    返回: (x, y) 坐标元组，如果超出范围返回None
    """
    if target_distance < 0:
        target_distance = 0
    if target_distance >= cumulative_distances[-1]:
        return points[-1] if points else None
    
    # 找到目标距离所在的线段
    for i in range(len(cumulative_distances) - 1):
        dist_start = cumulative_distances[i]
        dist_end = cumulative_distances[i + 1]
        
        if dist_start <= target_distance <= dist_end:
            # 在这个线段内插值
            if abs(dist_end - dist_start) < 1e-6:
                return points[i]
            
            t = (target_distance - dist_start) / (dist_end - dist_start)
            p1 = points[i]
            p2 = points[i + 1]
            x = p1[0] + (p2[0] - p1[0]) * t
            y = p1[1] + (p2[1] - p1[1]) * t
            return (x, y)
    
    # 如果没找到，返回最后一个点
    return points[-1] if points else None

def node_bounds(node, ui_scale):
    """
    计算节点边界框的 View2D 坐标（优化版本，用于减少锯齿）
    """
    scale = bpy.context.preferences.system.ui_scale
    
    di_x = node.dimensions.x
    di_y = node.dimensions.y
    w_cloud_magic_value = 9
    rara_magic_value = 5
    
    if hasattr(node, "location_absolute"):
        node_x = node.location_absolute.x
        node_y = node.location_absolute.y
    else:
        # 低版本没有location_absolute，使用兼容算法
        node_x = node.location.x
        node_y = node.location.y
        node_p = node.parent
        while node_p:
            node_x += node_p.location.x
            node_y += node_p.location.y
            node_p = node_p.parent
    
    # 当节点类型为中转点时，使用特殊输出
    if node.type == "REROUTE":
        x_min = (node_x - rara_magic_value) * scale
        x_max = (node_x + rara_magic_value) * scale
        y_min = (node_y - rara_magic_value) * scale
        y_max = (node_y + rara_magic_value) * scale
        return x_min, x_max, y_min, y_max
    
    x_min = node_x * scale
    x_max = x_min + di_x
    
    if node.hide and node.type not in {"REROUTE", "FRAME"}:
        y_min = node_y * scale - w_cloud_magic_value * scale - di_y / 2
        y_max = node_y * scale - w_cloud_magic_value * scale + di_y / 2
    else:
        y_min = node_y * scale
        y_max = y_min - di_y
    
    return x_min, x_max, y_min, y_max


def get_rounded_rect_path(node, v2d, radius=4.0, resolution=12, thickness=0.0):
    ui_scale = bpy.context.preferences.system.ui_scale
    x_min, x_max, y_min, y_max = node_bounds(node, ui_scale)
    
    # 转换到 Region 像素坐标
    p_bl = v2d.view_to_region(x_min, y_min, clip=False)
    p_tr = v2d.view_to_region(x_max, y_max, clip=False)
    
    if not p_bl or not p_tr:
        return None
        
    rmin_x = min(p_bl[0], p_tr[0])
    rmax_x = max(p_bl[0], p_tr[0])
    rmin_y = min(p_bl[1], p_tr[1])
    rmax_y = max(p_bl[1], p_tr[1])
    
    # 向外扩张 (线宽的一半)
    offset = thickness * 0.5
    rmin_x -= offset
    rmax_x += offset
    rmin_y -= offset
    rmax_y += offset
    
    w = rmax_x - rmin_x
    h = rmax_y - rmin_y
    
    eff_radius = min(radius, w/2, h/2)
    
    path = []
    
    def add_arc(cx, cy, start_ang, end_ang):
        for i in range(resolution + 1):
            t = i / resolution
            ang = start_ang + (end_ang - start_ang) * t
            path.append((cx + eff_radius * cos(ang), cy + eff_radius * sin(ang)))

    path.append(((rmin_x + rmax_x)/2.0, rmin_y))
    add_arc(rmax_x - eff_radius, rmin_y + eff_radius, 1.5 * pi, 2.0 * pi)
    add_arc(rmax_x - eff_radius, rmax_y - eff_radius, 0.0, 0.5 * pi)
    add_arc(rmin_x + eff_radius, rmax_y - eff_radius, 0.5 * pi, 1.0 * pi)
    add_arc(rmin_x + eff_radius, rmin_y + eff_radius, 1.0 * pi, 1.5 * pi)
    path.append(((rmin_x + rmax_x)/2.0, rmin_y))
    
    return path

    
draw_handler = None
last_time = 0
_SHADER_CACHE = {}

# 用于存储固定的流状态
_locked_flow_data = {
    'links': set(),
    'nodes': set(),
    'is_locked': False
}

def get_shader(name):
    if name in _SHADER_CACHE:
        return _SHADER_CACHE[name]
    
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant('MAT4', 'ModelViewProjectionMatrix')
    
    # --- 公共 Vertex Source ---
    vert_src = '''
        void main() {
            gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);
            v_uv = uv;
        }
    '''

    if name == 'RAINBOW':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_rainbow_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('FLOAT', 'u_time')
        info.push_constant('FLOAT', 'u_alpha')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        
        info.fragment_source('''
            vec3 hsv2rgb(vec3 c) {
                vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
                vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
                return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
            }
            void main() {
                float v_progress = v_uv.x;
                float v_side = v_uv.y;
                float t = u_time * 0.5;
                float flow_phase = t * 3.0 - v_progress * 6.2831853; 
                float hue = fract(flow_phase / 6.2831853);
                
                float saturation = 0.9;
                float value = 0.95;
                float pulse_t = u_time * 1.0;
                float pulse_center = fract(pulse_t);
                float raw_dist = fract(pulse_center - v_progress);
                float pulse = 0.0;
                if (raw_dist > 0.5) {
                     float front_dist = 1.0 - raw_dist;
                     pulse = exp(-(front_dist * front_dist) / 0.0002);
                } else {
                     pulse = exp(-(raw_dist * raw_dist) / 0.08);
                }
                float boost = 1.0 + 0.6 * pulse;
                float sat_damp = 1.0 - 0.2 * pulse;
                
                vec3 rgb = hsv2rgb(vec3(hue, saturation * sat_damp, min(1.0, value * boost)));
                float dist = abs(v_side);
                float alpha_edge = 1.0 - smoothstep(0.85, 1.0, dist);
                fragColor = vec4(rgb, u_alpha * alpha_edge);
            }
        ''')

    # --- 动态颜色渐变 Shader (支持最多10个颜色) ---
    elif name == 'GRADIENT':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_gradient_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('FLOAT', 'u_time')
        info.push_constant('FLOAT', 'u_alpha')
        info.push_constant('INT', 'u_color_count')  # 实际使用的颜色数量
        
        # 接收最多10个颜色
        for i in range(10):
            info.push_constant('VEC4', f'color{i+1}')
        
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        
        info.fragment_source('''
            void main() {
                float v_progress = v_uv.x;
                float v_side = v_uv.y;
                float t = u_time * 0.5;
                
                // 构建颜色数组（包含RGB和Alpha）
                vec3 colors[10];
                float alphas[10];
                colors[0] = color1.rgb; alphas[0] = color1.a;
                colors[1] = color2.rgb; alphas[1] = color2.a;
                colors[2] = color3.rgb; alphas[2] = color3.a;
                colors[3] = color4.rgb; alphas[3] = color4.a;
                colors[4] = color5.rgb; alphas[4] = color5.a;
                colors[5] = color6.rgb; alphas[5] = color6.a;
                colors[6] = color7.rgb; alphas[6] = color7.a;
                colors[7] = color8.rgb; alphas[7] = color8.a;
                colors[8] = color9.rgb; alphas[8] = color9.a;
                colors[9] = color10.rgb; alphas[9] = color10.a;
                
                // 计算流动相位
                float flow_speed = 0.5;
                float phase = (t * flow_speed) - v_progress;
                phase = fract(phase);
                
                // 动态颜色混合
                float n = float(u_color_count);
                float pos = phase * n;
                int index = int(floor(pos));
                float f = fract(pos);
                
                // 确保索引在有效范围内
                index = min(index, u_color_count - 1);
                int next_index = (index + 1) % u_color_count;
                
                // 混合RGB
                vec3 final_base_rgb = mix(colors[index], colors[next_index], f);
                
                // 混合Alpha（每个颜色的独立透明度）
                float final_base_alpha = mix(alphas[index], alphas[next_index], f);
                
                // 脉冲效果
                float pulse_t = u_time * 1.0;
                float pulse_center = fract(pulse_t);
                float raw_dist = fract(pulse_center - v_progress);
                float pulse = 0.0;
                if (raw_dist > 0.5) {
                     float front_dist = 1.0 - raw_dist;
                     pulse = exp(-(front_dist * front_dist) / 0.0002);
                } else {
                     pulse = exp(-(raw_dist * raw_dist) / 0.08);
                }
                float boost = 1.0 + 0.3 * pulse;
                
                vec3 final_rgb = min(vec3(1.0), final_base_rgb * boost);
                
                // 边缘alpha衰减
                float dist = abs(v_side);
                float alpha_edge = 1.0 - smoothstep(0.85, 1.0, dist);
                
                // 最终alpha = 颜色自身alpha * 全局透明度 * 边缘衰减
                float final_alpha = final_base_alpha * u_alpha * alpha_edge;
                fragColor = vec4(final_rgb, final_alpha);
            }
        ''')

    elif name == 'SMOOTH_COLOR':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_smooth_color_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('VEC4', 'color')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        info.fragment_source('''
            void main() {
                float dist = abs(v_uv.y);
                float alpha_edge = 1.0 - smoothstep(0.85, 1.0, dist);
                fragColor = vec4(color.rgb, color.a * alpha_edge);
            }
        ''')
    
    elif name == 'SDF_CIRCLE':
        iface = gpu.types.GPUStageInterfaceInfo("node_wrangler_sdf_circle_iface")
        iface.smooth('VEC2', 'v_uv')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(iface)
        info.push_constant('VEC4', 'color')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.vertex_source(vert_src)
        info.fragment_source('''
            void main() {
                float dist = length(v_uv);
                float delta = 1.5 * fwidth(dist);
                float alpha = 1.0 - smoothstep(1.0 - delta, 1.0, dist);
                fragColor = vec4(color.rgb, color.a * alpha);
            }
        ''')

    shader = gpu.shader.create_from_info(info)
    _SHADER_CACHE[name] = shader
    return shader


def _view2d_zoom_factor(v2d):
    try:
        x0, y0 = v2d.region_to_view(0.0, 0.0)
        x1, y1 = v2d.region_to_view(1.0, 0.0)
        dx = abs(x1 - x0)
        if dx <= 1e-8:
            return 1.0
        return 1.0 / dx
    except Exception:
        return 1.0

def get_curving_factor():
    try:
        curving = bpy.context.preferences.themes[0].node_editor.noodle_curving
        return curving / 10.0
    except Exception:
        return 0.0

def _rainbow_rgba(step_t, time_sec):
    t = time_sec * 0.5
    pulse_t = time_sec * 1.0
    pulse_center = pulse_t % 1.0
    flow_phase = t * 3.0 - step_t * 4.0
    hue = (flow_phase / (2 * pi)) % 1.0
    saturation = 0.65
    value = 0.95
    raw_dist = (pulse_center - step_t) % 1.0
    if raw_dist > 0.5:
        front_dist = 1.0 - raw_dist
        pulse = exp(-(front_dist * front_dist) / (2.0 * 0.01 * 0.01))
    else:
        pulse = exp(-(raw_dist * raw_dist) / (2.0 * 0.2 * 0.2))
    boost = 1.0 + 3.0 * pulse
    sat_damp = 1.0 - 0.95 * pulse
    r, g, b = colorsys.hsv_to_rgb(hue, saturation * sat_damp, min(1.0, value * boost))
    return (r, g, b, 1.0)

def get_native_link_points(link, v2d, curv, zoom_factor=1.0):
    """获取连线点列表，根据缩放级别优化采样点数"""
    fs, ts = link.from_socket, link.to_socket
    try:
        if not (fs.enabled and ts.enabled):
            return None
        from_node = link.from_node
        to_node = link.to_node
        from_idx = _get_socket_index_cached({}, from_node, fs, True) or 0
        to_idx = _get_socket_index_cached({}, to_node, ts, False) or 0
        x1, y1 = get_socket_loc(from_node, True, from_idx)
        x2, y2 = get_socket_loc(to_node, False, to_idx)
    except Exception:
        return None

    y_off = 0
    y1 += y_off
    y2 += y_off

    v2r = v2d.view_to_region
    
    # 性能优化：根据缩放级别动态调整采样点数
    # 缩放级别越低（视图越远），使用越少的采样点
    if zoom_factor > 0.5:
        seg = 24  # 高缩放级别：详细采样
    elif zoom_factor > 0.2:
        seg = 16  # 中等缩放级别
    elif zoom_factor > 0.1:
        seg = 12  # 低缩放级别
    else:
        seg = 8   # 极低缩放级别：最少采样

    if curv <= 0.001:
        p0 = (x1, y1)
        p3 = (x2, y2)
        p1 = (x1 + (x2 - x1) * (1.0 / 3.0), y1 + (y2 - y1) * (1.0 / 3.0))
        p2 = (x1 + (x2 - x1) * (2.0 / 3.0), y1 + (y2 - y1) * (2.0 / 3.0))
        pts = []
        for i in range(seg + 1):
            t = i / seg
            x = x1 + t * (x2 - x1)
            y = y1 + t * (y2 - y1)
            pts.append(v2r(x, y, clip=False))
        return pts

    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    if dx != 0:
        slope = dy / dx
    else:
        slope = float('inf')

    curving_factor = curv * 10
    clamp_factor = min(1.0, slope * (4.5 - 0.25 * curving_factor))
    handle_offset = curving_factor * 0.1 * dx * clamp_factor

    p0 = (x1, y1)
    p3 = (x2, y2)
    p1 = (x1 + handle_offset, y1)
    p2 = (x2 - handle_offset, y2)
    
    pts = []
    for i in range(seg + 1):
        t = i / seg
        inv_t = 1 - t
        inv_t2 = inv_t * inv_t
        inv_t3 = inv_t2 * inv_t
        t2 = t * t
        t3 = t2 * t
        x = (inv_t3 * p0[0] + 3 * inv_t2 * t * p1[0] + 3 * inv_t * t2 * p2[0] + t3 * p3[0])
        y = (inv_t3 * p0[1] + 3 * inv_t2 * t * p1[1] + 3 * inv_t * t2 * p2[1] + t3 * p3[1])
        pts.append(v2r(x, y, clip=False))
    return pts

def _is_link_visible(region, pts, margin=50):
    """检查连线是否在视口中可见（视口裁剪）"""
    if not pts or len(pts) < 2:
        return False
    
    # 如果无法获取区域，假设可见
    if not region:
        return True
    
    view_min_x = 0
    view_min_y = 0
    view_max_x = region.width if hasattr(region, 'width') else 1920  # 默认值
    view_max_y = region.height if hasattr(region, 'height') else 1080  # 默认值
    
    # 检查是否有任何点在视口内（带边距）
    for pt in pts:
        if pt and len(pt) >= 2:
            x, y = pt[0], pt[1]
            if (view_min_x - margin <= x <= view_max_x + margin and 
                view_min_y - margin <= y <= view_max_y + margin):
                return True
    
    return False

def dpi_fac():
    prefs = bpy.context.preferences.system
    return prefs.dpi / 72

def abs_node_location(node):
    abs_location = node.location
    if node.parent is None:
        return abs_location
    return abs_location + abs_node_location(node.parent)

def get_socket_loc(node, is_output, index):
    try:
        sockets = node.outputs if is_output else node.inputs
        if index < len(sockets):
            socket = sockets[index]
            offset = 520 
            if bpy.app.version >= (5, 1, 0): 
                offset = 456
            vec = Vector((c_float * 2).from_address(c_void_p.from_address(socket.as_pointer() + offset).value + 24))
            return vec.x, vec.y
    except Exception:
        pass

    if node.type == 'REROUTE':
        nlocx, nlocy = abs_node_location(node)
        fac = dpi_fac()
        return (nlocx + 1) * fac, (nlocy + 1) * fac

    fac = dpi_fac()
    nlocx, nlocy = abs_node_location(node)
    base_x = (nlocx + 1) * fac
    base_y = (nlocy + 1) * fac

    if is_output:
        x = base_x + node.dimensions.x
    else:
        x = base_x

    header_height = 32.0 * fac
    socket_height = 21.0 * fac
    y = base_y - header_height

    sockets = node.outputs if is_output else node.inputs
    enabled_sockets = [s for s in sockets if s.enabled]
    try:
        real_idx = enabled_sockets.index(sockets[index])
    except (ValueError, IndexError):
        real_idx = 0

    y = y - (real_idx * socket_height) - (socket_height * 0.5)
    if is_output:
        y += 0.5 * fac
    else:
        y -= 0.5 * fac
    return x, y

def _get_socket_index_cached(cache, node, socket, is_output):
    key = (node.as_pointer(), bool(is_output))
    socket_map = cache.get(key)
    if socket_map is None:
        sockets = node.outputs if is_output else node.inputs
        socket_map = {s.as_pointer(): i for i, s in enumerate(sockets)}
        cache[key] = socket_map
    return socket_map.get(socket.as_pointer())

def _get_line_strip_geometry(vertices, width):
    if len(vertices) < 2:
        return [], []
    pos_data = []
    uv_data = []
    half_w = width * 0.5
    count = len(vertices)
    verts = [Vector(v) for v in vertices]
    
    distances = [0.0]
    total_length = 0.0
    for i in range(count - 1):
        dist = (verts[i+1] - verts[i]).length
        total_length += dist
        distances.append(total_length)
    
    for i in range(count):
        curr_p = verts[i]
        if i == 0:
            tangent = (verts[1] - curr_p).normalized()
        elif i == count - 1:
            tangent = (curr_p - verts[i-1]).normalized()
        else:
            t1 = (curr_p - verts[i-1]).normalized()
            t2 = (verts[i+1] - curr_p).normalized()
            tangent = (t1 + t2).normalized()
        
        normal = Vector((-tangent.y, tangent.x))
        p0 = curr_p + normal * half_w
        p1 = curr_p - normal * half_w
        
        pos_data.append((p0.x, p0.y))
        pos_data.append((p1.x, p1.y))
        
        if total_length > 0:
            u = distances[i] / total_length
        else:
            u = 0.0
        uv_data.append((u, 1.0))
        uv_data.append((u, -1.0))
    return pos_data, uv_data

def draw_batch_lines(all_lines_data, shader_name, width, colors=None, time_sec=0.0, overall_opacity=1.0):
    if not all_lines_data:
        return

    shader = get_shader(shader_name)
    if not shader:
        return

    all_pos = []
    all_uv = []
    
    for vertices in all_lines_data:
        if not vertices or len(vertices) < 2:
            continue
        pos, uv = _get_line_strip_geometry(vertices, width)
        if not pos:
            continue
        if all_pos:
            all_pos.append(all_pos[-1])
            all_uv.append(all_uv[-1])
            all_pos.append(pos[0])
            all_uv.append(uv[0])
        all_pos.extend(pos)
        all_uv.extend(uv)
        
    if not all_pos:
        return

    shader.bind()
    if shader_name == 'RAINBOW':
        shader.uniform_float("u_time", time_sec % 1000.0)
        shader.uniform_float("u_alpha", overall_opacity)
    elif shader_name == 'GRADIENT':
        shader.uniform_float("u_time", time_sec % 1000.0)
        shader.uniform_float("u_alpha", overall_opacity)
        
        # 获取颜色数量和颜色列表
        color_count = len(colors) if colors else 0
        if color_count < 2:
            # 如果颜色不足，使用默认值
            color_count = 5
            colors = [(0.0, 0.5, 1.0, 1.0), (0.0, 1.0, 0.8, 1.0), (1.0, 1.0, 0.0, 1.0),
                      (1.0, 0.5, 0.0, 1.0), (1.0, 0.0, 0.5, 1.0)]
        
        # 应用全局透明度到每个颜色的alpha值
        # 每个颜色已经有自己的alpha值，现在再乘以全局透明度
        colors = [(c[0], c[1], c[2], (c[3] * overall_opacity) if len(c) > 3 else overall_opacity) for c in colors]
        
        # 设置颜色数量
        shader.uniform_int("u_color_count", color_count)
        
        # 传入最多10个颜色（不足的用最后一个颜色填充）
        color_list = list(colors[:color_count])
        while len(color_list) < 10:
            color_list.append(color_list[-1] if color_list else (1.0, 1.0, 1.0, overall_opacity))
        
        for i in range(10):
            shader.uniform_float(f"color{i+1}", color_list[i])
    elif shader_name == 'SMOOTH_COLOR':
        if colors and len(colors) >= 1:
            # 应用透明度
            color = colors[0]
            if len(color) >= 4:
                color = (color[0], color[1], color[2], color[3] * overall_opacity)
            else:
                color = (*color[:3], overall_opacity)
            shader.uniform_float("color", color)
    
    batch = batch_for_shader(shader, 'TRI_STRIP', {"pos": all_pos, "uv": all_uv})
    batch.draw(shader)

def draw_batch_circles(batch_circles, radius, color, overall_opacity=1.0):
    if not batch_circles or radius <= 0:
        return
    shader = get_shader('SDF_CIRCLE')
    if not shader:
        return
    adjusted_radius = radius * 1.0
    size = adjusted_radius + 2.0
    uv_scale = size / adjusted_radius if adjusted_radius > 0 else 1.0
    all_pos = []
    all_uv = []
    o0 = (-size, -size)
    o1 = ( size, -size)
    o2 = (-size,  size)
    o3 = ( size,  size)
    u0 = (-uv_scale, -uv_scale)
    u1 = ( uv_scale, -uv_scale)
    u2 = (-uv_scale,  uv_scale)
    u3 = ( uv_scale,  uv_scale)
    
    for (cx, cy) in batch_circles:
        p0 = (cx + o0[0], cy + o0[1])
        p1 = (cx + o1[0], cy + o1[1])
        p2 = (cx + o2[0], cy + o2[1])
        p3 = (cx + o3[0], cy + o3[1])
        all_pos.extend([p0, p2, p1])
        all_uv.extend([u0, u2, u1])
        all_pos.extend([p1, p2, p3])
        all_uv.extend([u1, u2, u3])
    
    # 应用透明度到颜色
    if len(color) >= 4:
        adjusted_color = (color[0], color[1], color[2], color[3] * overall_opacity)
    else:
        adjusted_color = (*color[:3], overall_opacity)
        
    batch = batch_for_shader(shader, 'TRIS', {"pos": all_pos, "uv": all_uv})
    shader.bind()
    shader.uniform_float("color", adjusted_color)
    gpu.state.blend_set('ALPHA')
    batch.draw(shader)
    gpu.state.blend_set('NONE')

def get_panel_settings():
    try:
        scene = bpy.context.scene
        if hasattr(scene, 'colorful_connections_settings'):
            settings = scene.colorful_connections_settings
            # 辅助函数：转为RGBA（支持从PropertyGroup读取alpha）
            def to_rgba(c):
                alpha = 1.0
                if hasattr(c, 'alpha'):
                    alpha = float(c.alpha)
                
                if isinstance(c, (list, tuple)):
                    if len(c) >= 4:
                        return (float(c[0]), float(c[1]), float(c[2]), float(c[3]))
                    elif len(c) >= 3:
                        return (float(c[0]), float(c[1]), float(c[2]), alpha)
                # 如果是 PropertyGroup 的属性
                if hasattr(c, 'color'):
                    col = c.color
                    return (float(col[0]), float(col[1]), float(col[2]), alpha)
                return (1.0, 1.0, 1.0, alpha)
            
            # 从新的颜色集合中读取（Constant类型）
            gradient_colors = []
            color_count = getattr(settings, 'gradient_color_count', 5)
            colors = getattr(settings, 'gradient_colors', None)
            
            if colors and len(colors) > 0:
                # 只读取实际使用的颜色数量
                for i in range(min(color_count, len(colors))):
                    gradient_colors.append(to_rgba(colors[i]))
            
            # 如果颜色不足，使用默认值
            if len(gradient_colors) < 2:
                gradient_colors = [
                    (0.0, 0.5, 1.0, 1.0),
                    (0.0, 1.0, 0.8, 1.0),
                    (1.0, 1.0, 0.0, 1.0),
                    (1.0, 0.5, 0.0, 1.0),
                    (1.0, 0.0, 0.5, 1.0)
                ]
            
            # 从新的颜色集合中读取（Field类型）
            field_gradient_colors = []
            field_color_count = getattr(settings, 'field_gradient_color_count', 5)
            field_colors = getattr(settings, 'field_gradient_colors', None)
            
            if field_colors and len(field_colors) > 0:
                # 只读取实际使用的颜色数量
                for i in range(min(field_color_count, len(field_colors))):
                    field_gradient_colors.append(to_rgba(field_colors[i]))
            
            # 如果Field颜色不足，使用默认值（紫色系）
            if len(field_gradient_colors) < 2:
                field_gradient_colors = [
                    (0.8, 0.2, 1.0, 1.0),
                    (0.6, 0.4, 1.0, 1.0),
                    (1.0, 0.4, 0.8, 1.0),
                    (0.9, 0.6, 1.0, 1.0),
                    (0.7, 0.3, 0.9, 1.0)
                ]
            
            # 读取底层背景颜色（新格式：RGB和Alpha分开，兼容旧格式）
            backing_color_rgba = (0.0, 0.0, 0.0, 0.55)  # 默认值
            try:
                # 优先尝试新格式（RGB和Alpha分开）
                # 直接尝试读取，不使用hasattr，因为PropertyGroup的属性应该总是存在
                try:
                    rgb = settings.backing_color_rgb
                    alpha = settings.backing_color_alpha
                    # 处理Blender的Vector类型，转换为tuple
                    if hasattr(rgb, '__len__') and len(rgb) >= 3:
                        backing_color_rgba = (float(rgb[0]), float(rgb[1]), float(rgb[2]), float(alpha))
                    elif isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                        backing_color_rgba = (float(rgb[0]), float(rgb[1]), float(rgb[2]), float(alpha))
                except AttributeError:
                    # 如果新属性不存在，尝试旧格式（RGBA向量）
                    try:
                        backing_color = settings.backing_color
                        if hasattr(backing_color, '__len__') and len(backing_color) >= 4:
                            backing_color_rgba = (float(backing_color[0]), float(backing_color[1]), float(backing_color[2]), float(backing_color[3]))
                        elif isinstance(backing_color, (list, tuple)) and len(backing_color) >= 4:
                            backing_color_rgba = (float(backing_color[0]), float(backing_color[1]), float(backing_color[2]), float(backing_color[3]))
                    except AttributeError:
                        pass  # 使用默认值
            except Exception as e:
                print(f"读取底层背景颜色时出错: {e}")
                import traceback
                traceback.print_exc()
                backing_color_rgba = (0.0, 0.0, 0.0, 0.55)
            
            return {
                'animation_speed': settings.animation_speed,
                'line_thickness': settings.line_thickness,
                'node_border_thickness': settings.node_border_thickness,
                'enable_colorful_connections': settings.enable_colorful_connections,
                'connection_color_type': settings.connection_color_type,
                'trace_mode': getattr(settings, 'trace_mode', 'ALL_SELECTED'),
                'flow_direction': getattr(settings, 'flow_direction', 'DOWNSTREAM'),
                'lock_flow': getattr(settings, 'lock_flow', False),
                'enable_type_based_colors': getattr(settings, 'enable_type_based_colors', False),
                'overall_opacity': getattr(settings, 'overall_opacity', 1.0),
                'backing_color': backing_color_rgba,
                'gradient_colors': gradient_colors,
                'field_gradient_colors': field_gradient_colors
            }
        else:
            # 默认值
            return {
                'animation_speed': 1.0,
                'line_thickness': 2.0,
                'node_border_thickness': 3.0,
                'enable_colorful_connections': True,
                'connection_color_type': 'CUSTOM',
                'trace_mode': 'ALL_SELECTED',
                'flow_direction': 'DOWNSTREAM',
                'lock_flow': False,
                'enable_type_based_colors': False,
                'overall_opacity': 1.0,
                'backing_color': (0.0, 0.0, 0.0, 0.55),  # 默认值，格式：(R, G, B, A)
                'gradient_colors': [
                    (0.0, 0.5, 1.0, 1.0),
                    (0.0, 1.0, 0.8, 1.0),
                    (1.0, 1.0, 0.0, 1.0),
                    (1.0, 0.5, 0.0, 1.0),
                    (1.0, 0.0, 0.5, 1.0)
                ],
                'field_gradient_colors': [
                    (0.8, 0.2, 1.0, 1.0),
                    (0.6, 0.4, 1.0, 1.0),
                    (1.0, 0.4, 0.8, 1.0),
                    (0.9, 0.6, 1.0, 1.0),
                    (0.7, 0.3, 0.9, 1.0)
                ]
            }
    except Exception as e:
        # 调试用
        # print(f"Error in get_panel_settings: {e}")
        return {
            'animation_speed': 1.0,
            'line_thickness': 2.0,
            'node_border_thickness': 3.0,
            'enable_colorful_connections': True,
            'connection_color_type': 'CUSTOM',
            'trace_mode': 'ALL_SELECTED',
            'flow_direction': 'DOWNSTREAM',
            'lock_flow': False,
            'enable_type_based_colors': False,
            'gradient_colors': [
                (0.0, 0.5, 1.0, 1.0),
                (0.0, 1.0, 0.8, 1.0),
                (1.0, 1.0, 0.0, 1.0),
                (1.0, 0.5, 0.0, 1.0),
                (1.0, 0.0, 0.5, 1.0)
            ]
        }

def extend_links_through_reroutes(links_to_draw, start_node, direction='both', visited_nodes=None):
    if visited_nodes is None:
        visited_nodes = set()
    if start_node in visited_nodes or start_node.type != 'REROUTE':
        return
    visited_nodes.add(start_node)
    
    if direction in ['forward', 'both']:
        for output in start_node.outputs:
            if output.enabled:
                for link in output.links:
                    links_to_draw.add(link)
                    next_node = link.to_node
                    if next_node and next_node.type == 'REROUTE':
                        extend_links_through_reroutes(links_to_draw, next_node, 'forward', visited_nodes)
    
    if direction in ['backward', 'both']:
        for input_socket in start_node.inputs:
            if input_socket.enabled:
                for link in input_socket.links:
                    links_to_draw.add(link)
                    prev_node = link.from_node
                    if prev_node and prev_node.type == 'REROUTE':
                        extend_links_through_reroutes(links_to_draw, prev_node, 'backward', visited_nodes)

def trace_all_reroute_links(selected_node, links_to_draw, visited_nodes=None):
    """旧模式：仅收集选定节点的直接连线 + Reroute 延伸"""
    if visited_nodes is None:
        visited_nodes = set()
    if selected_node in visited_nodes:
        return
    visited_nodes.add(selected_node)
    
    for output in selected_node.outputs:
        if output.enabled:
            for link in output.links:
                links_to_draw.add(link)
                if link.to_node and link.to_node.type == 'REROUTE':
                    extend_links_through_reroutes(links_to_draw, link.to_node, 'forward')
    
    for input_socket in selected_node.inputs:
        if input_socket.enabled:
            for link in input_socket.links:
                links_to_draw.add(link)
                if link.from_node and link.from_node.type == 'REROUTE':
                    extend_links_through_reroutes(links_to_draw, link.from_node, 'backward')

# --- 新的逻辑：真正的深度递归遍历 ---
def traverse_recursive(current_node, direction, collected_links, visited_nodes):
    """
    深度优先搜索，遍历整个节点树的数据流
    
    direction: 'forward' (downstream) or 'backward' (upstream)
    collected_links: 收集到的连线集合
    visited_nodes: 已访问的节点集合（用于防止循环）
    """
    if current_node in visited_nodes:
        return
    visited_nodes.add(current_node)

    if direction == 'forward':
        # 向下查找：Output -> Links -> Next Node
        for output in current_node.outputs:
            if output.enabled:
                for link in output.links:
                    if link not in collected_links:
                        collected_links.add(link)
                        if link.to_node:
                            traverse_recursive(link.to_node, 'forward', collected_links, visited_nodes)
    
    elif direction == 'backward':
        # 向上查找：Input -> Links -> Previous Node
        for input_socket in current_node.inputs:
            if input_socket.enabled:
                for link in input_socket.links:
                    if link not in collected_links:
                        collected_links.add(link)
                        if link.from_node:
                            traverse_recursive(link.from_node, 'backward', collected_links, visited_nodes)

def draw_colorful_connections():
    context = bpy.context
    if context.space_data is None or context.space_data.type != 'NODE_EDITOR':
        return
    tree = context.space_data.node_tree
    if not tree:
        return
    
    settings = get_panel_settings()
    if not settings.get('enable_colorful_connections', True):
        return

    links_to_draw = set()
    nodes_to_outline = set()  # 用来画边框的节点

    trace_mode = settings.get('trace_mode', 'ALL_SELECTED')

    # --- 逻辑分支 ---
    if trace_mode == 'ALL_SELECTED':
        # 原有逻辑：所有选中节点都发光
        selected_nodes = context.selected_nodes
        if not selected_nodes:
            return
        nodes_to_outline = set(selected_nodes)  # 边框只画选中的
        for node in selected_nodes:
            trace_all_reroute_links(node, links_to_draw)
            
    elif trace_mode == 'ACTIVE_FLOW':
        # 新逻辑：仅追踪活动节点的数据流
        lock_flow = settings.get('lock_flow', False)
        
        # 如果取消了锁定，清除保存的状态
        if not lock_flow and _locked_flow_data['is_locked']:
            _locked_flow_data['is_locked'] = False
            _locked_flow_data['links'].clear()
            _locked_flow_data['nodes'].clear()
        
        # 检查是否需要使用固定的流
        if lock_flow and _locked_flow_data['is_locked']:
            # 使用固定的流数据
            links_to_draw.update(_locked_flow_data['links'])
            nodes_to_outline.update(_locked_flow_data['nodes'])
        else:
            # 重新计算流
            active_node = context.active_node
            if not active_node:
                return
            
            # 边框始终画活动节点
            nodes_to_outline.add(active_node)
            
            direction = settings.get('flow_direction', 'DOWNSTREAM')
            
            if direction == 'BOTH':
                # 双向模式：使用两个独立的 visited_nodes 集合，避免相互干扰
                visited_nodes_forward = set()
                visited_nodes_backward = set()
                
                # 向下遍历
                traverse_recursive(active_node, 'forward', links_to_draw, visited_nodes_forward)
                # 向上遍历
                traverse_recursive(active_node, 'backward', links_to_draw, visited_nodes_backward)
            else:
                # 单向模式：使用一个集合即可
                visited_nodes_trace = set()
                if direction == 'DOWNSTREAM':
                    traverse_recursive(active_node, 'forward', links_to_draw, visited_nodes_trace)
                elif direction == 'UPSTREAM':
                    traverse_recursive(active_node, 'backward', links_to_draw, visited_nodes_trace)
            
            # 如果启用了锁定，保存当前的流状态
            if lock_flow:
                _locked_flow_data['links'] = links_to_draw.copy()
                _locked_flow_data['nodes'] = nodes_to_outline.copy()
                _locked_flow_data['is_locked'] = True

    if not links_to_draw and not nodes_to_outline:
        return

    gpu.state.blend_set('ALPHA')

    v2d = context.region.view2d
    zoom = _view2d_zoom_factor(v2d)
    
    time_sec = time.time() * settings.get('animation_speed', 1.0)
    connection_color_type = settings.get('connection_color_type', 'CUSTOM')
    overall_opacity = settings.get('overall_opacity', 1.0)
    
    grad_cols = settings.get('gradient_colors', [])
    field_grad_cols = settings.get('field_gradient_colors', [])

    batch_lines_backing = []
    batch_lines_main = []
    batch_node_bbox = []

    batch_circles_backing = []
    batch_circles_start = []
    batch_circles_end = []

    # 处理节点边框
    if nodes_to_outline:
        border_thickness = settings.get('node_border_thickness', 3.0)
        bbox_width = max(1.0, border_thickness * zoom)
        pixel_radius = 4.0 * zoom
        
        for node in nodes_to_outline:
            bbox_poly = get_rounded_rect_path(
                node, 
                v2d, 
                radius=pixel_radius,
                thickness=bbox_width
            )
            if bbox_poly:
                batch_node_bbox.append(bbox_poly)

    socket_index_cache = {}
    curv_factor = get_curving_factor()
    enable_type_colors = settings.get('enable_type_based_colors', False)
    
    # 存储每条连线的信息，用于后续绘制
    link_info_list = []

    for link in links_to_draw:
        fs = getattr(link, "from_socket", None)
        ts = getattr(link, "to_socket", None)
        if not fs or not ts:
            continue
        n1 = link.from_node
        n2 = link.to_node
        from_idx = _get_socket_index_cached(socket_index_cache, n1, fs, True)
        to_idx = _get_socket_index_cached(socket_index_cache, n2, ts, False)
        if from_idx is None or to_idx is None:
            continue
        # 计算位置
        try:
            # get_socket_loc 可能在特殊情况下失败，加个保护
            l1x, l1y = get_socket_loc(n1, True, from_idx)
            l2x, l2y = get_socket_loc(n2, False, to_idx)
        except:
            continue

        # 性能优化：根据缩放级别调整采样点数
        pts = get_native_link_points(link, v2d, curv_factor, zoom)
        if not pts or len(pts) < 2:
            continue
        
        # 性能优化：视口裁剪，跳过不可见的连线
        try:
            region = context.region
            if not _is_link_visible(region, pts, margin=100):
                continue
        except:
            # 如果无法获取 region，继续绘制（向后兼容）
            pass
        
        # 保存连线信息和socket信息
        is_field = is_field_link(tree, link)
        link_info_list.append({
            'pts': pts,
            'from_socket': fs,
            'to_socket': ts,
            'start_pos': (pts[0][0], pts[0][1]),
            'end_pos': (pts[-1][0], pts[-1][1]),
            'is_field': is_field,
            'link': link  # 保存link引用以便后续使用
        })

    # 分离Field和Constant连线
    field_links = []
    constant_links = []
    
    for link_info in link_info_list:
        if link_info.get('is_field', False):
            field_links.append(link_info)
        else:
            constant_links.append(link_info)
    
    width_backing = max(2.0, 9.0 * zoom)
    width_main = max(1.5, settings.get('line_thickness', 2.0) * zoom)

    # 1. Backing (底层背景) - 给所有连线画背景
    all_backing = [info['pts'] for info in link_info_list]
    if all_backing:
        # 从设置中获取底层背景颜色（draw_batch_lines会自动应用overall_opacity）
        backing_color_setting = settings.get('backing_color', (0.0, 0.0, 0.0, 0.55))
        # 确保是RGBA格式的tuple，并确保所有值都是float
        if isinstance(backing_color_setting, (list, tuple)) and len(backing_color_setting) >= 4:
            backing_color = (
                float(backing_color_setting[0]),
                float(backing_color_setting[1]),
                float(backing_color_setting[2]),
                float(backing_color_setting[3])
            )
        else:
            backing_color = (0.0, 0.0, 0.0, 0.55)
        
        # 调试：打印颜色值（可以注释掉）
        # print(f"底层背景颜色: {backing_color}, 整体透明度: {overall_opacity}")
        
        draw_batch_lines(all_backing, 'SMOOTH_COLOR', width_backing, colors=[backing_color], overall_opacity=overall_opacity)

    # 2. Main Lines - Constant连线：实线流动
    if constant_links:
        if enable_type_colors:
            # 性能优化：按socket类型分组，批量绘制相同类型的连线
            links_by_socket_type = {}
            for link_info in constant_links:
                ts = link_info['to_socket']
                socket_type = get_socket_type_name(ts)
                if socket_type not in links_by_socket_type:
                    links_by_socket_type[socket_type] = []
                links_by_socket_type[socket_type].append(link_info)
            
            # 为每种socket类型批量绘制
            for socket_type, type_links in links_by_socket_type.items():
                if not type_links:
                    continue
                # 获取该类型的颜色偏移
                sample_link = type_links[0]
                ts = sample_link['to_socket']
                link_colors = apply_type_based_color_shift(grad_cols, sample_link['from_socket'], ts, offset_strength=0.5)
                # 批量绘制所有相同类型的连线
                type_points = [info['pts'] for info in type_links]
                draw_batch_lines(type_points, 'GRADIENT', width_main, colors=link_colors, time_sec=time_sec, overall_opacity=overall_opacity)
        else:
            # 所有连线使用相同颜色
            constant_main = [info['pts'] for info in constant_links]
            draw_batch_lines(constant_main, 'GRADIENT', width_main, colors=grad_cols, time_sec=time_sec, overall_opacity=overall_opacity)
    
    # 3. Field连线：使用Field配色方案（实线，不再使用虚线）
    if field_links:
        if enable_type_colors:
            # 性能优化：按socket类型分组，批量绘制相同类型的连线
            field_links_by_socket_type = {}
            for field_info in field_links:
                ts = field_info['to_socket']
                socket_type = get_socket_type_name(ts)
                if socket_type not in field_links_by_socket_type:
                    field_links_by_socket_type[socket_type] = []
                field_links_by_socket_type[socket_type].append(field_info)
            
            # 为每种socket类型批量绘制
            for socket_type, type_links in field_links_by_socket_type.items():
                if not type_links:
                    continue
                # 获取该类型的颜色偏移
                sample_link = type_links[0]
                ts = sample_link['to_socket']
                link_colors = apply_type_based_color_shift(field_grad_cols, sample_link['from_socket'], ts, offset_strength=0.5)
                # 批量绘制所有相同类型的连线
                type_points = [info['pts'] for info in type_links]
                draw_batch_lines(type_points, 'GRADIENT', width_main, colors=link_colors, time_sec=time_sec, overall_opacity=overall_opacity)
        else:
            # 所有Field连线使用Field配色方案
            field_main = [info['pts'] for info in field_links]
            draw_batch_lines(field_main, 'GRADIENT', width_main, colors=field_grad_cols, time_sec=time_sec, overall_opacity=overall_opacity)

    # 3. Circles - 背景圆圈（给所有连线）
    all_circles_backing = []
    for info in link_info_list:
        all_circles_backing.append(info['start_pos'])
        all_circles_backing.append(info['end_pos'])
    if all_circles_backing:
        backing_circle_color = (0, 0, 0, 0.55 * overall_opacity)
        draw_batch_circles(all_circles_backing, 7.0 * zoom, backing_circle_color, overall_opacity=overall_opacity)

    # 端点圆圈 - 根据连线类型（Constant或Field）显示不同颜色
    if enable_type_colors:
        # 性能优化：按颜色和大小分组，批量绘制端点圆圈
        circles_by_key = {}  # key: (color_tuple, size) -> list of positions
        for link_info in link_info_list:
            ts = link_info['to_socket']
            sx, sy = link_info['start_pos']
            tx, ty = link_info['end_pos']
            is_field = link_info.get('is_field', False)
            
            # 根据连线类型选择颜色方案
            base_cols = field_grad_cols if is_field else grad_cols
            
            # 起始端点
            socket_color_start = apply_type_based_color_shift([base_cols[0] if base_cols else (1,1,1,1)], None, ts, offset_strength=0.5)[0]
            socket_size_start = get_socket_circle_size(ts, zoom)
            start_key = (socket_color_start, socket_size_start)
            if start_key not in circles_by_key:
                circles_by_key[start_key] = []
            circles_by_key[start_key].append((sx, sy))
            
            # 结束端点
            socket_color_end = apply_type_based_color_shift([base_cols[-1] if base_cols else (1,1,1,1)], None, ts, offset_strength=0.5)[0]
            socket_size_end = get_socket_circle_size(ts, zoom)
            end_key = (socket_color_end, socket_size_end)
            if end_key not in circles_by_key:
                circles_by_key[end_key] = []
            circles_by_key[end_key].append((tx, ty))
        
        # 批量绘制所有相同颜色和大小的圆圈
        for (color, size), positions in circles_by_key.items():
            if positions:
                draw_batch_circles(positions, size, color, overall_opacity=overall_opacity)
    else:
        # 根据连线类型使用不同的颜色方案
        constant_start_positions = [info['start_pos'] for info in constant_links]
        constant_end_positions = [info['end_pos'] for info in constant_links]
        field_start_positions = [info['start_pos'] for info in field_links]
        field_end_positions = [info['end_pos'] for info in field_links]
        
        # Constant连线端点
        if constant_start_positions or constant_end_positions:
            c_start = grad_cols[0] if grad_cols else (1,1,1,1)
            c_end = grad_cols[-1] if grad_cols else c_start
            if constant_start_positions:
                draw_batch_circles(constant_start_positions, 5.0 * zoom, c_start, overall_opacity=overall_opacity)
            if constant_end_positions:
                draw_batch_circles(constant_end_positions, 5.0 * zoom, c_end, overall_opacity=overall_opacity)
        
        # Field连线端点
        if field_start_positions or field_end_positions:
            f_start = field_grad_cols[0] if field_grad_cols else (0.8, 0.2, 1.0, 1.0)
            f_end = field_grad_cols[-1] if field_grad_cols else f_start
            if field_start_positions:
                draw_batch_circles(field_start_positions, 5.0 * zoom, f_start, overall_opacity=overall_opacity)
            if field_end_positions:
                draw_batch_circles(field_end_positions, 5.0 * zoom, f_end, overall_opacity=overall_opacity)

    # 4. Node Borders
    if batch_node_bbox:
        draw_batch_lines(batch_node_bbox, 'GRADIENT', bbox_width, colors=grad_cols, time_sec=time_sec, overall_opacity=overall_opacity)

    gpu.state.blend_set('NONE')
    # 性能优化：根据连线数量动态调整重绘频率
    # 连线数量多时，降低刷新频率以减少GPU负载
    num_links = len(links_to_draw)
    if num_links > 500:
        redraw_interval = 0.2  # 大量连线时，每0.2秒刷新一次
    elif num_links > 200:
        redraw_interval = 0.15  # 中等数量连线
    else:
        redraw_interval = 0.1  # 少量连线，正常刷新频率
    
    if not bpy.app.timers.is_registered(force_redraw):
        bpy.app.timers.register(force_redraw, first_interval=redraw_interval)

def force_redraw():
    try:
        for wm in bpy.data.window_managers:
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'NODE_EDITOR':
                        area.tag_redraw()
    except:
        pass
    return None

def register():
    global draw_handler, _SHADER_CACHE
    # 清除着色器缓存，确保使用最新的着色器代码（包括alpha支持）
    _SHADER_CACHE.clear()
    draw_handler = bpy.types.SpaceNodeEditor.draw_handler_add(
        draw_colorful_connections, (), 'WINDOW', 'POST_PIXEL'
    )

def unregister():
    global draw_handler, _SHADER_CACHE
    if draw_handler:
        bpy.types.SpaceNodeEditor.draw_handler_remove(draw_handler, 'WINDOW')
        draw_handler = None
    # 清除着色器缓存
    _SHADER_CACHE.clear()